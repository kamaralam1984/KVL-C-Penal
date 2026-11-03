"""Structured JSON logging with redaction, request/correlation ids, in-process Prometheus metrics, readiness, error reporting, OpenAPI polish.

Installed once from app.main via `install(app)`. No external dependencies.
"""
import hmac
import json
import logging
import re
import sys
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
log = logging.getLogger("kvl")
access_log = logging.getLogger("kvl.access")

# ------------------------------------------------------------------ redaction
_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "authorization", "cookie", "api_key", "apikey", "otp", "signing", "refresh", "csrf", "key_hash")
_RULES: list[tuple[re.Pattern, Any]] = [
    (re.compile(r"(?i)\b(set-cookie|cookie)\b(\"?\s*[:=]\s*)[^\r\n\"]*"), r"\1\2[redacted]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]+"), "Bearer [redacted]"),
    (re.compile(r"\bkvl_[A-Za-z0-9]{6,}_[A-Za-z0-9_\-]{16,}"), "kvl_[redacted]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9_\-]{16,}"), "whsec_[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*"), "[redacted-jwt]"),
    (re.compile(r"\bgAAAA[A-Za-z0-9_\-=]{20,}"), "[redacted-fernet]"),
    (re.compile(r"(?i)\b(kvl_at|kvl_rt|kvl_csrf)=[^\s;,]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)(\"?(?:password|passwd|pwd|secret|signing_secret|client_secret|token|access_token|refresh_token|api[_-]?key|authorization|otp)\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s\"',;&}]+)"), lambda m: m.group(1) + (f"{m.group(2)[0]}[redacted]{m.group(2)[0]}" if m.group(2)[0] in "\"'" else "[redacted]")),
]


def redact(text_: str) -> str:
    for pat, rep in _RULES:
        text_ = pat.sub(rep, text_)
    return text_


def redact_obj(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: ("[redacted]" if isinstance(k, str) and any(s in k.lower() for s in _SENSITIVE_KEYS) else redact_obj(x)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_obj(x) for x in v]
    if isinstance(v, str):
        return redact(v)
    return v


_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname, "logger": record.name, "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                data[k] = v
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(redact_obj(data), default=str))


