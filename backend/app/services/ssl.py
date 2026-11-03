"""Certificates: prerequisite checks, ACME requests through the agent (certbot), renewal sweep, expiry data, failure alerts.

Only metadata is stored. Staging is the default ACME environment; production requires an explicit flag AND ssl:manage.
Renewal design: `sweep_renewals()` is idempotent and is meant to be called by a periodic task (cron / systemd timer / the
automation module) roughly every 6 hours. It enqueues `ssl.renew` for issued certificates within the renewal window, applies
exponential back-off after failures, marks lapsed certificates `expired`, and alerts when attempts are exhausted.
"""
import ipaddress
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.identity import User
from app.models.infra import AgentJob
from app.models.ssl import Certificate
from app.models.tenant import Tenant
from app.ops.ssl import HOST_RE
from app.services import dns as dnsmod
from app.services.audit import audit
from app.services.jobs import default_server, enqueue_job
from app.services.mail import queue_email
from app.services.ownership import domain_owner_for, get_ref, owns_resource

RENEW_WINDOW_DAYS = 30
MAX_RENEW_FAILURES = 5
_HOST = re.compile(HOST_RE)


def normalize_domain(raw: str) -> str:
    d = (raw or "").strip().lower().rstrip(".")
    if not d or len(d) > 253 or "*" in d or "/" in d or " " in d:
        raise dnsmod.verr("domain", "Enter a valid domain name such as example.com (wildcards are not supported with HTTP validation).")
    try:
        ipaddress.ip_address(d)
        raise dnsmod.verr("domain", "IP addresses cannot have certificates here; use a domain name.")
    except ValueError:
        pass
    try:
        d = d.encode("idna").decode()
    except UnicodeError:
        raise dnsmod.verr("domain", "Domain name is not valid.") from None
    if not _HOST.match(d):
        raise dnsmod.verr("domain", "Enter a valid domain name such as example.com.")
    return d


def _now() -> datetime:
    return datetime.now(UTC)


def _contact_email(db: Session, tenant_id: uuid.UUID) -> str | None:
    t = db.get(Tenant, tenant_id)
    if t is None:
        return None
    if t.contact_email:
        return t.contact_email
    if t.owner_user_id:
        u = db.get(User, t.owner_user_id)
        return u.email if u else None
    return None


def check_prerequisites(db: Session, tenant_id: uuid.UUID, domain: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, msg: str):
        checks.append({"check": name, "ok": ok, "message": msg})

    add("domain_valid", True, "Domain name is valid.")
    owned = owns_resource(db, tenant_id, "domain", domain) or domain_owner_for(db, tenant_id, domain) is not None
    add("ownership", owned, "Domain belongs to this account." if owned else "Domain is not registered to this account.")
    ref = get_ref(db, tenant_id, "domain", domain)
    webroot = ref.root_rel if ref else None
    add("webroot", bool(webroot), "Site webroot is registered for challenge files." if webroot else "No webroot is registered for this domain yet (website/domain provisioning must record it).")
    server_id = (ref.server_id if ref and ref.server_id else None) or (default_server(db).id if default_server(db) else None)
    add("server", server_id is not None, "A server is available." if server_id else "No registered server is available to run the ACME client.")
    email = _contact_email(db, tenant_id)
    add("contact_email", bool(email), "ACME account email is available." if email else "The account has no contact email for the certificate authority.")
    dns_ok, msg = False, ""
    try:
        res = dnsmod.resolver_factory()
        addrs = res.lookup(domain, "A") + res.lookup(domain, "AAAA")
        expected = set((ref.attrs or {}).get("server_ips", [])) if ref else set()
        if not addrs:
            msg = "The domain does not resolve yet; point its A/AAAA record at this server first."
        elif expected and not (expected & set(addrs)):
            msg = f"The domain resolves to {', '.join(addrs[:3])}, not to this server."
        else:
            dns_ok, msg = True, "Domain resolves."
    except Exception:  # noqa: BLE001 - resolver failures are reported, not raised
        msg = "DNS lookup failed; try again shortly."
    add("dns_resolves", dns_ok, msg)
    return {"domain": domain, "ok": all(c["ok"] for c in checks), "checks": checks, "_server_id": server_id, "_webroot": webroot, "_email": email}


