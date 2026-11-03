"""Reseller lifecycle, packages, allocation enforcement, client provisioning, commission ledger, white-label domains."""
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import Ctx, api_error
from app.core.security import new_token, sha256
from app.models.identity import Invitation, Role
from app.models.reseller import (
    PACKAGE_RESOURCES, CommissionEntry, CommissionRule, Reseller, ResellerClient, ResellerDomain, ResellerPackage,
)
from app.models.tenant import Tenant, TenantQuota
from app.services import automation
from app.services.audit import audit
from app.services.mail import queue_email
from app.services.tenants import provision_tenant

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
TXT_PREFIX = "_kvl-verify."
VERIFY_WINDOW = timedelta(hours=72)


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------- scoping
def own_reseller(db: Session, ctx: Ctx, *, lock: bool = False, require_approved: bool = True) -> Reseller:
    """The caller's reseller. Derived from the membership only; client-supplied ids are never trusted."""
    if ctx.scope != "reseller" or not ctx.reseller_id:
        raise api_error(403, "forbidden", "Reseller account required.")
    q = select(Reseller).where(Reseller.id == ctx.reseller_id)
    r = db.scalar(q.with_for_update() if lock else q)
    if r is None:
        raise api_error(404, "not_found", "Reseller not found.")
    if require_approved and r.status != "approved":
        raise api_error(403, "reseller_not_approved", "Your reseller account is not approved.")
    return r


def own_client(db: Session, reseller: Reseller, tenant_id: uuid.UUID) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if t is None or t.reseller_id != reseller.id:
        raise api_error(404, "not_found", "Client not found.")
    return t


def own_package(db: Session, reseller: Reseller, package_id: uuid.UUID) -> ResellerPackage:
    p = db.get(ResellerPackage, package_id)
    if p is None or p.reseller_id != reseller.id:
        raise api_error(404, "not_found", "Package not found.")
    return p


# ------------------------------------------------------------------------------------- allocation
def allocation(r: Reseller) -> dict[str, int]:
    return {k: int(v) for k, v in ((r.limits or {}).get("allocation") or {}).items() if k in PACKAGE_RESOURCES}


def committed(db: Session, reseller_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(TenantQuota.resource, func.coalesce(func.sum(TenantQuota.limit_value), 0)).join(Tenant, Tenant.id == TenantQuota.tenant_id)
        .where(Tenant.reseller_id == reseller_id, Tenant.status != "cancelled").group_by(TenantQuota.resource)
    ).all()
    return {res: int(v) for res, v in rows}


def used(db: Session, reseller_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(TenantQuota.resource, func.coalesce(func.sum(TenantQuota.used_value), 0)).join(Tenant, Tenant.id == TenantQuota.tenant_id)
        .where(Tenant.reseller_id == reseller_id, Tenant.status != "cancelled").group_by(TenantQuota.resource)
    ).all()
    return {res: int(v) for res, v in rows}


def check_allocation(db: Session, r: Reseller, deltas: dict[str, int]) -> None:
    """Platform allocation is a hard ceiling: committed client quotas may never exceed it. Caller holds the reseller row lock."""
    alloc, now = allocation(r), committed(db, r.id)
    for res, d in deltas.items():
        if d <= 0:
            continue
        if res not in alloc:
            raise api_error(409, "not_in_allocation", f"'{res}' is not part of your platform allocation.")
        if now.get(res, 0) + d > alloc[res]:
            raise api_error(409, "allocation_exceeded", f"This would commit {now.get(res, 0) + d} {res}; your platform allocation is {alloc[res]}.")


def wholesale_for(r: Reseller, limits: dict[str, int]) -> int:
    costs = (r.limits or {}).get("unit_costs") or {}
    return sum(int(costs.get(k, 0)) * int(v) for k, v in limits.items())


def validate_package_limits(r: Reseller, limits: dict[str, int]) -> dict[str, int]:
    alloc = allocation(r)
    clean: dict[str, int] = {}
    for k, v in limits.items():
        if k not in PACKAGE_RESOURCES:
            raise api_error(422, "validation_error", f"Unknown resource '{k}'.")
        if int(v) < 0:
            raise api_error(422, "validation_error", f"{k} must be >= 0.")
        if k not in alloc:
            raise api_error(409, "not_in_allocation", f"'{k}' is not part of your platform allocation.")
        if int(v) > alloc[k]:
            raise api_error(409, "allocation_exceeded", f"A package cannot offer more {k} than your platform allocation ({alloc[k]}).")
        clean[k] = int(v)
    if not clean:
        raise api_error(422, "validation_error", "A package needs at least one resource limit.")
    return clean


