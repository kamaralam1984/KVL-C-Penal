"""Runs one validated, allow-listed operation with a hard timeout, output limits and secret redaction."""
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from kvl_agent import registry
from kvl_agent.context import BinaryNotAllowed, OpContext, PathEscape

_SECRET_KEY = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private|credential|authorization|signature)", re.I)
_SECRET_TEXT = re.compile(r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|authorization)\b\s*[=:]\s*\S+|bearer\s+[A-Za-z0-9._~+/=-]{8,}")


class OperationTimeout(Exception):
    pass


def run_with_timeout(fn, timeout: float):
    """Run fn in a daemon thread. Python cannot kill threads, so a timed-out handler is abandoned (it must
    use ctx.run, whose subprocesses are killed on timeout, for anything long-running)."""
    box: dict[str, Any] = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True, name="kvl-op")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise OperationTimeout()
    if "error" in box:
        raise box["error"]
    return box["value"]


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(k): ("[redacted]" if _SECRET_KEY.search(str(k)) else redact(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub("[redacted]", value)
    return value


def truncate_summary(summary: dict, max_bytes: int) -> dict:
    raw = json.dumps(summary, default=str)
    if len(raw.encode()) <= max_bytes:
        return summary
    return {"truncated": True, "original_bytes": len(raw.encode()), "preview": raw[: max(max_bytes // 2, 0)]}


@dataclass
class JobOutcome:
    status: str  # succeeded | failed | timed_out
    summary: dict
    error: str | None
    duration_ms: int

    def payload(self) -> dict:
        return {"status": self.status, "summary": self.summary, "error": self.error, "duration_ms": self.duration_ms}


def execute_job(job: dict, ctx: OpContext) -> JobOutcome:
    """Authorisation is the allow-list itself: unknown operations and schema-invalid params never reach a handler."""
    started = time.monotonic()

    def done(status: str, summary: dict | None = None, error: str | None = None) -> JobOutcome:
        s = truncate_summary(redact(summary or {}), ctx.max_output)
        return JobOutcome(status, s, (redact(error)[:2000] if error else None), int((time.monotonic() - started) * 1000))

    name = job.get("operation")
    if not isinstance(name, str):
        return done("failed", error="malformed job")
    try:
        op, params = registry.validate(name, job.get("params"))
    except registry.UnknownOperation:
        return done("failed", error=f"operation not allow-listed: {name[:80]}")
    except registry.InvalidParams as e:
        return done("failed", error=f"invalid params: {e}"[:1000])
    server_timeout = job.get("timeout_seconds")
    timeout = min(op.timeout, server_timeout) if isinstance(server_timeout, int) and server_timeout > 0 else op.timeout
    try:
        result = run_with_timeout(lambda: op.handler(params, ctx), timeout)
    except OperationTimeout:
        return done("timed_out", error=f"exceeded {timeout}s")
    except (BinaryNotAllowed, PathEscape) as e:
        return done("failed", error=f"blocked: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        return done("failed", error=f"{type(e).__name__}: {e}"[:500])
    return done("succeeded", result if isinstance(result, dict) else {"value": result})
