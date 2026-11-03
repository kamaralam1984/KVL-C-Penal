"""Phase 13: reseller lifecycle (admin) and reseller portal (own clients, packages, branding, commissions, reports)."""
import csv
import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, get_ctx, require
from app.models.billing import Invoice
from app.models.identity import Membership, Role, User
from app.models.reseller import (
    PACKAGE_RESOURCES, CommissionEntry, CommissionRule, Reseller, ResellerClient, ResellerDomain, ResellerPackage,
)
from app.models.tenant import Tenant, TenantActivity, TenantQuota
from app.services import reseller as svc
from app.services.audit import audit
from app.services.tenants import log_activity

router = APIRouter(tags=["resellers"])
HEX = "^#[0-9a-fA-F]{6}$"


def csv_response(name: str, header: list[str], rows: list[list[Any]]) -> Response:
    """CSV with formula-injection protection."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow([("'" + str(c)) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@", "\t", "\r") else ("" if c is None else c) for c in row])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


def reseller_dto(db: Session, r: Reseller, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(r.id), "name": r.name, "legal_name": r.legal_name, "contact_email": r.contact_email, "status": r.status,
        "business": r.business or {}, "limits": r.limits or {}, "applied_at": r.applied_at, "decided_at": r.decided_at, "decision_note": r.decision_note,
        "owner_user_id": str(r.owner_user_id) if r.owner_user_id else None,
    }
    if detail:
        out["clients"] = db.scalar(select(func.count()).select_from(Tenant).where(Tenant.reseller_id == r.id, Tenant.status != "cancelled")) or 0
        out["allocation"] = svc.allocation(r)
        out["committed"] = svc.committed(db, r.id)
        out["used"] = svc.used(db, r.id)
        out["balances"] = svc.ledger_balances(db, r.id)
    return out


# ============================================================================================ apply
class BusinessIn(BaseModel):
    gstin: str | None = Field(default=None, pattern=r"^[0-9A-Z]{15}$")
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=30)
    website: str | None = Field(default=None, max_length=200, pattern=r"^https?://")


class ApplyIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr
    business: BusinessIn | None = None


@router.post("/reseller/apply", status_code=201)
def apply(body: ApplyIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Any verified signed-in user may apply. Nothing is granted until a Super Admin approves."""
    ratelimit.enforce(f"reseller_apply:{ctx.user.id}", 5, 3600)
    if ctx.user.email_verified_at is None:
        raise api_error(403, "email_unverified", "Verify your email before applying.")
    if ctx.scope in ("platform", "reseller"):
        raise api_error(409, "not_eligible", "This account cannot apply as a reseller.")
    if db.scalar(select(Reseller.id).where(Reseller.owner_user_id == ctx.user.id, Reseller.status.in_(("pending_approval", "approved", "suspended")))):
        raise api_error(409, "application_exists", "You already have a reseller application or account.")
    r = Reseller(name=body.name.strip(), legal_name=body.legal_name, contact_email=str(body.contact_email).lower(), owner_user_id=ctx.user.id, business=body.business.model_dump(exclude_none=True) if body.business else {}, limits={}, branding={})
    db.add(r)
    db.flush()
    audit(db, "reseller.applied", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return {"id": str(r.id), "status": r.status}


@router.get("/reseller/application")
def my_application(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    r = db.scalar(select(Reseller).where(Reseller.owner_user_id == ctx.user.id).order_by(Reseller.applied_at.desc()))
    return {"application": reseller_dto(db, r) if r else None}


# ============================================================================================ admin
class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    max_clients: int | None = Field(default=None, ge=0, le=100000)
    allocation: dict[str, int] | None = None
    unit_costs: dict[str, int] | None = None


class LimitsIn(BaseModel):
    max_clients: int | None = Field(default=None, ge=0, le=100000)
    allocation: dict[str, int] = Field(default_factory=dict)
    unit_costs: dict[str, int] = Field(default_factory=dict)


def _clean_limits(body: LimitsIn | DecisionIn) -> dict[str, Any]:
    for m in (body.allocation or {}, body.unit_costs or {}):
        for k, v in m.items():
            if k not in PACKAGE_RESOURCES or v < 0 or v > 10**10:
                raise api_error(422, "validation_error", f"Invalid resource entry '{k}'.")
    return {"max_clients": body.max_clients, "allocation": body.allocation or {}, "unit_costs": body.unit_costs or {}}


def _get(db: Session, rid: uuid.UUID) -> Reseller:
    r = db.get(Reseller, rid)
    if r is None:
        raise api_error(404, "not_found", "Reseller not found.")
    return r


@router.get("/resellers")
def list_resellers(status: str | None = None, ctx: Ctx = Depends(require("resellers:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    stmt = select(Reseller).order_by(Reseller.applied_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Reseller.status == status)
    return {"items": [reseller_dto(db, r, True) for r in db.scalars(stmt)]}


@router.get("/resellers/commission-rules")
def list_rules(reseller_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    stmt = select(CommissionRule).order_by(CommissionRule.created_at.desc())
    if reseller_id:
        stmt = stmt.where(CommissionRule.reseller_id == reseller_id)
    return {"items": [{"id": str(x.id), "reseller_id": str(x.reseller_id) if x.reseller_id else None, "name": x.name, "rate_bps": x.rate_bps, "plan_code": x.plan_code, "min_invoice_paise": x.min_invoice_paise, "active": x.active} for x in db.scalars(stmt)]}


class RuleIn(BaseModel):
    reseller_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=120)
    rate_bps: int = Field(ge=0, le=10000)
    plan_code: str | None = Field(default=None, max_length=60)
    min_invoice_paise: int = Field(default=0, ge=0)
    active: bool = True


@router.post("/resellers/commission-rules", status_code=201)
def create_rule(body: RuleIn, request: Request, ctx: Ctx = Depends(require("commissions:manage")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    if body.reseller_id:
        _get(db, body.reseller_id)
    x = CommissionRule(**body.model_dump())
    db.add(x)
    db.flush()
    audit(db, "commission.rule_created", actor=ctx.user.id, target_type="commission_rule", target_id=x.id, detail=body.model_dump(mode="json"), ip=client_ip(request))
    db.commit()
    return {"id": str(x.id)}


@router.patch("/resellers/commission-rules/{rule_id}")
def update_rule(rule_id: uuid.UUID, body: dict[str, Any], request: Request, ctx: Ctx = Depends(require("commissions:manage")), db: Session = Depends(get_db)):
    x = db.get(CommissionRule, rule_id)
    if x is None or not ctx.is_platform:
        raise api_error(404, "not_found", "Rule not found.")
    if "rate_bps" in body:
        if not isinstance(body["rate_bps"], int) or not 0 <= body["rate_bps"] <= 10000:
            raise api_error(422, "validation_error", "rate_bps must be 0..10000.")
        x.rate_bps = body["rate_bps"]
    if "active" in body:
        x.active = bool(body["active"])
    audit(db, "commission.rule_updated", actor=ctx.user.id, target_type="commission_rule", target_id=x.id, detail={k: body[k] for k in ("rate_bps", "active") if k in body}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/resellers/{rid}")
def get_reseller(rid: uuid.UUID, ctx: Ctx = Depends(require("resellers:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    return reseller_dto(db, _get(db, rid), True)


def _grant_membership(db: Session, r: Reseller, user_id: uuid.UUID) -> None:
    role = db.scalar(select(Role).where(Role.name == "reseller"))
    exists = db.scalar(select(Membership.id).where(Membership.user_id == user_id, Membership.role_id == role.id, Membership.reseller_id == r.id))
    if not exists:
        db.add(Membership(user_id=user_id, role_id=role.id, tenant_id=None, reseller_id=r.id))


@router.post("/resellers/{rid}/approve")
def approve(rid: uuid.UUID, body: DecisionIn, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = _get(db, rid)
    if r.status not in ("pending_approval", "rejected"):
        raise api_error(409, "invalid_transition", f"Cannot approve a reseller in status {r.status}.")
    r.limits = _clean_limits(body)
    r.status, r.decided_by, r.decision_note, r.decided_at = "approved", ctx.user.id, body.note, svc.utcnow()
    if r.owner_user_id:
        _grant_membership(db, r, r.owner_user_id)
    audit(db, "reseller.approved", actor=ctx.user.id, target_type="reseller", target_id=r.id, detail={"limits": r.limits}, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r, True)


@router.post("/resellers/{rid}/reject")
def reject(rid: uuid.UUID, body: DecisionIn, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = _get(db, rid)
    if r.status != "pending_approval":
        raise api_error(409, "invalid_transition", f"Cannot reject a reseller in status {r.status}.")
    r.status, r.decided_by, r.decision_note, r.decided_at = "rejected", ctx.user.id, body.note, svc.utcnow()
    audit(db, "reseller.rejected", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r)


@router.post("/resellers/{rid}/suspend")
def suspend(rid: uuid.UUID, body: DecisionIn, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = _get(db, rid)
    if r.status != "approved":
        raise api_error(409, "invalid_transition", "Only approved resellers can be suspended.")
    r.status, r.decision_note = "suspended", body.note
    audit(db, "reseller.suspended", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r)


@router.post("/resellers/{rid}/reactivate")
def reactivate(rid: uuid.UUID, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = _get(db, rid)
    if r.status != "suspended":
        raise api_error(409, "invalid_transition", "Reseller is not suspended.")
    r.status = "approved"
    audit(db, "reseller.reactivated", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r)


@router.put("/resellers/{rid}/limits")
def set_limits(rid: uuid.UUID, body: LimitsIn, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = db.scalar(select(Reseller).where(Reseller.id == rid).with_for_update())
    if r is None:
        raise api_error(404, "not_found", "Reseller not found.")
    new = _clean_limits(body)
    committed = svc.committed(db, r.id)
    for res, c in committed.items():
        if c > new["allocation"].get(res, 0):
            raise api_error(409, "allocation_below_committed", f"Allocation for {res} cannot be lowered below the {c} already committed to clients.")
    r.limits = new
    audit(db, "reseller.limits_changed", actor=ctx.user.id, target_type="reseller", target_id=r.id, detail=new, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r, True)


class MemberIn(BaseModel):
    email: EmailStr


@router.post("/resellers/{rid}/members", status_code=201)
def add_member(rid: uuid.UUID, body: MemberIn, request: Request, ctx: Ctx = Depends(require("resellers:manage")), db: Session = Depends(get_db)):
    r = _get(db, rid)
    if r.status != "approved":
        raise api_error(409, "invalid_transition", "Reseller is not approved.")
    u = db.scalar(select(User).where(User.email == str(body.email).lower()))
    if u is None:
        raise api_error(404, "not_found", "No registered user with that email.")
    _grant_membership(db, r, u.id)
    audit(db, "reseller.member_added", actor=ctx.user.id, target_type="reseller", target_id=r.id, detail={"user_id": str(u.id)}, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.get("/resellers/{rid}/commissions")
def admin_ledger(rid: uuid.UUID, status: str | None = None, ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    _get(db, rid)
    stmt = select(CommissionEntry).where(CommissionEntry.reseller_id == rid).order_by(CommissionEntry.created_at.desc()).limit(500)
    if status:
        stmt = stmt.where(CommissionEntry.status == status)
    return {"balances": svc.ledger_balances(db, rid), "items": [svc.entry_dto(e) for e in db.scalars(stmt)]}


@router.post("/resellers/{rid}/commissions/approve")
def approve_entries(rid: uuid.UUID, request: Request, ctx: Ctx = Depends(require("commissions:manage")), db: Session = Depends(get_db)):
    _get(db, rid)
    n = 0
    for e in db.scalars(select(CommissionEntry).where(CommissionEntry.reseller_id == rid, CommissionEntry.status == "pending").with_for_update()):
        svc.approve_entry(db, e)
        n += 1
    audit(db, "commission.approved", actor=ctx.user.id, target_type="reseller", target_id=rid, detail={"entries": n}, ip=client_ip(request))
    db.commit()
    return {"approved": n, "balances": svc.ledger_balances(db, rid)}


class PayoutIn(BaseModel):
    payout_reference: str = Field(min_length=3, max_length=120)


@router.post("/resellers/{rid}/commissions/pay")
def pay(rid: uuid.UUID, body: PayoutIn, request: Request, ctx: Ctx = Depends(require("commissions:manage")), db: Session = Depends(get_db)):
    """Record an offline payout of every approved entry. No money moves from this API; it records a payout the owner made."""
    _get(db, rid)
    n = svc.pay_entries(db, rid, body.payout_reference)
    audit(db, "commission.paid", actor=ctx.user.id, target_type="reseller", target_id=rid, detail={"entries": n, "reference": body.payout_reference}, ip=client_ip(request))
    db.commit()
    return {"paid": n, "balances": svc.ledger_balances(db, rid)}


@router.get("/resellers/{rid}/commissions/reconciliation")
def admin_reconciliation(rid: uuid.UUID, ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    if not ctx.is_platform:
        raise api_error(403, "forbidden", "Platform staff only.")
    _get(db, rid)
    return svc.reconcile(db, rid)


# ============================================================================================ portal
@router.get("/reseller/me")
def me(ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx, require_approved=False)
    return reseller_dto(db, r, True)


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    business: BusinessIn | None = None


@router.patch("/reseller/me")
def update_me(body: ProfileIn, request: Request, ctx: Ctx = Depends(require("branding:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    if body.name:
        r.name = body.name.strip()
    if body.legal_name is not None:
        r.legal_name = body.legal_name
    if body.contact_email:
        r.contact_email = str(body.contact_email).lower()
    if body.business is not None:
        r.business = {**(r.business or {}), **body.business.model_dump(exclude_none=True)}
    audit(db, "reseller.profile_updated", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return reseller_dto(db, r, True)


@router.get("/reseller/allocation")
def allocation(ctx: Ctx = Depends(require("packages:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    alloc, com, used = svc.allocation(r), svc.committed(db, r.id), svc.used(db, r.id)
    return {"max_clients": (r.limits or {}).get("max_clients"), "resources": [{"resource": k, "allocation": v, "committed": com.get(k, 0), "used": used.get(k, 0), "remaining": v - com.get(k, 0)} for k, v in alloc.items()]}


# ---- packages
class PackageIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    limits: dict[str, int]
    retail_price_paise: int = Field(ge=0, le=10**9)


class PackagePatch(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    limits: dict[str, int] | None = None
    retail_price_paise: int | None = Field(default=None, ge=0, le=10**9)
    active: bool | None = None


@router.get("/reseller/packages")
def list_packages(ctx: Ctx = Depends(require("packages:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    return {"items": [svc.package_dto(r, p) for p in db.scalars(select(ResellerPackage).where(ResellerPackage.reseller_id == r.id).order_by(ResellerPackage.created_at.desc()))]}


@router.post("/reseller/packages", status_code=201)
def create_package(body: PackageIn, request: Request, ctx: Ctx = Depends(require("packages:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    limits = svc.validate_package_limits(r, body.limits)
    if db.scalar(select(ResellerPackage.id).where(ResellerPackage.reseller_id == r.id, ResellerPackage.name == body.name.strip())):
        raise api_error(409, "package_exists", "A package with this name already exists.")
    p = ResellerPackage(reseller_id=r.id, name=body.name.strip(), description=body.description, limits=limits, retail_price_paise=body.retail_price_paise, wholesale_price_paise=svc.wholesale_for(r, limits))
    db.add(p)
    db.flush()
    audit(db, "reseller.package_created", actor=ctx.user.id, target_type="package", target_id=p.id, ip=client_ip(request))
    db.commit()
    return svc.package_dto(r, p)


@router.patch("/reseller/packages/{pid}")
def update_package(pid: uuid.UUID, body: PackagePatch, request: Request, ctx: Ctx = Depends(require("packages:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    p = svc.own_package(db, r, pid)
    if body.limits is not None:
        p.limits = svc.validate_package_limits(r, body.limits)
        p.wholesale_price_paise = svc.wholesale_for(r, p.limits)
    if body.retail_price_paise is not None:
        p.retail_price_paise = body.retail_price_paise
    if body.description is not None:
        p.description = body.description
    if body.active is not None:
        p.active = body.active
    audit(db, "reseller.package_updated", actor=ctx.user.id, target_type="package", target_id=p.id, ip=client_ip(request))
    db.commit()
    return svc.package_dto(r, p)


@router.delete("/reseller/packages/{pid}")
def delete_package(pid: uuid.UUID, request: Request, ctx: Ctx = Depends(require("packages:delete")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    p = svc.own_package(db, r, pid)
    in_use = db.scalar(select(func.count()).select_from(ResellerClient).where(ResellerClient.package_id == p.id)) or 0
    if in_use:
        p.active = False  # clients keep their quotas; the package just can't be sold again
        outcome = "deactivated"
    else:
        db.delete(p)
        outcome = "deleted"
    audit(db, f"reseller.package_{outcome}", actor=ctx.user.id, target_type="package", target_id=pid, ip=client_ip(request))
    db.commit()
    return {"status": outcome}


# ---- clients
class ClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    contact_email: EmailStr
    package_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=500)


class ClientPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    contact_email: EmailStr | None = None
    notes: str | None = Field(default=None, max_length=500)
    renews_at: str | None = None


def client_dto(db: Session, t: Tenant, link: ResellerClient | None, pkg: ResellerPackage | None, quotas: list[TenantQuota]) -> dict[str, Any]:
    return {
        "id": str(t.id), "name": t.name, "slug": t.slug, "status": t.status, "contact_email": t.contact_email, "created_at": t.created_at,
        "package_id": str(pkg.id) if pkg else None, "package": pkg.name if pkg else None, "retail_price_paise": pkg.retail_price_paise if pkg else None,
        "renews_at": link.renews_at if link else None, "notes": link.notes if link else None,
        "usage": [{"resource": q.resource, "limit": q.limit_value, "used": q.used_value} for q in sorted(quotas, key=lambda x: x.resource)],
    }


def _client_rows(db: Session, r: Reseller, only: uuid.UUID | None = None) -> list[dict[str, Any]]:
    stmt = select(Tenant).where(Tenant.reseller_id == r.id).order_by(Tenant.created_at.desc()).limit(500)
    if only:
        stmt = stmt.where(Tenant.id == only)
    tenants = db.scalars(stmt).all()
    ids = [t.id for t in tenants]
    links = {c.tenant_id: c for c in db.scalars(select(ResellerClient).where(ResellerClient.tenant_id.in_(ids)))} if ids else {}
    pkgs = {p.id: p for p in db.scalars(select(ResellerPackage).where(ResellerPackage.reseller_id == r.id))}
    quotas: dict[uuid.UUID, list[TenantQuota]] = {}
    for q in db.scalars(select(TenantQuota).where(TenantQuota.tenant_id.in_(ids))) if ids else []:
        quotas.setdefault(q.tenant_id, []).append(q)
    return [client_dto(db, t, links.get(t.id), pkgs.get(links[t.id].package_id) if t.id in links and links[t.id].package_id else None, quotas.get(t.id, [])) for t in tenants]


@router.get("/reseller/clients")
def list_clients(ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    return {"items": _client_rows(db, svc.own_reseller(db, ctx))}


@router.post("/reseller/clients", status_code=201)
def create_client(body: ClientIn, request: Request, ctx: Ctx = Depends(require("clients:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    pkg = svc.own_package(db, r, body.package_id) if body.package_id else None
    t = svc.create_client(db, ctx, r, body.name, str(body.contact_email), pkg, body.notes)
    db.commit()
    return _client_rows(db, r, t.id)[0]


@router.get("/reseller/clients/{tid}")
def get_client(tid: uuid.UUID, ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    svc.own_client(db, r, tid)
    return _client_rows(db, r, tid)[0]


@router.patch("/reseller/clients/{tid}")
def update_client(tid: uuid.UUID, body: ClientPatch, request: Request, ctx: Ctx = Depends(require("clients:write")), db: Session = Depends(get_db)):
    from datetime import datetime

    r = svc.own_reseller(db, ctx)
    t = svc.own_client(db, r, tid)
    link = db.scalar(select(ResellerClient).where(ResellerClient.tenant_id == t.id))
    if body.name:
        t.name = body.name.strip()
    if body.contact_email:
        t.contact_email = str(body.contact_email).lower()
    if link:
        if body.notes is not None:
            link.notes = body.notes
        if body.renews_at:
            try:
                link.renews_at = datetime.fromisoformat(body.renews_at)
            except ValueError:
                raise api_error(422, "validation_error", "renews_at must be an ISO date.") from None
    log_activity(db, t.id, ctx.user.id, "client.updated", "Client details updated by reseller")
    audit(db, "reseller.client_updated", actor=ctx.user.id, tenant_id=t.id, target_type="tenant", target_id=t.id, ip=client_ip(request))
    db.commit()
    return _client_rows(db, r, tid)[0]


class AssignIn(BaseModel):
    package_id: uuid.UUID


@router.post("/reseller/clients/{tid}/package")
def assign_package(tid: uuid.UUID, body: AssignIn, request: Request, ctx: Ctx = Depends(require("clients:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    t = svc.own_client(db, r, tid)
    p = svc.own_package(db, r, body.package_id)
    svc.assign_package(db, ctx, r, t, p)
    db.commit()
    return _client_rows(db, r, tid)[0]


@router.get("/reseller/clients/{tid}/support-context")
def support_context(tid: uuid.UUID, ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    """What a reseller needs to answer a client: status, package, quotas, recent activity, invoice standing. No credentials or file data."""
    r = svc.own_reseller(db, ctx)
    t = svc.own_client(db, r, tid)
    acts = db.scalars(select(TenantActivity).where(TenantActivity.tenant_id == t.id).order_by(TenantActivity.created_at.desc()).limit(10)).all()
    open_inv = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.tenant_id == t.id, Invoice.status == "open")) or 0
    return {
        "client": _client_rows(db, r, tid)[0], "open_invoices": open_inv,
        "recent_activity": [{"action": a.action, "summary": a.summary, "created_at": a.created_at} for a in acts],
        "tickets": "Open the Support module to see tickets raised by this client.",
    }


# ---- white label
class BrandingIn(BaseModel):
    portal_name: str | None = Field(default=None, max_length=60)
    logo_url: str | None = Field(default=None, max_length=500, pattern=r"^https://[^\s<>\"']+$")
    primary_color: str | None = Field(default=None, pattern=HEX)
    accent_color: str | None = Field(default=None, pattern=HEX)
    support_email: EmailStr | None = None
    email_from_name: str | None = Field(default=None, max_length=80)
    email_footer: str | None = Field(default=None, max_length=300)


def branding_dto(r: Reseller) -> dict[str, Any]:
    b = r.branding or {}
    return {**{k: b.get(k) for k in ("portal_name", "logo_url", "primary_color", "accent_color", "support_email", "email_from_name", "email_footer")}, "disclosure_text": svc.PLATFORM_DISCLOSURE, "email_branding_note": "Branded sender name/footer are applied to emails; delivery still uses the platform mail server."}


@router.get("/reseller/branding")
def get_branding(ctx: Ctx = Depends(require("branding:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    return {"branding": branding_dto(r), "domains": [svc.domain_dto(d) for d in db.scalars(select(ResellerDomain).where(ResellerDomain.reseller_id == r.id).order_by(ResellerDomain.created_at))]}


@router.put("/reseller/branding")
def put_branding(body: BrandingIn, request: Request, ctx: Ctx = Depends(require("branding:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    r.branding = {**(r.branding or {}), **{k: (str(v) if v is not None else None) for k, v in body.model_dump(exclude_unset=True).items()}}
    audit(db, "reseller.branding_updated", actor=ctx.user.id, target_type="reseller", target_id=r.id, ip=client_ip(request))
    db.commit()
    return branding_dto(r)


class DomainIn(BaseModel):
    domain: str = Field(min_length=4, max_length=253)


@router.post("/reseller/domains", status_code=201)
def add_domain(body: DomainIn, request: Request, ctx: Ctx = Depends(require("branding:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    d = svc.new_domain(db, r, body.domain)
    audit(db, "reseller.domain_added", actor=ctx.user.id, target_type="reseller_domain", target_id=d.id, detail={"domain": d.domain}, ip=client_ip(request))
    db.commit()
    return svc.domain_dto(d)


def _own_domain(db: Session, r: Reseller, did: uuid.UUID) -> ResellerDomain:
    d = db.get(ResellerDomain, did)
    if d is None or d.reseller_id != r.id:
        raise api_error(404, "not_found", "Domain not found.")
    return d


@router.post("/reseller/domains/{did}/verify")
def verify_domain(did: uuid.UUID, ctx: Ctx = Depends(require("branding:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    ratelimit.enforce(f"domain_verify:{r.id}", 20, 3600)
    d = svc.verify_domain(db, _own_domain(db, r, did))
    audit(db, "reseller.domain_checked", actor=ctx.user.id, target_type="reseller_domain", target_id=d.id, detail={"state": d.state})
    db.commit()
    return svc.domain_dto(d)


@router.delete("/reseller/domains/{did}")
def remove_domain(did: uuid.UUID, ctx: Ctx = Depends(require("branding:write")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    d = _own_domain(db, r, did)
    db.delete(d)
    audit(db, "reseller.domain_removed", actor=ctx.user.id, target_type="reseller_domain", target_id=did)
    db.commit()
    return {"status": "ok"}


@router.get("/branding/by-host")
def branding_by_host(request: Request, host: str = Query(min_length=4, max_length=253), db: Session = Depends(get_db)):
    """Public. Only VERIFIED domains resolve, and only presentation fields are returned (no client or account data)."""
    ratelimit.enforce(f"branding_host:{client_ip(request)}", 120, 60)
    d = db.scalar(select(ResellerDomain).where(ResellerDomain.domain == host.lower().split(":")[0], ResellerDomain.state == "verified"))
    r = db.get(Reseller, d.reseller_id) if d else None
    if r is None or r.status != "approved":
        raise api_error(404, "not_found", "No branding for this host.")
    return branding_dto(r) | {"support_email": (r.branding or {}).get("support_email")}


@router.get("/branding/effective")
def effective_branding(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Branding a signed-in tenant user sees: their own reseller's, never another's."""
    rid = ctx.reseller_id
    if ctx.tenant_id and ctx.scope == "tenant":
        rid = db.scalar(select(Tenant.reseller_id).where(Tenant.id == ctx.tenant_id))
    r = db.get(Reseller, rid) if rid else None
    if r is None or r.status != "approved":
        return {"branding": None, "disclosure_text": None}
    return {"branding": branding_dto(r), "disclosure_text": svc.PLATFORM_DISCLOSURE}


# ---- commissions + reports
@router.get("/reseller/commissions")
def my_commissions(status: str | None = None, ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    stmt = select(CommissionEntry).where(CommissionEntry.reseller_id == r.id).order_by(CommissionEntry.created_at.desc()).limit(500)
    if status:
        stmt = stmt.where(CommissionEntry.status == status)
    return {"balances": svc.ledger_balances(db, r.id), "items": [svc.entry_dto(e) for e in db.scalars(stmt)]}


@router.get("/reseller/commissions/reconciliation")
def my_reconciliation(ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    return svc.reconcile(db, svc.own_reseller(db, ctx).id)


@router.get("/reseller/commissions/export.csv")
def export_commissions(ctx: Ctx = Depends(require("commissions:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    rows = db.scalars(select(CommissionEntry).where(CommissionEntry.reseller_id == r.id).order_by(CommissionEntry.created_at)).all()
    return csv_response("commissions.csv", ["created_at", "kind", "status", "amount_inr", "rate_percent", "base_inr", "client_id", "invoice_id", "payout_reference"], [
        [e.created_at.isoformat(), e.kind, e.status, f"{e.amount_paise / 100:.2f}", (e.rate_bps or 0) / 100, f"{(e.base_paise or 0) / 100:.2f}", e.tenant_id, e.invoice_id, e.payout_reference] for e in rows])


@router.get("/reseller/reports/summary")
def summary(ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    by_status = dict(db.execute(select(Tenant.status, func.count()).where(Tenant.reseller_id == r.id).group_by(Tenant.status)).all())
    billed = db.scalar(select(func.coalesce(func.sum(Invoice.taxable_paise), 0)).join(Tenant, Tenant.id == Invoice.tenant_id).where(Tenant.reseller_id == r.id, Invoice.status == "paid")) or 0
    pkgs = db.scalar(select(func.count()).select_from(ResellerPackage).where(ResellerPackage.reseller_id == r.id, ResellerPackage.active.is_(True))) or 0
    return {"clients_by_status": by_status, "clients_total": sum(by_status.values()), "active_packages": pkgs, "paid_invoices_ex_tax_paise": int(billed), "balances": svc.ledger_balances(db, r.id), "allocation": svc.allocation(r), "committed": svc.committed(db, r.id), "used": svc.used(db, r.id)}


@router.get("/reseller/reports/clients.csv")
def export_clients(ctx: Ctx = Depends(require("clients:read")), db: Session = Depends(get_db)):
    r = svc.own_reseller(db, ctx)
    rows = _client_rows(db, r)
    return csv_response("clients.csv", ["name", "status", "contact_email", "package", "retail_price_inr", "renews_at", "created_at"], [
        [c["name"], c["status"], c["contact_email"], c["package"], f"{c['retail_price_paise'] / 100:.2f}" if c["retail_price_paise"] is not None else "", c["renews_at"].isoformat() if c["renews_at"] else "", c["created_at"].isoformat()] for c in rows])