def package_dto(r: Reseller, p: ResellerPackage) -> dict[str, Any]:
    margin = p.retail_price_paise - p.wholesale_price_paise
    return {
        "id": str(p.id), "name": p.name, "description": p.description, "limits": p.limits, "active": p.active,
        "retail_price_paise": p.retail_price_paise, "wholesale_price_paise": p.wholesale_price_paise,
        "margin_paise": margin, "margin_bps": (margin * 10000 // p.retail_price_paise) if p.retail_price_paise else 0, "created_at": p.created_at,
    }


def apply_package_quotas(db: Session, tenant: Tenant, limits: dict[str, int]) -> None:
    db.flush()
    for res in PACKAGE_RESOURCES:
        lim = int(limits.get(res, 0))
        q = db.scalar(select(TenantQuota).where(TenantQuota.tenant_id == tenant.id, TenantQuota.resource == res).with_for_update())
        if q:
            q.limit_value = lim
        else:
            db.add(TenantQuota(tenant_id=tenant.id, resource=res, limit_value=lim))


def client_limits(db: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    return {q.resource: q.limit_value for q in db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == tenant_id))}


# ---------------------------------------------------------------------------------------- clients
def create_client(db: Session, ctx: Ctx, r: Reseller, name: str, contact_email: str, package: ResellerPackage | None, notes: str | None) -> Tenant:
    r = db.scalar(select(Reseller).where(Reseller.id == r.id).with_for_update())
    max_clients = (r.limits or {}).get("max_clients")
    count = db.scalar(select(func.count()).select_from(Tenant).where(Tenant.reseller_id == r.id, Tenant.status != "cancelled")) or 0
    if max_clients is not None and count >= int(max_clients):
        raise api_error(409, "client_limit_reached", f"Your client limit ({max_clients}) has been reached.")
    if package is not None:
        if not package.active:
            raise api_error(409, "package_inactive", "Package is not active.")
        check_allocation(db, r, dict(package.limits))
    t = provision_tenant(db, name, None, contact_email, status="active", plan_code=None, reseller_id=r.id, actor=ctx.user.id)
    apply_package_quotas(db, t, package.limits if package else {})
    db.add(ResellerClient(reseller_id=r.id, tenant_id=t.id, package_id=package.id if package else None, renews_at=utcnow() + timedelta(days=30) if package else None, notes=notes))
    role = db.scalar(select(Role).where(Role.name == "customer"))
    token = new_token(32)
    db.add(Invitation(email=contact_email.lower(), role_id=role.id, tenant_id=t.id, invited_by=ctx.user.id, token_hash=sha256(token), expires_at=utcnow() + timedelta(days=7)))
    queue_email(db, contact_email, f"You have been invited to {(r.branding or {}).get('portal_name') or r.name}", f"Accept your invitation: /accept-invite?token={token} (valid 7 days).", "invitation", t.id)
    automation.emit_event(db, "tenant.created", {"tenant_id": t.id, "tenant_name": t.name, "reseller_id": r.id}, tenant_id=t.id, idempotency_key=f"tenant_created:{t.id}")
    audit(db, "reseller.client_created", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, detail={"reseller_id": str(r.id), "package": str(package.id) if package else None})
    return t


def assign_package(db: Session, ctx: Ctx, r: Reseller, tenant: Tenant, package: ResellerPackage) -> None:
    r = db.scalar(select(Reseller).where(Reseller.id == r.id).with_for_update())
    if not package.active:
        raise api_error(409, "package_inactive", "Package is not active.")
    current = client_limits(db, tenant.id)
    check_allocation(db, r, {k: int(package.limits.get(k, 0)) - current.get(k, 0) for k in PACKAGE_RESOURCES})
    apply_package_quotas(db, tenant, package.limits)
    link = db.scalar(select(ResellerClient).where(ResellerClient.tenant_id == tenant.id))
    if link is None:
        link = ResellerClient(reseller_id=r.id, tenant_id=tenant.id)
        db.add(link)
    link.package_id = package.id
    link.renews_at = link.renews_at or utcnow() + timedelta(days=30)
    audit(db, "reseller.package_assigned", actor=ctx.user.id, tenant_id=tenant.id, target_type="package", target_id=package.id)


# ---------------------------------------------------------------------------------------- commissions
def _rule_for(db: Session, reseller_id: uuid.UUID, plan_code: str | None) -> CommissionRule | None:
    rules = db.scalars(select(CommissionRule).where(CommissionRule.active.is_(True), (CommissionRule.reseller_id == reseller_id) | CommissionRule.reseller_id.is_(None))).all()

    def score(x: CommissionRule) -> int:
        return (2 if x.reseller_id else 0) + (1 if x.plan_code else 0)

    fit = [x for x in rules if x.plan_code is None or x.plan_code == plan_code]
    return max(fit, key=score) if fit else None


