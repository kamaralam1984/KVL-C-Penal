import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, require_tenant
from app.services import dns as svc
from app.services.audit import audit

router = APIRouter(prefix="/cloudflare", tags=["cloudflare"])


class ConnIn(BaseModel):
    token: str = Field(min_length=20, max_length=100, repr=False)
    label: str | None = Field(None, max_length=120)
    tenant_id: uuid.UUID | None = None


@router.get("/connection")
def connection(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    return svc.connection_view(svc.get_connection(db, require_tenant(ctx, tenant_id)))


@router.put("/connection")
def connect(body: ConnIn, request: Request, ctx: Ctx = Depends(require("dns:manage")), db: Session = Depends(get_db)):
    """Store a scoped API token (encrypted). The token is validated against Cloudflare, never returned, never logged."""
    tid = require_tenant(ctx, body.tenant_id)
    conn = svc.connect(db, tid, body.token, body.label, ctx.user.id)
    db.commit()
    return svc.connection_view(conn)


@router.post("/connection/check")
def check(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:manage")), db: Session = Depends(get_db)):
    conn = svc.get_connection(db, require_tenant(ctx, tenant_id))
    if conn is None:
        raise api_error(409, "not_configured", "Cloudflare is not connected.")
    svc.recheck(db, conn, ctx.user.id)
    db.commit()
    return svc.connection_view(conn)


@router.delete("/connection", status_code=204)
def disconnect(request: Request, tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:manage")), db: Session = Depends(get_db)):
    tid = require_tenant(ctx, tenant_id)
    conn = svc.get_connection(db, tid)
    if conn is None:
        raise api_error(404, "not_found", "No Cloudflare connection.")
    db.delete(conn)
    audit(db, "cloudflare.disconnected", actor=ctx.user.id, tenant_id=tid, target_type="cloudflare", ip=client_ip(request))
    db.commit()


@router.get("/zones")
def zones(tenant_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("dns:read")), db: Session = Depends(get_db)):
    conn = svc.get_connection(db, require_tenant(ctx, tenant_id))
    return {"configured": conn is not None, "items": (conn.zones or []) if conn else []}
