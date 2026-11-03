"""Domain registration, ownership conflict prevention and DNS TXT verification."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.core.security import new_token
from app.models.hosting import Domain
from app.services import dnsverify
from app.services.hostnames import HostnameError, ancestors, normalize_hostname
from app.services.resources import log_event


def add_domain(db: Session, tenant_id: uuid.UUID, raw: str, *, kind: str = "primary", website_id: uuid.UUID | None = None, actor: uuid.UUID | None = None) -> Domain:
    try:
        ascii_name, uni = normalize_hostname(raw)
    except HostnameError as e:
        raise api_error(422, "invalid_hostname", str(e)) from None
    if db.scalar(select(Domain.id).where(Domain.hostname == ascii_name)):
        # Same message for own and foreign claims: do not reveal who owns a hostname.
        raise api_error(409, "domain_conflict", "This hostname is already registered on the platform.")
    parent_verified_same_tenant = False
    for anc in ancestors(ascii_name):
        d = db.scalar(select(Domain).where(Domain.hostname == anc, Domain.verification_state == "verified"))
        if d and d.tenant_id != tenant_id:
            raise api_error(409, "domain_conflict", "This hostname belongs to a domain that is verified by another account.")
        if d and d.tenant_id == tenant_id:
            parent_verified_same_tenant = True
    dom = Domain(tenant_id=tenant_id, website_id=website_id, hostname=ascii_name, unicode_name=uni if uni != ascii_name else None, kind=kind, verification_token=new_token(24))
    if parent_verified_same_tenant:
        dom.verification_state, dom.verified_at, dom.verified_via = "verified", datetime.now(UTC), "parent_domain"
    db.add(dom)
    db.flush()
    log_event(db, tenant_id, "domain", dom.id, actor, "domain.added", f"Domain {ascii_name} added ({kind})")
    return dom


def verify_domain(db: Session, dom: Domain, actor: uuid.UUID | None) -> Domain:
    now = datetime.now(UTC)
    dom.last_checked_at = now
    if dom.verification_state == "verified":
        return dom
    resolver = dnsverify.resolver_factory()
    try:
        ok = dnsverify.check_txt(resolver, dom.hostname, dom.verification_token)
    except dnsverify.ResolverUnavailable as e:
        dom.verification_state, dom.last_error = "unavailable", str(e)[:300]
        return dom
    if ok:
        # Re-check conflicts: another tenant may have verified an ancestor since we registered.
        for anc in ancestors(dom.hostname):
            d = db.scalar(select(Domain).where(Domain.hostname == anc, Domain.verification_state == "verified", Domain.tenant_id != dom.tenant_id))
            if d:
                dom.verification_state, dom.last_error = "failed", "An ancestor domain is verified by another account."
                return dom
        dom.verification_state, dom.verified_at, dom.verified_via, dom.last_error = "verified", now, "dns_txt", None
        log_event(db, dom.tenant_id, "domain", dom.id, actor, "domain.verified", f"Ownership of {dom.hostname} verified via DNS TXT")
    else:
        dom.verification_state = "pending" if dom.verification_state in {"pending", "unavailable"} else "failed"
        dom.last_error = "TXT record not found yet. DNS changes can take time to propagate."
    return dom


def domain_dto(d: Domain) -> dict:
    return {
        "id": str(d.id), "tenant_id": str(d.tenant_id), "website_id": str(d.website_id) if d.website_id else None,
        "hostname": d.hostname, "unicode_name": d.unicode_name, "kind": d.kind,
        "verification_state": d.verification_state, "verified_at": d.verified_at, "verified_via": d.verified_via,
        "last_checked_at": d.last_checked_at, "last_error": d.last_error, "expires_at": d.expires_at, "registrar": d.registrar,
        "dns_challenge": None if d.verification_state == "verified" else dnsverify.challenge_for(d.hostname, d.verification_token),
        "created_at": d.created_at,
    }
