"""DNS records: validation, preview/confirm change workflow, provider apply with an integrity guarantee, and verification.

HARD GUARANTEE: a change request targets exactly one record. `apply_change` snapshots the provider zone before and after the single
provider call and, if ANY other record differs, flags the change as an integrity violation (audit + alert). Mail records
(MX/SPF/DKIM/DMARC) additionally need an explicit `protected_ack` even when they are the target.
"""
import hashlib
import ipaddress
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.core.security import decrypt_secret, encrypt_secret
from app.models.ssl import CloudflareConnection, DnsChange, DnsRecord
from app.models.tenant import Tenant
from app.services import cloudflare as cf
from app.services.audit import audit
from app.services.mail import queue_email

RTYPES = ("A", "AAAA", "CNAME", "TXT", "MX", "NS", "CAA", "SRV")
PROXIABLE = {"A", "AAAA", "CNAME"}
_LABEL = re.compile(r"^(\*|[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)$")
_HOSTLABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_CAA = re.compile(r'^(0|128) (issue|issuewild|iodef) "([^"\\]{1,255})"$')
_PRIVATE4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]
_ULA = ipaddress.ip_network("fc00::/7")
_SRV_NAME = re.compile(r"^_[a-z0-9-]{1,30}\._(tcp|udp|tls)\.")


def verr(field: str, message: str) -> HTTPException:
    return HTTPException(422, detail={"code": "validation_error", "message": message, "fields": [{"field": field, "message": message}]})


# ----------------------------------------------------------------------------- validation
def valid_host(value: str, *, allow_root: bool = False) -> str | None:
    v = value.strip().lower().rstrip(".")
    if allow_root and value.strip() == ".":
        return "."
    if not v or len(v) > 253:
        return None
    try:
        ipaddress.ip_address(v)
        return None
    except ValueError:
        pass
    labels = v.split(".")
    if len(labels) < 2 or not all(_HOSTLABEL.match(label) for label in labels) or labels[-1].isdigit():
        return None
    return v


def normalize_name(name: str, domain: str) -> str:
    n = (name or "").strip().lower().rstrip(".")
    if n in ("", "@"):
        return domain
    if n != domain and not n.endswith("." + domain):
        n = f"{n}.{domain}"
    if len(n) > 253:
        raise verr("name", "Record name is too long.")
    labels = n[: -len(domain)].rstrip(".").split(".") if n != domain else []
    for i, label in enumerate(labels):
        if not _LABEL.match(label) or (label == "*" and i != 0):
            raise verr("name", f"Invalid label '{label}' in record name.")
    return n