class RedactingFilter(logging.Filter):
    """For non-JSON handlers: scrub the rendered message in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


_HANDLER_MARK = "_kvl_json_handler"


def configure_logging(stream=None) -> logging.Handler:
    """Attach (once) a JSON, redacting handler to the root logger and return it."""
    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, _HANDLER_MARK, False):
            return h
    h = logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(JsonFormatter() if get_settings().log_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    h.addFilter(RedactingFilter())
    setattr(h, _HANDLER_MARK, True)
    root.addHandler(h)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    return h


# ------------------------------------------------------------------ metrics
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self.hist: dict[tuple, list[float]] = {}  # (method, route) -> [bucket counts..., sum, count]
        self.in_flight = 0

    def inc(self, name: str, labels: dict[str, str] | None = None, n: float = 1) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted((labels or {}).items())))] += n

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.counters[("kvl_http_requests_total", (("method", method), ("route", route), ("status", str(status))))] += 1
            h = self.hist.setdefault((method, route), [0.0] * (len(BUCKETS) + 2))
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
            h[-2] += seconds
            h[-1] += 1

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.hist.clear()

    def render(self) -> str:
        env = get_settings().env
        out = ["# HELP kvl_app_info Build information.", "# TYPE kvl_app_info gauge", f'kvl_app_info{{env="{_esc(env)}",version="1.0.0"}} 1',
               "# HELP kvl_http_requests_in_flight In-flight HTTP requests.", "# TYPE kvl_http_requests_in_flight gauge", f"kvl_http_requests_in_flight {self.in_flight}"]
        with self._lock:
            names = sorted({n for n, _ in self.counters})
            for n in names:
                out += [f"# HELP {n} Counter.", f"# TYPE {n} counter"]
                for (nm, labels), v in sorted(self.counters.items()):
                    if nm == n:
                        lab = ",".join(f'{k}="{_esc(x)}"' for k, x in labels)
                        out.append(f"{n}{{{lab}}} {v:g}")
            out += ["# HELP kvl_http_request_duration_seconds Request latency.", "# TYPE kvl_http_request_duration_seconds histogram"]
            for (method, route), h in sorted(self.hist.items()):
                base = f'method="{_esc(method)}",route="{_esc(route)}"'
                for i, b in enumerate(BUCKETS):
                    out.append(f'kvl_http_request_duration_seconds_bucket{{{base},le="{b:g}"}} {h[i]:g}')
                out.append(f'kvl_http_request_duration_seconds_bucket{{{base},le="+Inf"}} {h[-1]:g}')
                out.append(f"kvl_http_request_duration_seconds_sum{{{base}}} {h[-2]:.6f}")
                out.append(f"kvl_http_request_duration_seconds_count{{{base}}} {h[-1]:g}")
        return "\n".join(out) + "\n"


metrics = Metrics()

# ------------------------------------------------------------------ error reporting hook
_reporters: list[Callable[[BaseException, dict], None]] = []


def register_error_reporter(fn: Callable[[BaseException, dict], None]) -> None:
    """Plug in Sentry/GlitchTip/etc.: fn(exc, context). Exceptions in reporters are swallowed. None are registered by default."""
    _reporters.append(fn)


def report_error(exc: BaseException, context: dict | None = None) -> None:
    ctx = {"request_id": request_id_var.get(), **(context or {})}
    log.error("unhandled exception %s", type(exc).__name__, exc_info=exc, extra={"context": ctx})
    metrics.inc("kvl_unhandled_errors_total", {"type": type(exc).__name__})
    for r in _reporters:
        try:
            r(exc, ctx)
        except Exception:  # noqa: BLE001
            log.exception("error reporter failed")


# ------------------------------------------------------------------ readiness
def check_ready() -> tuple[bool, dict[str, str]]:
    from app.core.db import engine

    checks = {}
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "unavailable"
    try:
        import redis

        redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5).ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        checks["redis"] = "unavailable"
    return all(v == "ok" for v in checks.values()), checks


ESCALATION_CODES = {"privilege_escalation", "self_assignment"}
DOCS_PREFIXES = ("/api/docs", "/api/openapi.json")


def _record_escalation(request_id: str | None, ctx, code: str, path: str, ip: str | None) -> None:
    from app.core.db import SessionLocal
    from app.services.audit import audit
    from app.services.security_events import record_event

    uid = ctx.user.id if ctx else None
    with SessionLocal() as db:
        audit(db, "security.privilege_escalation_attempt", actor=uid, tenant_id=getattr(ctx, "tenant_id", None), detail={"code": code, "path": path}, ip=ip, request_id=request_id)
        record_event(db, "privilege_escalation_attempt", "high", f"Blocked privilege-escalation attempt ({code}) on {path}.", tenant_id=getattr(ctx, "tenant_id", None), user_id=uid, ip=ip,
                     detail={"code": code, "path": path})
        db.commit()


def install(app: FastAPI) -> None:
    """Call right after creating the app, before other middleware, so this runs innermost (after request-id assignment)."""
    configure_logging()

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = getattr(request.state, "request_id", None) or request.headers.get("x-request-id")
        token = request_id_var.set(rid)
        start = time.perf_counter()
        metrics.in_flight += 1
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            if status == 403 and "json" in resp.headers.get("content-type", ""):
                body = b"".join([chunk async for chunk in resp.body_iterator])  # type: ignore[attr-defined]
                try:
                    code = json.loads(body).get("error", {}).get("code")
                except ValueError:
                    code = None
                headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
                resp = Response(content=body, status_code=403, headers=headers, media_type="application/json")
                if code in ESCALATION_CODES:
                    await run_in_threadpool(_record_escalation, rid, getattr(request.state, "ctx", None), code, request.url.path, request.client.host if request.client else None)
            if not request.url.path.startswith(DOCS_PREFIXES):
                resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            return resp
        except Exception as exc:
            report_error(exc, {"method": request.method, "path": request.url.path})
            raise
        finally:
            metrics.in_flight -= 1
            dur = time.perf_counter() - start
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            if route != "unmatched" and not route.startswith("/api/") and request.url.path.startswith("/api/v1/"):
                route = "/api/v1" + route  # included routers report prefix-relative paths
            metrics.observe(request.method, route, status, dur)
            ctx = getattr(request.state, "ctx", None)
            access_log.info("request", extra={"method": request.method, "path": request.url.path, "route": route, "status": status, "duration_ms": round(dur * 1000, 1),
                                              "user_id": str(ctx.user.id) if ctx else None})  # query strings are never logged
            request_id_var.reset(token)

    @app.get("/readyz", tags=["system"], summary="Readiness probe (database and Redis)")
    def readyz():
        ok, checks = check_ready()
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})

    @app.get("/metrics", tags=["system"], include_in_schema=False)
    def metrics_endpoint(request: Request):
        from app.core.db import SessionLocal
        from app.core.deps import get_ctx

        s = get_settings()
        auth = request.headers.get("authorization", "")
        if s.metrics_token and auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), s.metrics_token):
            return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
        with SessionLocal() as db:
            try:
                ctx = get_ctx(request, db)
            except HTTPException:
                raise HTTPException(status_code=401, detail={"code": "unauthenticated", "message": "Metrics require a scrape token or security:read."}) from None
            if not ctx.is_platform or not ctx.can("security:read"):
                raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Metrics require security:read."})
        return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")

    _polish_openapi(app)


TAGS = [
    {"name": "auth", "description": "Sign-in, sessions, MFA and password flows (cookie sessions)."},
    {"name": "tenants", "description": "Tenant lifecycle, quotas and activity."},
    {"name": "rbac", "description": "Roles, permissions, users and memberships."},
    {"name": "support", "description": "Tickets, attachments, departments, SLA and support reports."},
    {"name": "knowledge-base", "description": "Help articles; drafts are staff-only."},
    {"name": "announcements", "description": "Platform notices with audience targeting and read/ack state."},
    {"name": "staff", "description": "Staff accounts, permissions view, activity and workload."},
    {"name": "api-keys", "description": "Scoped API keys. Secrets are shown once; use `Authorization: Bearer kvl_...`."},
    {"name": "webhooks", "description": "Outbound webhook subscriptions, signing, delivery log and retries."},
    {"name": "developer", "description": "Scope and event catalogs, usage."},
    {"name": "security", "description": "Security events, alert routing, audit viewer, sessions (platform)."},
    {"name": "system", "description": "Health, readiness and metrics."},
]
ERROR_SCHEMA = {"type": "object", "required": ["error"], "properties": {"error": {"type": "object", "required": ["code", "message", "request_id"], "properties": {
    "code": {"type": "string", "example": "forbidden"}, "message": {"type": "string"}, "request_id": {"type": "string"},
    "fields": {"type": "array", "items": {"type": "object", "properties": {"field": {"type": "string"}, "message": {"type": "string"}}}}}}}}


def _polish_openapi(app: FastAPI) -> None:
    from fastapi.openapi.utils import get_openapi

    app.openapi_tags = TAGS
    app.description = (
        "Control plane API for KVL private hosting. Versioned under `/api/v1`.\n\n"
        "**Authentication**: browser cookie session (with `X-CSRF-Token`) or `Authorization: Bearer kvl_...` API key. "
        "**Errors**: `{\"error\": {\"code\", \"message\", \"request_id\", \"fields?\"}}`. **Idempotency**: send `Idempotency-Key` on supported POSTs. "
        "**Rate limits**: 429 with `Retry-After`. See docs/api/overview.md."
    )

    def custom():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes, tags=TAGS)
        schema.setdefault("components", {}).setdefault("schemas", {})["Error"] = ERROR_SCHEMA
        ref = {"description": "Error", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
        for path, ops in schema.get("paths", {}).items():
            if not path.startswith("/api/v1"):
                continue
            for method, op in ops.items():
                if isinstance(op, dict) and "responses" in op:
                    for code in ("401", "403", "404", "429"):
                        op["responses"].setdefault(code, ref)
                    if "422" in op["responses"]:
                        op["responses"]["422"] = ref
        app.openapi_schema = schema
        return schema

    app.openapi = custom  # type: ignore[method-assign]
