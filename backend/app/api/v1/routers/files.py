import base64
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require, require_tenant, resolve_tenant
from app.models.files import FileOperation
from app.models.ownership import ResourceRef
from app.services import filemgr as fm

router = APIRouter(prefix="/files", tags=["files"])
Tid = uuid.UUID | None


class PathIn(BaseModel):
    path: str = Field("", max_length=4096)


class ListIn(PathIn):
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)
    show_hidden: bool = False
    sort: Literal["name", "size", "mtime"] = "name"


class SearchIn(PathIn):
    query: str = Field(min_length=1, max_length=100, pattern=r"^[^/\\\x00-\x1f]+$")
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class UploadIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content_b64: str = Field(max_length=1_500_000)
    upload_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    chunk_index: int = Field(0, ge=0, le=2000)
    total_chunks: int = Field(1, ge=1, le=2000)
    total_size: int = Field(ge=0)
    overwrite: bool = False


class MkdirIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    parents: bool = False


class RenameIn(BaseModel):
    src: str = Field(min_length=1, max_length=4096)
    dst: str = Field(min_length=1, max_length=4096)
    overwrite: bool = False


class DeleteIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    confirm: bool = False


class RestoreIn(BaseModel):
    trash_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    new_path: str | None = Field(None, max_length=4096)


class PurgeIn(BaseModel):
    trash_id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$")
    empty: bool = False
    confirm: bool = False


class ArchiveIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)
    dest: str = Field(min_length=1, max_length=4096)
    format: Literal["zip", "tar.gz"] = "zip"


class ExtractIn(BaseModel):
    archive: str = Field(min_length=1, max_length=4096)
    dest_dir: str = Field("", max_length=4096)
    overwrite: bool = False


def _accepted(fop, job) -> dict[str, Any]:
    return fm.job_view(fop, job)


def _run(db, ctx, request, tid, site, op, agent_op, params, *, path=None, detail=None, reserved=0, audit_action=None):
    fop, job = fm.queue(db, tenant_id=tid, actor=ctx.user.id, site=site, op=op, agent_op=agent_op, params=params, path=path, detail=detail, reserved=reserved, audit_action=audit_action, ip=client_ip(request))
    db.commit()
    return Response(status_code=202, content=__import__("json").dumps(_accepted(fop, job), default=str), media_type="application/json")