def validate_record(rtype: str, name: str, content: str, ttl: int, priority: int | None, proxied: bool | None, domain: str) -> dict[str, Any]:
    rtype = (rtype or "").upper()
    if rtype not in RTYPES:
        raise verr("type", f"Unsupported record type. Supported: {', '.join(RTYPES)}.")
    n = normalize_name(name, domain)
    c = (content or "").strip()
    if not c:
        raise verr("content", "Record value is required.")
    if ttl != 1 and not 60 <= ttl <= 86400:
        raise verr("ttl", "TTL must be 1 (automatic) or between 60 and 86400 seconds.")
    if proxied and rtype not in PROXIABLE:
        raise verr("proxied", f"{rtype} records cannot be proxied.")
    apex = n == domain
    if rtype == "A":
        try:
            ip = ipaddress.IPv4Address(c)
        except ValueError:
            raise verr("content", "A record value must be an IPv4 address.") from None
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved or any(ip in n for n in _PRIVATE4):
            raise verr("content", "A record must be a public IPv4 address.")
    elif rtype == "AAAA":
        try:
            ip6 = ipaddress.IPv6Address(c)
        except ValueError:
            raise verr("content", "AAAA record value must be an IPv6 address.") from None
        if ip6.is_loopback or ip6.is_unspecified or ip6.is_multicast or ip6.is_link_local or ip6 in _ULA:
            raise verr("content", "AAAA record must be a public IPv6 address.")
        c = str(ip6)
    elif rtype == "CNAME":
        h = valid_host(c)
        if h is None:
            raise verr("content", "CNAME target must be a hostname.")
        if apex:
            raise verr("name", "A CNAME cannot be set at the zone apex.")
        if h == n:
            raise verr("content", "A CNAME cannot point to itself.")
        c = h
    elif rtype == "MX":
        if priority is None or not 0 <= priority <= 65535:
            raise verr("priority", "MX priority (0-65535) is required.")
        h = valid_host(c)
        if h is None:
            raise verr("content", "MX target must be a hostname (not an IP address).")
        c = h
    elif rtype == "NS":
        h = valid_host(c)
        if h is None:
            raise verr("content", "NS target must be a hostname.")
        if apex:
            raise verr("name", "Apex NS records are managed at the registrar and cannot be changed here.")
        c = h
    elif rtype == "TXT":
        if len(c) > 2048 or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", c):
            raise verr("content", "TXT value must be at most 2048 printable characters.")
        low = c.lower()
        if low.startswith("v=spf1") and not re.search(r"(\s[-~?+]?all|\sredirect=\S+)\s*$", low):
            raise verr("content", "SPF record must end with an 'all' mechanism or a redirect.")
        if n.startswith("_dmarc.") and not low.startswith("v=dmarc1"):
            raise verr("content", "A _dmarc TXT record must start with v=DMARC1.")
    elif rtype == "CAA":
        m = _CAA.match(c)
        if not m:
            raise verr("content", 'CAA value must look like: 0 issue "letsencrypt.org".')
        if m.group(2) == "iodef" and not re.match(r"^(mailto:|https://)", m.group(3)):
            raise verr("content", "CAA iodef must be a mailto: or https:// URL.")
    elif rtype == "SRV":
        if not _SRV_NAME.match(n):
            raise verr("name", "SRV name must look like _service._tcp.example.com.")
        if priority is None or not 0 <= priority <= 65535:
            raise verr("priority", "SRV priority (0-65535) is required.")
        parts = c.split()
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or not (0 <= int(parts[0]) <= 65535 and 1 <= int(parts[1]) <= 65535):
            raise verr("content", "SRV value must be: weight port target (e.g. 10 5060 sip.example.com).")
        t = valid_host(parts[2], allow_root=True)
        if t is None:
            raise verr("content", "SRV target must be a hostname or '.'.")
        c = f"{int(parts[0])} {int(parts[1])} {t}"
    return {"type": rtype, "name": n, "content": c, "ttl": 1 if proxied else ttl, "priority": priority if rtype in ("MX", "SRV") else None, "proxied": bool(proxied) if rtype in PROXIABLE else None}


def mail_critical(rec: dict, domain: str) -> str | None:
    t, n, c = rec["type"], rec["name"], rec["content"].lower()
    if t == "MX":
        return "MX (mail routing)"
    if t == "TXT" and c.startswith("v=spf1"):
        return "SPF"
    if t == "TXT" and n.startswith("_dmarc."):
        return "DMARC"
    if "._domainkey." in n or n.startswith("_domainkey."):
        return "DKIM"
    return None


# ----------------------------------------------------------------------------- mirror helpers
FIELDS = ("type", "name", "content", "ttl", "priority", "proxied")


def row_dict(r: DnsRecord) -> dict[str, Any]:
    return {"type": r.rtype, "name": r.name, "content": r.content, "ttl": r.ttl, "priority": r.priority, "proxied": r.proxied}


def row_view(r: DnsRecord) -> dict[str, Any]:
    return {"id": str(r.id), **row_dict(r), "provider": r.provider, "synced_at": r.synced_at}


def from_cf(rec: dict, domain: str) -> dict[str, Any] | None:
    t = rec.get("type")
    if t not in RTYPES:
        return None
    c = str(rec.get("content", ""))
    if t == "TXT" and len(c) >= 2 and c[0] == c[-1] == '"':
        c = c[1:-1]
    if t in ("CNAME", "MX", "NS"):
        c = c.rstrip(".").lower()
    if t == "SRV" and isinstance(rec.get("data"), dict) and not c:
        d = rec["data"]
        c = f"{d.get('weight')} {d.get('port')} {d.get('target')}"
    return {"type": t, "name": str(rec.get("name", "")).lower(), "content": c, "ttl": int(rec.get("ttl", 1)), "priority": rec.get("priority") if t in ("MX", "SRV") else None, "proxied": rec.get("proxied") if t in PROXIABLE else None}


