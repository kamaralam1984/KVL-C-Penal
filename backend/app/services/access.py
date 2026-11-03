"""Tenant/role-scoped visibility for hosting resources. Anything not visible is reported as 404."""
import uuid
from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.deps import Ctx, api_error
from app.models.tenant import Tenant

T = TypeVar("T")


def scope_stmt(stmt: Select, model, ctx: Ctx, tenant_id: uuid.UUID | None = None) -> Select:
    col = model.tenant_id
    if ctx.is_platform:
        return stmt.where(col == tenant_id) if tenant_id else stmt
    if ctx.scope == "tenant" and ctx.tenant_id:
        if tenant_id and tenant_id != ctx.tenant_id:
            return stmt.where(False)
        return stmt.where(col == ctx.tenant_id)
    if ctx.scope == "reseller" and ctx.reseller_id:
        ids = select(Tenant.id).where(Tenant.reseller_id == ctx.reseller_id)
        stmt = stmt.where(col.in_(ids))
        return stmt.where(col == tenant_id) if tenant_id else stmt
    return stmt.where(False)


def get_visible(db: Session, ctx: Ctx, model, obj_id: uuid.UUID, what: str = "Resource"):
    row = db.scalar(scope_stmt(select(model).where(model.id == obj_id), model, ctx))
    if row is None:
        raise api_error(404, "not_found", f"{what} not found.")
    return row


def tenant_for_create(ctx: Ctx, requested: uuid.UUID | None) -> uuid.UUID:
    from app.core.deps import require_tenant

    return require_tenant(ctx, requested)
