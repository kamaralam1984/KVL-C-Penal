import importlib
import logging
import pkgutil
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import routers as routers_pkg
from app.core.config import get_settings
from app.core.deps import csrf_ok
from app.core.observability import install as install_observability

log = logging.getLogger("kvl")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="KVL Hosting Panel API",
        version="1.0.0",
        description="Control plane for KVL private hosting. Errors use {error:{code,message,request_id}}.",
        docs_url="/api/docs" if s.is_dev else None,
        openapi_url="/api/openapi.json" if s.is_dev else None,
    )
    install_observability(app)  # Phase 17: JSON logs, metrics, /readyz, /metrics, OpenAPI polish
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key", "Authorization"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        if not csrf_ok(request):
            resp = JSONResponse(
                status_code=403,
                content={"error": {"code": "csrf_failed", "message": "Missing or invalid CSRF token.", "request_id": rid}},
            )
        else:
            resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not s.is_dev:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    def err(request: Request, status: int, code: str, message: str, extra: dict | None = None, headers=None):
        body = {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **(extra or {})}}
        return JSONResponse(status_code=status, content=body, headers=headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        d = exc.detail
        if isinstance(d, dict):
            extra = {k: v for k, v in d.items() if k not in {"code", "message"}}
            return err(request, exc.status_code, d.get("code", "error"), d.get("message", ""), extra, exc.headers)
        return err(request, exc.status_code, "not_found" if exc.status_code == 404 else "error", str(d), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return err(request, 422, "validation_error", "Request validation failed.", {"fields": fields})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error rid=%s", getattr(request.state, "request_id", None))
        return err(request, 500, "internal_error", "Something went wrong.")

    @app.get("/healthz", tags=["system"])
    def healthz():
        return {"status": "ok"}

    # Every module in app/api/v1/routers that exposes `router` is mounted under /api/v1.
    for m in pkgutil.iter_modules(routers_pkg.__path__):
        mod = importlib.import_module(f"{routers_pkg.__name__}.{m.name}")
        if hasattr(mod, "router"):
            app.include_router(mod.router, prefix="/api/v1")
    from app.services.wiring import wire

    wire()
    return app


app = create_app()