def to_cf(rec: dict) -> dict[str, Any]:
    p: dict[str, Any] = {"type": rec["type"], "name": rec["name"], "ttl": rec["ttl"]}
    if rec["type"] == "SRV":
        w, port, target = rec["content"].split()
        p["data"] = {"priority": rec["priority"], "weight": int(w), "port": int(port), "target": target}
    elif rec["type"] == "CAA":
        flags, tag, val = _CAA.match(rec["content"]).groups()[0:3]  # type: ignore[union-attr]
        p["data"] = {"flags": int(flags), "tag": tag, "value": val}
    else:
        p["content"] = rec["content"]
        if rec["type"] == "MX":
            p["priority"] = rec["priority"]
    if rec["type"] in PROXIABLE:
        p["proxied"] = bool(rec.get("proxied"))
    return p


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in FIELDS)


# ----------------------------------------------------------------------------- connection / zones
def get_connection(db: Session, tenant_id: uuid.UUID) -> CloudflareConnection | None:
    return db.scalar(select(CloudflareConnection).where(CloudflareConnection.tenant_id == tenant_id))


def zone_for(conn: CloudflareConnection | None, domain: str) -> dict | None:
    best = None
    for z in (conn.zones or []) if conn else []:
        if domain == z["name"] or domain.endswith("." + z["name"]):
            if best is None or len(z["name"]) > len(best["name"]):
                best = z
    return best


def client_for(conn: CloudflareConnection):
    token = decrypt_secret(conn.token_enc)
    if not token:
        raise api_error(409, "not_configured", "Stored Cloudflare token could not be decrypted; reconnect Cloudflare.")
    return cf.client_factory(token)


def cf_error(e: cf.CloudflareError) -> HTTPException:
    if isinstance(e, cf.CloudflareAuthError):
        return api_error(403 if e.code == "permission_denied" else 422, e.code, f"Cloudflare rejected the token: {e.message}")
    if isinstance(e, cf.CloudflareRateLimited):
        return HTTPException(429, detail={"code": "rate_limited", "message": e.message, "retry_after": e.retry_after}, headers={"Retry-After": str(int(e.retry_after or 1))})
    return api_error(502, e.code, f"Cloudflare error: {e.message}")


def connect(db: Session, tenant_id: uuid.UUID, token: str, label: str | None, actor: uuid.UUID | None) -> CloudflareConnection:
    token = token.strip()
    if re.fullmatch(r"[0-9a-f]{37}", token):
        raise verr("token", "Global API keys are not accepted. Create a scoped API token (Zone:Read, DNS:Read, and DNS:Edit only if needed).")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        raise verr("token", "That does not look like a Cloudflare API token.")
    client = cf.client_factory(token)
    try:
        client.verify_token()
        zones = client.list_zones()
    except cf.CloudflareError as e:
        audit(db, "cloudflare.connect_failed", actor=actor, tenant_id=tenant_id, target_type="cloudflare", detail={"reason": e.code})
        db.commit()
        raise cf_error(e) from None
    finally:
        client.close()
    conn = get_connection(db, tenant_id) or CloudflareConnection(tenant_id=tenant_id, token_enc="", token_fingerprint="")
    conn.token_enc, conn.token_fingerprint = encrypt_secret(token), hashlib.sha256(token.encode()).hexdigest()[:8]
    conn.label, conn.status, conn.zones, conn.last_error, conn.created_by = label, "connected", zones, None, actor
    conn.last_checked_at = datetime.now(UTC)
    db.add(conn)
    audit(db, "cloudflare.connected", actor=actor, tenant_id=tenant_id, target_type="cloudflare", detail={"zones": [z["name"] for z in zones]})
    return conn


def connection_view(conn: CloudflareConnection | None) -> dict[str, Any]:
    if conn is None:
        return {"status": "not_configured", "configured": False, "zones": [], "required_permissions": ["Zone:Read", "DNS:Read", "DNS:Edit (only to apply changes)"]}
    return {"status": conn.status, "configured": True, "label": conn.label, "fingerprint": conn.token_fingerprint, "zones": conn.zones or [], "last_checked_at": conn.last_checked_at, "last_error": conn.last_error, "required_permissions": ["Zone:Read", "DNS:Read", "DNS:Edit (only to apply changes)"]}