@router.get("/sites")
def sites(tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(ResourceRef).where(ResourceRef.kind == "website")
    if tid:
        stmt = stmt.where(ResourceRef.tenant_id == tid)
    rows = db.scalars(stmt.order_by(ResourceRef.ref).limit(500)).all()
    return {"items": [{"ref": r.ref, "label": r.label or r.ref, "configured": bool(r.root_rel), "tenant_id": str(r.tenant_id)} for r in rows], "quota": fm.quota_view(db, tid)}


@router.get("/quota")
def quota(tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    return fm.quota_view(db, resolve_tenant(ctx, tenant_id))


@router.get("/history")
def history(site: str | None = None, tenant_id: Tid = None, limit: int = 50, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    tid = resolve_tenant(ctx, tenant_id)
    stmt = select(FileOperation)
    if tid:
        stmt = stmt.where(FileOperation.tenant_id == tid)
    if site:
        stmt = stmt.where(FileOperation.site_ref == site)
    rows = db.scalars(stmt.order_by(FileOperation.created_at.desc()).limit(min(limit, 200))).all()
    return {"items": [{"id": str(r.id), "op": r.op, "path": r.path, "site": r.site_ref, "actor_user_id": str(r.actor_user_id) if r.actor_user_id else None, "created_at": r.created_at} for r in rows]}


def _op(db: Session, ctx: Ctx, oid: uuid.UUID) -> FileOperation:
    f = db.get(FileOperation, oid)
    if f is None or (not ctx.is_platform and f.tenant_id != ctx.tenant_id):
        raise api_error(404, "not_found", "Operation not found.")
    return f


@router.get("/jobs/{oid}")
def job(oid: uuid.UUID, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    f = _op(db, ctx, oid)
    j = fm.settle(db, f)
    db.commit()
    return fm.job_view(f, j)


@router.get("/jobs/{oid}/download")
def download(oid: uuid.UUID, request: Request, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    f = _op(db, ctx, oid)
    j = fm.settle(db, f)
    if f.op != "read" or j is None or j.status != "succeeded" or not (j.result or {}).get("content_b64") or j.result["content_b64"] == "[delivered]":
        raise api_error(409, "not_ready", "Download is not ready or was already delivered.")
    data = base64.b64decode(j.result["content_b64"])
    name = "".join(c for c in (j.result.get("name") or "download") if c.isalnum() or c in "._- ")[:100] or "download"
    j.result = {**j.result, "content_b64": "[delivered]"}  # single-use: do not keep file content in the queue table
    from app.services.audit import audit

    audit(db, "files.download", actor=ctx.user.id, tenant_id=f.tenant_id, target_type="website", target_id=f.site_ref, detail={"path": f.path, "size": len(data)}, ip=client_ip(request))
    db.commit()
    return Response(content=data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})


@router.post("/{site}/list", status_code=202)
def list_dir(site: str, body: ListIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "list", "files.list", {**body.model_dump(), "path": fm.safe_rel(body.path)}, path=body.path)


@router.post("/{site}/stat", status_code=202)
def stat(site: str, body: PathIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "stat", "files.stat", {"path": fm.safe_rel(body.path)}, path=body.path)


@router.post("/{site}/read", status_code=202)
def read(site: str, body: PathIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    if not body.path:
        raise api_error(422, "invalid_path", "A file path is required.")
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "read", "files.read", {"path": fm.safe_rel(body.path)}, path=body.path)


@router.post("/{site}/search", status_code=202)
def search(site: str, body: SearchIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "search", "files.search", {**body.model_dump(), "path": fm.safe_rel(body.path)}, path=body.path)


@router.post("/{site}/trash/list", status_code=202)
def trash_list(site: str, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:read")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "trash_list", "files.trash_list", {})


@router.post("/{site}/upload", status_code=202)
def upload(site: str, body: UploadIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    tid = require_tenant(ctx, tenant_id)
    path = fm.safe_rel(body.path)
    if not path:
        raise api_error(422, "invalid_path", "A file name is required.")
    if body.total_size > fm.MAX_UPLOAD_BYTES:
        raise api_error(413, "upload_too_large", f"Uploads are limited to {fm.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if ext in fm.BLOCKED_EXT:
        raise api_error(415, "file_type_blocked", f"Files of type '{ext}' are not allowed.")
    if body.chunk_index >= body.total_chunks:
        raise api_error(422, "invalid_chunk", "chunk_index is out of range.")
    reserved = 0
    if body.chunk_index == 0:
        fm.reserve(db, tid, body.total_size)  # disk_mb quota; 409 quota_exceeded
        reserved = body.total_size
    else:
        started = db.scalar(select(FileOperation.id).where(FileOperation.tenant_id == tid, FileOperation.op == "write", FileOperation.detail["upload_id"].astext == body.upload_id, FileOperation.reserved_bytes >= 0, FileOperation.path == path))
        if started is None:
            raise api_error(409, "upload_not_started", "Start the upload with chunk 0 first.")
    params = body.model_dump()
    params["path"] = path
    return _run(db, ctx, request, tid, site, "write", "files.write", params, path=path, detail={"upload_id": body.upload_id, "chunk": body.chunk_index, "of": body.total_chunks}, reserved=reserved, audit_action="files.upload" if body.chunk_index == body.total_chunks - 1 else None)


@router.post("/{site}/mkdir", status_code=202)
def mkdir(site: str, body: MkdirIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "mkdir", "files.mkdir", {"path": fm.safe_rel(body.path), "parents": body.parents}, path=body.path, audit_action="files.mkdir")


@router.post("/{site}/rename", status_code=202)
def rename(site: str, body: RenameIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "rename", "files.rename", {"src": fm.safe_rel(body.src), "dst": fm.safe_rel(body.dst), "overwrite": body.overwrite}, path=body.src, detail={"dst": body.dst}, audit_action="files.rename")


@router.post("/{site}/delete", status_code=202)
def delete(site: str, body: DeleteIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:delete")), db: Session = Depends(get_db)):
    """Moves to the site trash (recoverable). Requires explicit confirmation."""
    if not body.confirm:
        raise api_error(400, "confirmation_required", "Set confirm=true to delete.")
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "delete", "files.delete", {"path": fm.safe_rel(body.path), "confirm": True}, path=body.path, audit_action="files.delete")


@router.post("/{site}/restore", status_code=202)
def restore(site: str, body: RestoreIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    p = {"trash_id": body.trash_id, **({"new_path": fm.safe_rel(body.new_path)} if body.new_path else {})}
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "restore", "files.restore", p, path=body.trash_id, audit_action="files.restore")


@router.post("/{site}/purge", status_code=202)
def purge(site: str, body: PurgeIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:delete")), db: Session = Depends(get_db)):
    if not body.confirm:
        raise api_error(400, "confirmation_required", "Set confirm=true to permanently delete.")
    if bool(body.trash_id) == bool(body.empty):
        raise api_error(422, "validation_error", "Specify exactly one of trash_id or empty.")
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "purge", "files.purge", {"trash_id": body.trash_id, "empty": body.empty, "confirm": True}, path=body.trash_id or "*", audit_action="files.purge")


@router.post("/{site}/archive", status_code=202)
def archive(site: str, body: ArchiveIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "archive", "files.archive", {"paths": [fm.safe_rel(p) for p in body.paths], "dest": fm.safe_rel(body.dest), "format": body.format}, path=body.dest, audit_action="files.archive")


@router.post("/{site}/extract", status_code=202)
def extract(site: str, body: ExtractIn, request: Request, tenant_id: Tid = None, ctx: Ctx = Depends(require("files:write")), db: Session = Depends(get_db)):
    return _run(db, ctx, request, require_tenant(ctx, tenant_id), site, "extract", "files.extract", {"archive": fm.safe_rel(body.archive), "dest_dir": fm.safe_rel(body.dest_dir), "overwrite": body.overwrite}, path=body.archive, audit_action="files.extract")