def public_prereq(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def cert_view(c: Certificate, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    days = int((c.not_after - now).total_seconds() // 86400) if c.not_after else None
    status = "expired" if c.status == "issued" and c.not_after and c.not_after < now else c.status
    sev = "ok"
    if status in ("expired", "failed"):
        sev = "critical"
    elif days is not None and days <= 7:
        sev = "critical"
    elif days is not None and days <= 14:
        sev = "warning"
    elif days is not None and days <= RENEW_WINDOW_DAYS:
        sev = "notice"
    return {"id": str(c.id), "domain": c.domain, "status": status, "environment": c.environment, "trusted": c.environment == "production", "issuer": c.issuer,
            "not_before": c.not_before, "not_after": c.not_after, "days_remaining": days, "severity": sev, "serial": c.serial, "fingerprint_sha256": c.fingerprint_sha256,
            "sans": c.sans or [], "auto_renew": c.auto_renew, "last_error": c.last_error, "last_attempt_at": c.last_attempt_at, "renewal_failures": c.renewal_failures,
            "last_renewed_at": c.last_renewed_at, "created_at": c.created_at, "tenant_id": str(c.tenant_id)}


def _enqueue(db: Session, c: Certificate, op: str, params: dict, actor: uuid.UUID | None, server_id: uuid.UUID | None) -> None:
    if server_id is None:
        raise api_error(409, "not_configured", "No registered server is available.")
    job = enqueue_job(db, server_id, op, params, tenant_id=c.tenant_id, requested_by=actor, correlation_id=f"cert:{c.id}")
    c.job_id, c.server_id, c.last_attempt_at = job.id, server_id, _now()


def _env_params(c: Certificate) -> dict:
    p: dict[str, Any] = {"domain": c.domain, "environment": c.environment}
    if c.environment == "production":
        p["confirm_production"] = True
    return p


def request_certificate(db: Session, *, actor: uuid.UUID | None, tenant_id: uuid.UUID, domain: str, production: bool, can_manage: bool) -> Certificate:
    domain = normalize_domain(domain)
    if production and not can_manage:
        raise api_error(403, "forbidden", "Production certificates require ssl:manage permission.")
    prereq = check_prerequisites(db, tenant_id, domain)
    if not prereq["checks"][1]["ok"]:
        raise api_error(404, "not_found", "Domain not found.")
    if not prereq["ok"]:
        raise HTTPException(409, detail={"code": "prerequisites_failed", "message": "Domain prerequisites are not met.", "checks": public_prereq(prereq)["checks"]})
    env = "production" if production else "staging"
    c = db.scalar(select(Certificate).where(Certificate.tenant_id == tenant_id, Certificate.domain == domain, Certificate.environment == env))
    if c is not None:
        sync_certificate(db, c)
        if c.status in ("pending", "renewing"):
            raise api_error(409, "already_in_progress", "A certificate request for this domain is already in progress.")
        if c.status == "issued" and c.not_after and c.not_after > _now():
            raise api_error(409, "already_issued", "A valid certificate already exists; use renew instead.")
    else:
        c = Certificate(tenant_id=tenant_id, domain=domain, environment=env)
        db.add(c)
    c.status, c.last_error, c.requested_by = "pending", None, actor
    db.flush()
    _enqueue(db, c, "ssl.request_certificate", {**_env_params(c), "email": prereq["_email"], "webroot": prereq["_webroot"]}, actor, prereq["_server_id"])
    audit(db, "ssl.requested", actor=actor, tenant_id=tenant_id, target_type="certificate", target_id=c.id, detail={"domain": domain, "environment": env})
    return c


def renew_certificate(db: Session, c: Certificate, actor: uuid.UUID | None, *, reason: str = "manual") -> Certificate:
    sync_certificate(db, c)
    if c.status in ("pending", "renewing"):
        raise api_error(409, "already_in_progress", "An operation is already in progress for this certificate.")
    if c.status not in ("issued", "expired", "failed"):
        raise api_error(409, "invalid_state", "Certificate cannot be renewed in its current state.")
    previous = c.status
    c.status = "renewing" if c.not_before else "pending"
    _enqueue(db, c, "ssl.renew" if c.not_before else "ssl.request_certificate",
             _env_params(c) if c.not_before else {**_env_params(c), "email": _contact_email(db, c.tenant_id) or "", "webroot": (get_ref(db, c.tenant_id, "domain", c.domain).root_rel if get_ref(db, c.tenant_id, "domain", c.domain) else "")},
             actor, c.server_id or (default_server(db).id if default_server(db) else None))
    audit(db, "ssl.renewal_started", actor=actor, tenant_id=c.tenant_id, target_type="certificate", target_id=c.id, detail={"domain": c.domain, "reason": reason, "previous_status": previous})
    return c


def _fail(db: Session, c: Certificate, job: AgentJob, was: str) -> None:
    msg = (job.error or (job.result or {}).get("message") or f"job {job.status}").strip()[:500]
    c.last_error = msg
    c.renewal_failures = (c.renewal_failures or 0) + 1 if was == "renewing" else c.renewal_failures
    if was == "renewing":
        c.status = "issued" if c.not_after and c.not_after > _now() else "expired"
    else:
        c.status = "failed"
    audit(db, "ssl.renewal_failed" if was == "renewing" else "ssl.request_failed", tenant_id=c.tenant_id, target_type="certificate", target_id=c.id,
          detail={"domain": c.domain, "error": msg, "failures": c.renewal_failures, "environment": c.environment})
    to = _contact_email(db, c.tenant_id)
    if to:
        queue_email(db, to, f"Certificate {'renewal' if was == 'renewing' else 'request'} failed for {c.domain}",
                    f"We could not {'renew' if was == 'renewing' else 'issue'} the SSL certificate for {c.domain}.\nReason: {msg}\nYour site keeps serving its current certificate until it expires. Check your DNS and contact support if this continues.",
                    "ssl_failure", c.tenant_id)


def sync_certificate(db: Session, c: Certificate) -> Certificate:
    """Fold a finished agent job into the certificate row (idempotent; no-op while the job is still running)."""
    if c.status not in ("pending", "renewing") or not c.job_id:
        return c
    job = db.get(AgentJob, c.job_id)
    if job is None or job.status in ("queued", "running"):
        return c
    was = c.status
    res = job.result or {}
    if job.status == "succeeded" and res.get("ok", True) is not False and res.get("not_after"):
        c.status, c.last_error = "issued", None
        c.issuer = str(res.get("issuer", ""))[:200]
        c.not_before, c.not_after = datetime.fromisoformat(res["not_before"]), datetime.fromisoformat(res["not_after"])
        c.serial, c.fingerprint_sha256, c.sans = res.get("serial"), res.get("fingerprint_sha256"), res.get("sans") or []
        c.renewal_failures, c.last_renewed_at = 0, _now()
        audit(db, "ssl.issued" if was == "pending" else "ssl.renewed", tenant_id=c.tenant_id, target_type="certificate", target_id=c.id, detail={"domain": c.domain, "environment": c.environment, "not_after": res["not_after"]})
    else:
        _fail(db, c, job, was)
    return c


def sweep_renewals(db: Session, now: datetime | None = None, window_days: int = RENEW_WINDOW_DAYS) -> dict[str, Any]:
    """Periodic task body. Returns a summary; safe to run repeatedly."""
    now = now or _now()
    out: dict[str, Any] = {"synced": 0, "renewals_enqueued": [], "marked_expired": [], "gave_up": [], "skipped_backoff": []}
    for c in db.scalars(select(Certificate).where(Certificate.status.in_(("pending", "renewing")))).all():
        sync_certificate(db, c)
        out["synced"] += 1
    for c in db.scalars(select(Certificate).where(Certificate.status.in_(("issued", "expired")))).all():
        if c.status == "issued" and c.not_after and c.not_after < now:
            c.status = "expired"
            out["marked_expired"].append(c.domain)
            audit(db, "ssl.expired", tenant_id=c.tenant_id, target_type="certificate", target_id=c.id, detail={"domain": c.domain})
            to = _contact_email(db, c.tenant_id)
            if to:
                queue_email(db, to, f"SSL certificate expired for {c.domain}", f"The certificate for {c.domain} has expired. Visitors may see security warnings until it is renewed.", "ssl_expired", c.tenant_id)
        if not c.auto_renew or not c.not_after or c.not_after - now > timedelta(days=window_days):
            continue
        if c.renewal_failures >= MAX_RENEW_FAILURES:
            if c.renewal_failures == MAX_RENEW_FAILURES:
                out["gave_up"].append(c.domain)
                audit(db, "ssl.renewal_exhausted", tenant_id=c.tenant_id, target_type="certificate", target_id=c.id, detail={"domain": c.domain, "failures": c.renewal_failures})
                c.renewal_failures += 1  # alert once
            continue
        backoff = timedelta(hours=6 * (2 ** max(c.renewal_failures - 1, 0))) if c.renewal_failures else timedelta(0)
        if c.last_attempt_at and now - c.last_attempt_at < backoff:
            out["skipped_backoff"].append(c.domain)
            continue
        try:
            renew_certificate(db, c, None, reason="sweep")
            out["renewals_enqueued"].append(c.domain)
        except Exception as e:  # noqa: BLE001 - one bad certificate must not stop the sweep
            out.setdefault("errors", []).append({"domain": c.domain, "error": getattr(e, "detail", str(e))})
    return out


def overview(db: Session, tenant_id: uuid.UUID | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    stmt = select(Certificate)
    if tenant_id:
        stmt = stmt.where(Certificate.tenant_id == tenant_id)
    certs = db.scalars(stmt.order_by(Certificate.not_after.asc().nulls_last())).all()
    for c in certs:
        sync_certificate(db, c)
    views = [cert_view(c, now) for c in certs]
    counts: dict[str, int] = {}
    for v in views:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    exp = lambda n: sum(1 for v in views if v["status"] == "issued" and v["days_remaining"] is not None and v["days_remaining"] <= n)  # noqa: E731
    return {"counts": counts, "total": len(views), "expiring_7d": exp(7), "expiring_14d": exp(14), "expiring_30d": exp(30),
            "staging_only": sum(1 for v in views if v["status"] == "issued" and not v["trusted"]), "certificates": views}