def recheck(db: Session, conn: CloudflareConnection, actor: uuid.UUID | None) -> CloudflareConnection:
    client = client_for(conn)
    try:
        client.verify_token()
        conn.zones = client.list_zones()
        conn.status, conn.last_error = "connected", None
    except cf.CloudflareError as e:
        conn.status = {"permission_denied": "permission_denied", "invalid_token": "invalid", "rate_limited": "rate_limited"}.get(e.code, "invalid")
        conn.last_error = e.message[:300]
        audit(db, "cloudflare.check_failed", actor=actor, tenant_id=conn.tenant_id, target_type="cloudflare", detail={"reason": e.code})
        db.commit()
        raise cf_error(e) from None
    finally:
        client.close()
        conn.last_checked_at = datetime.now(UTC)
    return conn


# ----------------------------------------------------------------------------- sync
def sync_zone(db: Session, tenant_id: uuid.UUID, domain: str, actor: uuid.UUID | None = None) -> list[DnsRecord]:
    """Read-only against the provider: refresh the local mirror of records at/below `domain`."""
    conn = get_connection(db, tenant_id)
    if conn is None:
        raise api_error(409, "not_configured", "Cloudflare is not connected for this account.")
    zone = zone_for(conn, domain)
    if zone is None:
        raise api_error(409, "zone_not_authorised", "The connected Cloudflare token does not cover this domain.")
    client = client_for(conn)
    try:
        remote = client.list_records(zone["id"])
    except cf.CloudflareError as e:
        conn.last_error = e.message[:300]
        db.commit()
        raise cf_error(e) from None
    finally:
        client.close()
    return _store_mirror(db, tenant_id, domain, zone["id"], remote)


def _in_domain(name: str, domain: str) -> bool:
    return name == domain or name.endswith("." + domain)


def _store_mirror(db: Session, tenant_id: uuid.UUID, domain: str, zone_id: str, remote: list[dict]) -> list[DnsRecord]:
    now = datetime.now(UTC)
    existing = {r.provider_record_id: r for r in db.scalars(select(DnsRecord).where(DnsRecord.tenant_id == tenant_id, DnsRecord.domain == domain))}
    seen, out = set(), []
    for rec in remote:
        d = from_cf(rec, domain)
        if d is None or not _in_domain(d["name"], domain):
            continue
        pid = str(rec["id"])
        seen.add(pid)
        row = existing.get(pid) or DnsRecord(tenant_id=tenant_id, domain=domain, provider="cloudflare", provider_record_id=pid)
        row.zone_id, row.rtype, row.name, row.content, row.ttl, row.priority, row.proxied, row.synced_at = zone_id, d["type"], d["name"], d["content"], d["ttl"], d["priority"], d["proxied"], now
        db.add(row)
        out.append(row)
    for pid, row in existing.items():
        if pid not in seen:
            db.delete(row)
    db.flush()
    return out


# ----------------------------------------------------------------------------- change workflow
def _rollback_text(action: str, before: dict | None, after: dict | None, domain: str) -> str:
    fmt = lambda r: f"{r['type']} {r['name']} -> {r['content']}" + (f" (priority {r['priority']})" if r.get("priority") is not None else "") + f" TTL {r['ttl']}"
    if action == "create":
        return f"To undo: delete the new record ({fmt(after)}). In the panel use 'Rollback' on this change, which prepares a delete you must confirm. Manual route: Cloudflare dashboard > DNS > Records > delete that single record."
    if action == "update":
        return f"To undo: set the record back to its previous values ({fmt(before)}). In the panel use 'Rollback' on this change. Manual route: Cloudflare dashboard > DNS > edit the record and restore the old value."
    return f"To undo: re-create the deleted record ({fmt(before)}). In the panel use 'Rollback' on this change. Manual route: Cloudflare dashboard > DNS > Add record with the values above."