def record_sale_commission(db: Session, inv) -> CommissionEntry | None:  # inv: Invoice
    from app.models.billing import Plan, PlanVersion, Subscription

    t = db.get(Tenant, inv.tenant_id)
    if t is None or not t.reseller_id or inv.taxable_paise <= 0:
        return None
    plan_code = None
    if inv.subscription_id:
        plan_code = db.scalar(select(Plan.code).join(PlanVersion, PlanVersion.plan_id == Plan.id).join(Subscription, Subscription.plan_version_id == PlanVersion.id).where(Subscription.id == inv.subscription_id))
    rule = _rule_for(db, t.reseller_id, plan_code)
    if rule is None or inv.taxable_paise < rule.min_invoice_paise:
        return None
    amount = inv.taxable_paise * rule.rate_bps // 10000
    if amount <= 0:
        return None
    e = CommissionEntry(reseller_id=t.reseller_id, tenant_id=t.id, invoice_id=inv.id, kind="sale", ref=str(inv.id), amount_paise=amount, rate_bps=rule.rate_bps, base_paise=inv.taxable_paise, note=f"Rule: {rule.name}")
    try:
        with db.begin_nested():
            db.add(e)
            db.flush()
    except IntegrityError:
        return None
    return e


def reverse_commission(db: Session, inv, refund) -> CommissionEntry | None:
    sale = db.scalar(select(CommissionEntry).where(CommissionEntry.invoice_id == inv.id, CommissionEntry.kind == "sale"))
    if sale is None:
        return None
    done = -(db.scalar(select(func.coalesce(func.sum(CommissionEntry.amount_paise), 0)).where(CommissionEntry.invoice_id == inv.id, CommissionEntry.kind == "reversal")) or 0)
    amount = min((sale.amount_paise * refund.amount_paise + inv.total_paise // 2) // max(inv.total_paise, 1), sale.amount_paise - int(done))
    if amount <= 0:
        return None
    e = CommissionEntry(
        reseller_id=sale.reseller_id, tenant_id=sale.tenant_id, invoice_id=inv.id, kind="reversal", ref=str(refund.id), amount_paise=-amount,
        status="approved" if sale.status == "paid" else sale.status, note="Refund reversal",
        approved_at=utcnow() if sale.status in ("approved", "paid") else None,
    )
    db.add(e)
    db.flush()
    return e


def ledger_balances(db: Session, reseller_id: uuid.UUID) -> dict[str, int]:
    """Balances are derived from the ledger on every call; nothing is stored."""
    rows = dict(db.execute(select(CommissionEntry.status, func.coalesce(func.sum(CommissionEntry.amount_paise), 0)).where(CommissionEntry.reseller_id == reseller_id).group_by(CommissionEntry.status)).all())
    pending, approved, paid = (int(rows.get(s, 0)) for s in ("pending", "approved", "paid"))
    return {"pending_paise": pending, "approved_paise": approved, "paid_paise": paid, "payable_paise": approved, "total_earned_paise": pending + approved + paid}


def entry_dto(e: CommissionEntry) -> dict[str, Any]:
    return {
        "id": str(e.id), "kind": e.kind, "tenant_id": str(e.tenant_id) if e.tenant_id else None, "invoice_id": str(e.invoice_id) if e.invoice_id else None,
        "amount_paise": e.amount_paise, "rate_bps": e.rate_bps, "base_paise": e.base_paise, "status": e.status, "payout_reference": e.payout_reference,
        "note": e.note, "created_at": e.created_at, "approved_at": e.approved_at, "paid_at": e.paid_at,
    }


def approve_entry(db: Session, e: CommissionEntry) -> None:
    if e.status != "pending":
        raise api_error(409, "invalid_transition", f"Only pending entries can be approved (this one is {e.status}).")
    e.status, e.approved_at = "approved", utcnow()


def pay_entries(db: Session, reseller_id: uuid.UUID, payout_reference: str) -> int:
    rows = db.scalars(select(CommissionEntry).where(CommissionEntry.reseller_id == reseller_id, CommissionEntry.status == "approved").with_for_update()).all()
    for e in rows:
        e.status, e.paid_at, e.payout_reference = "paid", utcnow(), payout_reference
    return len(rows)


def reconcile(db: Session, reseller_id: uuid.UUID) -> dict[str, Any]:
    from app.models.billing import Invoice

    issues: list[dict[str, Any]] = []
    entries = db.scalars(select(CommissionEntry).where(CommissionEntry.reseller_id == reseller_id)).all()
    sales = {e.invoice_id: e for e in entries if e.kind == "sale"}
    for e in entries:
        inv = db.get(Invoice, e.invoice_id) if e.invoice_id else None
        if e.kind == "sale" and (inv is None or inv.status != "paid"):
            issues.append({"entry_id": str(e.id), "issue": "sale entry without a paid invoice"})
        if e.kind == "sale" and inv and e.base_paise != inv.taxable_paise:
            issues.append({"entry_id": str(e.id), "issue": "base differs from invoice taxable amount"})
    reversed_by_inv: dict[uuid.UUID, int] = {}
    for e in entries:
        if e.kind == "reversal":
            reversed_by_inv[e.invoice_id] = reversed_by_inv.get(e.invoice_id, 0) - e.amount_paise
    for inv_id, amt in reversed_by_inv.items():
        if inv_id not in sales or amt > sales[inv_id].amount_paise:
            issues.append({"invoice_id": str(inv_id), "issue": "reversals exceed the sale entry"})
    paid_invoices = db.scalars(select(Invoice).join(Tenant, Tenant.id == Invoice.tenant_id).where(Tenant.reseller_id == reseller_id, Invoice.status == "paid", Invoice.taxable_paise > 0)).all()
    without = [str(i.id) for i in paid_invoices if i.id not in sales]
    b = ledger_balances(db, reseller_id)
    total = sum(e.amount_paise for e in entries)
    return {
        "balances": b, "entries": len(entries), "ledger_sum_paise": total, "consistent": total == b["total_earned_paise"] and not issues,
        "issues": issues, "paid_invoices_without_commission": without,
        "note": "Paid invoices without a commission entry had no matching commission rule at the time or fell below its minimum.",
    }


# ------------------------------------------------------------------------------------ white-label
class Resolver(Protocol):
    def txt(self, name: str) -> list[str]: ...


class DnsPythonResolver:
    def txt(self, name: str) -> list[str]:
        import dns.exception
        import dns.resolver

        try:
            ans = dns.resolver.resolve(name, "TXT", lifetime=4.0)
        except (dns.exception.DNSException, OSError):
            return []
        return ["".join(p.decode() for p in r.strings) for r in ans]


def get_resolver() -> Resolver:
    return DnsPythonResolver()


def new_domain(db: Session, r: Reseller, domain: str) -> ResellerDomain:
    domain = domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.match(domain):
        raise api_error(422, "validation_error", "Enter a valid domain name such as panel.example.com.")
    d = ResellerDomain(reseller_id=r.id, domain=domain, txt_token=secrets.token_hex(16))
    try:
        with db.begin_nested():
            db.add(d)
            db.flush()
    except IntegrityError:
        raise api_error(409, "domain_taken", "This domain is already registered.") from None
    return d


def domain_instructions(d: ResellerDomain) -> dict[str, str]:
    return {"type": "TXT", "name": f"{TXT_PREFIX}{d.domain}", "value": f"kvl-verify={d.txt_token}"}


def verify_domain(db: Session, d: ResellerDomain, resolver: Resolver | None = None, now: datetime | None = None) -> ResellerDomain:
    """pending -> verified | pending (not yet seen) | failed (not seen within 72h). failed/verified re-check allowed."""
    now = now or utcnow()
    values = (resolver or get_resolver()).txt(f"{TXT_PREFIX}{d.domain}")
    d.last_checked_at = now
    if f"kvl-verify={d.txt_token}" in [v.strip().strip('"') for v in values]:
        d.state, d.verified_at, d.failure_reason = "verified", d.verified_at or now, None
    elif d.state == "verified":
        d.state, d.failure_reason, d.verified_at = "failed", "The TXT record is no longer present.", None
    elif now - d.created_at > VERIFY_WINDOW:
        d.state, d.failure_reason = "failed", "TXT record not found within 72 hours."
    else:
        d.state, d.failure_reason = "pending", "TXT record not found yet. DNS changes can take time to propagate."
    return d


def domain_dto(d: ResellerDomain) -> dict[str, Any]:
    return {"id": str(d.id), "domain": d.domain, "state": d.state, "dns_record": domain_instructions(d), "last_checked_at": d.last_checked_at, "verified_at": d.verified_at, "failure_reason": d.failure_reason}


PLATFORM_DISCLOSURE = "Hosting infrastructure is owned and operated by KVL Hosting. Your reseller is responsible for your commercial relationship."
