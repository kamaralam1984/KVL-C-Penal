"""Business email setup for a customer domain with a hosted mail provider (Google Workspace, Zoho Mail, Microsoft 365).

The panel does not run a mail server (a home/office Mac cannot deliver mail reliably: blocked port 25, no reverse DNS).
Instead this computes the DNS records the chosen provider needs, compares them with the domain's mirrored DNS records
and lets the customer prepare each missing record through the normal DNS change workflow (preview -> confirm -> rollback,
mail records need an explicit acknowledgement). Nothing is changed by this router itself.
"""
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, require, require_tenant
from app.models.ssl import DnsRecord
from app.services.ownership import owns_resource

router = APIRouter(prefix="/email-setup", tags=["email"])
Provider = Literal["google", "zoho_in", "zoho_com", "microsoft"]
DKIM_SELECTOR = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DKIM_VALUE = re.compile(r"^v=DKIM1;[ A-Za-z0-9=;+/_-]{20,2000}$")

PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {"label": "Google Workspace", "mx": [("smtp.google.com", 1)], "spf": "include:_spf.google.com", "dkim_selector": "google",
               "smtp": {"host": "smtp.gmail.com", "port": 587, "security": "starttls"}},
    "zoho_in": {"label": "Zoho Mail (India data centre)", "mx": [("mx.zoho.in", 10), ("mx2.zoho.in", 20), ("mx3.zoho.in", 50)], "spf": "include:zohomail.in",
                "dkim_selector": "zmail", "smtp": {"host": "smtp.zoho.in", "port": 587, "security": "starttls"}},
    "zoho_com": {"label": "Zoho Mail (US data centre)", "mx": [("mx.zoho.com", 10), ("mx2.zoho.com", 20), ("mx3.zoho.com", 50)], "spf": "include:zohomail.com",
                 "dkim_selector": "zmail", "smtp": {"host": "smtp.zoho.com", "port": 587, "security": "starttls"}},
    "microsoft": {"label": "Microsoft 365", "mx": [], "spf": "include:spf.protection.outlook.com", "dkim_selector": None,
                  "smtp": {"host": "smtp.office365.com", "port": 587, "security": "starttls"}},
}


def _domain(db: Session, ctx: Ctx, domain: str, tenant_id: uuid.UUID | None) -> tuple[uuid.UUID, str]:
    tid = require_tenant(ctx, tenant_id)
    d = domain.strip().lower().rstrip(".")
    if not owns_resource(db, tid, "domain", d):
        raise api_error(404, "not_found", "Domain not found.")
    return tid, d


def required_records(provider: str, domain: str, dkim_selector: str | None, dkim_value: str | None) -> list[dict[str, Any]]:
    p = PROVIDERS[provider]
    mx = p["mx"] or [(domain.replace(".", "-") + ".mail.protection.outlook.com", 0)]
    recs: list[dict[str, Any]] = [{"key": f"mx{i}", "purpose": "Receive mail", "type": "MX", "name": domain, "content": host, "priority": prio} for i, (host, prio) in enumerate(mx)]
    recs.append({"key": "spf", "purpose": "Allow the provider to send as you (SPF)", "type": "TXT", "name": domain, "content": f"v=spf1 {p['spf']} ~all", "priority": None})
    if provider == "microsoft":
        recs.append({"key": "autodiscover", "purpose": "Outlook auto-configuration", "type": "CNAME", "name": f"autodiscover.{domain}", "content": "autodiscover.outlook.com", "priority": None})
    sel = dkim_selector or p["dkim_selector"]
    if sel and dkim_value:
        recs.append({"key": "dkim", "purpose": "Sign outgoing mail (DKIM)", "type": "TXT", "name": f"{sel}._domainkey.{domain}", "content": dkim_value, "priority": None})
    recs.append({"key": "dmarc", "purpose": "Tell receivers how to treat spoofed mail (DMARC)", "type": "TXT", "name": f"_dmarc.{domain}", "content": f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}", "priority": None})
    return recs


def _norm(s: str) -> str:
    return s.strip().strip('"').rstrip(".").lower()


def status_of(req: dict[str, Any], existing: list[DnsRecord]) -> dict[str, Any]:
    same_name = [r for r in existing if r.name == req["name"]]
    if req["type"] == "TXT" and req["key"] in ("spf", "dmarc"):
        prefix = "v=spf1" if req["key"] == "spf" else "v=dmarc1"
        cur = [r for r in same_name if r.rtype == "TXT" and _norm(r.content).startswith(prefix)]
        if not cur:
            return {"state": "missing", "action": "create", "record_id": None}
        if req["key"] == "spf":
            needed = _norm(req["content"]).split()[1]
            ok = needed in _norm(cur[0].content).split()
            return {"state": "ok" if ok else "conflict", "action": None if ok else "update", "record_id": str(cur[0].id), "current": cur[0].content,
                    "note": None if ok else "Your domain already has an SPF record: the provider's include must be added to it (only one SPF record is allowed)."}
        return {"state": "ok", "action": None, "record_id": str(cur[0].id), "current": cur[0].content}
    match = [r for r in same_name if r.rtype == req["type"] and _norm(r.content) == _norm(req["content"]) and (req["priority"] is None or r.priority == req["priority"])]
    if match:
        return {"state": "ok", "action": None, "record_id": str(match[0].id)}
    if req["type"] == "CNAME" and any(r.rtype in ("A", "AAAA", "CNAME") for r in same_name):
        return {"state": "conflict", "action": None, "record_id": None, "note": "Another record already uses this name."}
    return {"state": "missing", "action": "create", "record_id": None}


@router.get("/providers")
def providers(ctx: Ctx = Depends(require("dns:read"))):
    return [{"id": k, "label": v["label"], "dkim_selector": v["dkim_selector"], "smtp": v["smtp"]} for k, v in PROVIDERS.items()]


@router.get("/{domain}")
def plan(domain: str, provider: Provider, dkim_selector: str | None = Query(None, max_length=63), dkim_value: str | None = Query(None, max_length=2100),
         tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    tid, d = _domain(db, ctx, domain, tenant_id)
    if dkim_selector is not None and not DKIM_SELECTOR.fullmatch(dkim_selector):
        raise api_error(422, "validation_error", "Invalid DKIM selector.")
    if dkim_value is not None and not DKIM_VALUE.fullmatch(dkim_value.strip()):
        raise api_error(422, "validation_error", "Paste the DKIM value exactly as the provider shows it (starts with v=DKIM1;).")
    existing = list(db.scalars(select(DnsRecord).where(DnsRecord.tenant_id == tid, DnsRecord.domain == d)))
    recs = []
    for r in required_records(provider, d, dkim_selector, dkim_value.strip() if dkim_value else None):
        recs.append({**r, **status_of(r, existing)})
    other_mx = [x.content for x in existing if x.rtype == "MX" and x.name == d and _norm(x.content) not in {_norm(r["content"]) for r in recs if r["type"] == "MX"}]
    return {"domain": d, "provider": provider, "label": PROVIDERS[provider]["label"], "records": recs, "synced": bool(existing),
            "other_mx": other_mx, "smtp": PROVIDERS[provider]["smtp"],
            "ready": all(r["state"] == "ok" for r in recs) and not other_mx}