def build_preview(db: Session, *, actor: uuid.UUID | None, tenant_id: uuid.UUID, domain: str, action: str, record_id: uuid.UUID | None, record: dict | None, protected_ack: bool) -> DnsChange:
    rows = list(db.scalars(select(DnsRecord).where(DnsRecord.tenant_id == tenant_id, DnsRecord.domain == domain)))
    target = None
    if action in ("update", "delete"):
        target = next((r for r in rows if r.id == record_id), None)
        if target is None:
            raise api_error(404, "not_found", "DNS record not found. Sync records first.")
    before = row_dict(target) if target else None
    after = None
    warnings: list[str] = []
    if action in ("create", "update"):
        if record is None:
            raise verr("record", "A record is required for create/update.")
        after = validate_record(record["type"], record["name"], record["content"], record.get("ttl", 1), record.get("priority"), record.get("proxied"), domain)
        if target is not None and after["type"] != target.rtype:
            raise verr("type", "Record type cannot be changed; delete and create instead.")
        others = [r for r in rows if r.id != (target.id if target else None)]
        same_name = [r for r in others if r.name == after["name"]]
        if after["type"] == "CNAME" and same_name:
            raise verr("name", "A CNAME cannot coexist with other records of the same name.")
        if after["type"] != "CNAME" and any(r.rtype == "CNAME" for r in same_name):
            raise verr("name", "A CNAME already exists at this name; it cannot coexist with other records.")
        if any(_same(row_dict(r), after) for r in others):
            raise verr("content", "An identical record already exists.")
        if after["type"] == "TXT" and after["content"].lower().startswith("v=spf1") and any(r.rtype == "TXT" and r.content.lower().startswith("v=spf1") for r in others if r.name == after["name"]):
            raise verr("content", "A domain may have only one SPF record; edit the existing one instead.")
        if after["type"] == "TXT" and after["name"].startswith("_dmarc.") and any(r.rtype == "TXT" and r.name == after["name"] for r in others):
            raise verr("content", "A domain may have only one DMARC record; edit the existing one instead.")
    elif target is not None and target.rtype == "NS" and target.name == domain:
        raise verr("record_id", "Apex NS records are managed at the registrar and cannot be deleted here.")
    crit = mail_critical(after or before, domain) if (after or before) else None
    if crit and not protected_ack:
        raise HTTPException(409, detail={"code": "protected_record", "message": f"This is a {crit} record. Changing it can break email delivery; resubmit with protected_ack=true after reviewing the preview."})
    if crit:
        warnings.append(f"{crit} record: changes can affect email delivery.")
    if after and after.get("proxied"):
        warnings.append("Proxied through Cloudflare: public DNS will show Cloudflare addresses, not the origin value.")
    if action == "delete":
        warnings.append("Deleting a record can take a site or service offline.")
    diff = [{"field": f, "before": (before or {}).get(f), "after": (after or {}).get(f)} for f in FIELDS if (before or {}).get(f) != (after or {}).get(f)]
    ch = DnsChange(tenant_id=tenant_id, domain=domain, action=action, target_record_id=target.id if target else None, before=before, after=after, diff=diff,
                   warnings=warnings, status="previewed", production=True, protected_ack=protected_ack, rollback_instructions=_rollback_text(action, before, after, domain), requested_by=actor)
    ch.diff = diff
    db.add(ch)
    db.flush()
    audit(db, "dns.change_previewed", actor=actor, tenant_id=tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": domain, "action": action, "before": before, "after": after})
    return ch


def change_view(ch: DnsChange, db: Session | None = None) -> dict[str, Any]:
    unchanged = None
    if db is not None:
        unchanged = db.scalar(select(func.count()).select_from(DnsRecord).where(DnsRecord.tenant_id == ch.tenant_id, DnsRecord.domain == ch.domain, DnsRecord.id != ch.target_record_id))
    return {"id": str(ch.id), "domain": ch.domain, "action": ch.action, "target_record_id": str(ch.target_record_id) if ch.target_record_id else None, "before": ch.before, "after": ch.after,
            "diff": ch.diff, "warnings": ch.warnings or [], "status": ch.status, "production": ch.production, "rollback_instructions": ch.rollback_instructions,
            "rollback_of": str(ch.rollback_of) if ch.rollback_of else None, "verification": ch.verification, "verification_detail": ch.verification_detail, "error": ch.error,
            "requested_by": str(ch.requested_by) if ch.requested_by else None, "confirmed_by": str(ch.confirmed_by) if ch.confirmed_by else None, "applied_at": ch.applied_at,
            "created_at": ch.created_at, "unchanged_record_count": unchanged, "other_records_modified": 0 if ch.status != "violation" else None}


def _alert(db: Session, tenant_id: uuid.UUID, subject: str, body: str, kind: str) -> None:
    t = db.get(Tenant, tenant_id)
    if t and t.contact_email:
        queue_email(db, t.contact_email, subject, body, kind, tenant_id)


def apply_change(db: Session, ch: DnsChange, actor: uuid.UUID | None) -> DnsChange:
    """Apply one confirmed change through the provider, then prove nothing else moved."""
    if ch.status != "previewed":
        raise api_error(409, "invalid_state", f"Change is {ch.status}; only previewed changes can be applied.")
    conn = get_connection(db, ch.tenant_id)
    zone = zone_for(conn, ch.domain)
    if conn is None or zone is None:
        raise api_error(409, "not_configured", "Cloudflare is not connected for this domain; no DNS change was made. Apply the change manually using the preview.")
    client = client_for(conn)
    target = db.get(DnsRecord, ch.target_record_id) if ch.target_record_id else None
    try:
        pre = client.list_records(zone["id"])
        pre_map = {str(r["id"]): r for r in pre}
        if target is not None:
            cur = pre_map.get(target.provider_record_id or "")
            cur_d = from_cf(cur, ch.domain) if cur else None
            if cur_d is None or not _same(cur_d, ch.before or {}):
                raise api_error(409, "stale_preview", "The record changed at the provider since this preview. Sync and create a new preview.")
        new_id = None
        if ch.action == "create":
            new_id = str(client.create_record(zone["id"], to_cf(ch.after))["id"])
        elif ch.action == "update":
            client.update_record(zone["id"], target.provider_record_id, to_cf(ch.after))
        else:
            client.delete_record(zone["id"], target.provider_record_id)
        post = client.list_records(zone["id"])
    except cf.CloudflareError as e:
        ch.status, ch.error = "failed", e.message[:300]
        audit(db, "dns.change_failed", actor=actor, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain, "reason": e.code})
        db.commit()
        raise cf_error(e) from None
    finally:
        client.close()
    excl = {target.provider_record_id} if target else set()
    if new_id:
        excl.add(new_id)
    pre_s = {k: v for k, v in _snap(pre, ch.domain).items() if k not in excl}
    post_s = {k: v for k, v in _snap(post, ch.domain).items() if k not in excl}
    _store_mirror(db, ch.tenant_id, ch.domain, zone["id"], post)
    ch.confirmed_by, ch.applied_at = actor, datetime.now(UTC)
    if pre_s != post_s:
        ch.status, ch.error = "violation", "Unrelated DNS records differ after the change."
        audit(db, "dns.integrity_violation", actor=actor, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain, "changed_ids": sorted(k for k in set(pre_s) | set(post_s) if pre_s.get(k) != post_s.get(k))})
        _alert(db, ch.tenant_id, f"DNS integrity warning for {ch.domain}", "Records other than the one you changed differ after a DNS change. Review the zone immediately.", "dns_integrity")
        return ch
    ch.status, ch.error = "applied", None
    ch.verification = "pending"
    audit(db, "dns.change_applied", actor=actor, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain, "action": ch.action, "before": ch.before, "after": ch.after, "production": True})
    return ch


