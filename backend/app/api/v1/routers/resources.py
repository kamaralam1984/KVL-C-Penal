import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, get_ctx, require, resolve_tenant
from app.models.ownership import ResourceRef
from app.models.tenant import Tenant
from app.services.audit import audit
from app.services.ownership import norm_ref

router = APIRouter(prefix="/resources", tags=["resources"])
_PERM = {"website": "websites", "domain": "domains", "database": "databases"}
_REL = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$"


class RefIn(BaseModel):
    tenant_id: uuid.UUID
    kind: Literal["website", "domain", "database"]
    ref: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    label: str | None = Field(None, max_length=200)
    server_id: uuid.UUID | None = None
    root_rel: str | None = Field(None, pattern=_REL)
    config_rel: str | None = Field(None, pattern=_REL)
    dump_rel: str | None = Field(None, pattern=_REL)
    attrs: dict[str, Any] | None = None

    @field_validator("root_rel", "config_rel", "dump_rel")
    @classmethod
    def _nodots(cls, v):
        if v and ".." in v.split("/"):
            raise ValueError("invalid path")
        return v


def view(r: ResourceRef) -> dict[str, Any]:
    return {"id": str(r.id), "tenant_id": str(r.tenant_id), "kind": r.kind, "ref": r.ref, "label": r.label, "server_id": str(r.server_id) if r.server_id else None,
            "root_rel": r.root_rel, "has_config": bool(r.config_rel), "has_dump": bool(r.dump_rel), "created_at": r.created_at}


@router.get("")
def list_refs(kind: Literal["website", "domain", "database"] | None = None, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    kinds = [kind] if kind else list(_PERM)
    allowed = [k for k in kinds if ctx.can(f"{_PERM[k]}:read")]
    if not allowed:
        raise api_error(403, "forbidden", "You do not have permission to perform this action.")
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(ResourceRef).where(ResourceRef.kind.in_(allowed))
    if tid:
        stmt = stmt.where(ResourceRef.tenant_id == tid)
    rows = db.scalars(stmt.order_by(ResourceRef.kind, ResourceRef.ref).limit(500)).all()
    return {"items": [view(r) for r in rows]}


@router.post("", status_code=201)
def register(body: RefIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Platform staff register what a tenant owns and where it lives on the server. Interim until Phases 6-8 are wired in."""
    perm = f"{_PERM[body.kind]}:manage"
    if not ctx.is_platform or not ctx.can(perm):
        raise api_error(403, "forbidden", "You do not have permission to perform this action.")
    if db.get(Tenant, body.tenant_id) is None:
        raise api_error(404, "not_found", "Tenant not found.")
    ref = norm_ref(body.kind, body.ref)
    if db.scalar(select(ResourceRef.id).where(ResourceRef.kind == body.kind, ResourceRef.ref == ref)):
        raise api_error(409, "already_registered", "That resource is already registered.")
    r = ResourceRef(tenant_id=body.tenant_id, kind=body.kind, ref=ref, label=body.label, server_id=body.server_id, root_rel=body.root_rel, config_rel=body.config_rel, dump_rel=body.dump_rel, attrs=body.attrs)
    db.add(r)
    db.flush()
    audit(db, "resource.registered", actor=ctx.user.id, tenant_id=body.tenant_id, target_type=body.kind, target_id=r.id, detail={"ref": ref}, ip=client_ip(request))
    db.commit()
    return view(r)