def _snap(records: list[dict], domain: str) -> dict[str, Any]:
    out = {}
    for r in records:
        d = from_cf(r, domain)
        out[str(r["id"])] = d if d is not None else {"other": r.get("type"), "name": r.get("name"), "content": r.get("content")}
    return out


def rollback_change(db: Session, ch: DnsChange, actor: uuid.UUID | None) -> DnsChange:
    """Prepare (never apply) the inverse change as a new preview."""
    if ch.status != "applied":
        raise api_error(409, "invalid_state", "Only applied changes can be rolled back.")
    if ch.action == "create":
        row = db.scalar(select(DnsRecord).where(DnsRecord.tenant_id == ch.tenant_id, DnsRecord.domain == ch.domain, DnsRecord.rtype == ch.after["type"], DnsRecord.name == ch.after["name"], DnsRecord.content == ch.after["content"]))
        if row is None:
            raise api_error(409, "not_found", "The created record no longer exists; sync records first.")
        inv = build_preview(db, actor=actor, tenant_id=ch.tenant_id, domain=ch.domain, action="delete", record_id=row.id, record=None, protected_ack=ch.protected_ack)
    elif ch.action == "update":
        row = db.scalar(select(DnsRecord).where(DnsRecord.tenant_id == ch.tenant_id, DnsRecord.domain == ch.domain, DnsRecord.rtype == ch.after["type"], DnsRecord.name == ch.after["name"], DnsRecord.content == ch.after["content"]))
        if row is None:
            raise api_error(409, "not_found", "The record no longer matches this change; sync records first.")
        inv = build_preview(db, actor=actor, tenant_id=ch.tenant_id, domain=ch.domain, action="update", record_id=row.id, record=ch.before, protected_ack=ch.protected_ack)
    else:
        inv = build_preview(db, actor=actor, tenant_id=ch.tenant_id, domain=ch.domain, action="create", record_id=None, record=ch.before, protected_ack=ch.protected_ack)
    inv.rollback_of = ch.id
    audit(db, "dns.rollback_prepared", actor=actor, tenant_id=ch.tenant_id, target_type="dns_change", target_id=inv.id, detail={"domain": ch.domain, "rollback_of": str(ch.id)})
    return inv


# ----------------------------------------------------------------------------- verification (pluggable resolver)
class Resolver(Protocol):
    def lookup(self, name: str, rtype: str) -> list[str]: ...


class DnsPythonResolver:
    def lookup(self, name: str, rtype: str) -> list[str]:
        import dns.exception
        import dns.resolver

        r = dns.resolver.Resolver()
        r.lifetime = r.timeout = 3.0
        try:
            ans = r.resolve(name, rtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as e:
            raise RuntimeError(type(e).__name__) from None
        out = []
        for a in ans:
            s = a.to_text()
            if rtype == "TXT":
                s = "".join(p.decode() for p in a.strings)
            elif rtype in ("CNAME", "NS", "MX"):
                s = s.rstrip(".").lower() if rtype != "MX" else f"{a.preference} {a.exchange.to_text().rstrip('.').lower()}"
            out.append(s)
        return out


resolver_factory = lambda: DnsPythonResolver()  # noqa: E731  tests replace this


def _matches(rec: dict, values: list[str]) -> bool:
    exp = rec["content"]
    if rec["type"] == "MX":
        exp = f"{rec['priority']} {exp}"
    vals = [v.strip().strip('"') for v in values]
    if rec["type"] in ("AAAA",):
        norm = lambda s: str(ipaddress.ip_address(s)) if s else s
        try:
            return norm(exp) in {norm(v) for v in vals}
        except ValueError:
            return False
    if rec["type"] == "SRV":
        return any(v.rstrip(".").lower().endswith(exp.lower().split(" ", 2)[2]) and exp.split()[1] in v for v in vals)
    return exp.lower().rstrip(".") in {v.lower().rstrip(".") for v in vals}


def verify_change(db: Session, ch: DnsChange, actor: uuid.UUID | None) -> DnsChange:
    if ch.status != "applied":
        raise api_error(409, "invalid_state", "Only applied changes can be verified.")
    rec = ch.after or ch.before
    detail: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "name": rec["name"], "type": rec["type"]}
    if rec.get("proxied") and ch.action != "delete":
        # Public resolution shows Cloudflare edge addresses; confirm via the provider API instead.
        conn = get_connection(db, ch.tenant_id)
        zone = zone_for(conn, ch.domain)
        found = False
        if conn and zone:
            client = client_for(conn)
            try:
                found = any((d := from_cf(r, ch.domain)) and _same(d, ch.after) for r in client.list_records(zone["id"]))
            except cf.CloudflareError as e:
                raise cf_error(e) from None
            finally:
                client.close()
        ch.verification, detail["via"] = ("propagated" if found else "mismatch"), "provider_api (proxied record)"
    else:
        try:
            values = resolver_factory().lookup(rec["name"], rec["type"])
        except RuntimeError as e:
            ch.verification, detail["error"] = "pending", str(e)
            values = None
        if values is not None:
            detail["observed"] = values[:10]
            present = _matches(rec, values)
            ok = (not present) if ch.action == "delete" else present
            ch.verification = "propagated" if ok else "pending"
    ch.verification_detail = detail
    audit(db, "dns.change_verified", actor=actor, tenant_id=ch.tenant_id, target_type="dns_change", target_id=ch.id, detail={"domain": ch.domain, "result": ch.verification})
    return ch
