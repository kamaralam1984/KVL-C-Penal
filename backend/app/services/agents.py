"""Agent identity, request authentication, job state machine and result sanitising."""
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.core.security import decrypt_secret, encrypt_secret, hmac_matches, hmac_sha256, new_token, sha256
from app.models.agents import AgentCredential, AgentNonce, EnrollmentToken
from app.models.infra import AgentJob, Server
from app.services.audit import audit

TIMESTAMP_WINDOW = 300  # seconds either side of server time
NONCE_RETENTION = TIMESTAMP_WINDOW * 2 + 60
ENROLL_TTL_HOURS = 24
RESULT_MAX_BYTES = 16 * 1024
ERROR_MAX_CHARS = 2000
TIMEOUT_GRACE_SECONDS = 30
MAX_CONCURRENT_CLAIM = 5

# queued -> running -> terminal. A job can be cancelled until it is terminal.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "timed_out", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "timed_out": set(),
    "cancelled": set(),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- redaction
_SECRET_KEY = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private|credential|authorization|cookie|signature)", re.I)
_SECRET_TEXT = [
    re.compile(r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|authorization)\b\s*[=:]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
]


def redact_text(text: str) -> str:
    for rx in _SECRET_TEXT:
        text = rx.sub("[redacted]", text)
    return text


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(k)[:80]: ("[redacted]" if _SECRET_KEY.search(str(k)) else redact(v, depth + 1)) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:100]]
    if isinstance(value, str):
        return redact_text(value)[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value))[:200]


def sanitize_result(summary: Any) -> dict[str, Any]:
    """Redact secrets and enforce a hard size limit on what is persisted from an agent."""
    clean = redact(summary if isinstance(summary, dict) else {"value": summary})
    raw = json.dumps(clean, default=str)
    if len(raw.encode()) <= RESULT_MAX_BYTES:
        return clean
    return {"truncated": True, "preview": raw[: RESULT_MAX_BYTES // 2]}


def sanitize_error(msg: str | None) -> str | None:
    return redact_text(msg)[:ERROR_MAX_CHARS] if msg else None


# ---------------------------------------------------------------- signatures
def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, timestamp, nonce, body_hash(body)])


def sign_request(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return hmac_sha256(secret, canonical(method, path, timestamp, nonce, body))


def sign_response(secret: str, request_nonce: str, body: bytes) -> str:
    """Lets the agent verify that a job list really came from the holder of its secret (fail closed on mismatch)."""
    return hmac_sha256(secret, f"RESPONSE\n{request_nonce}\n{body_hash(body)}")


# ---------------------------------------------------------------- credentials
@dataclass
class AgentIdentity:
    server: Server
    credential: AgentCredential
    secret: str
    nonce: str


def issue_credential(db: Session, server: Server, actor: uuid.UUID | None) -> tuple[AgentCredential, str]:
    secret = new_token(32)
    cred = AgentCredential(
        server_id=server.id, key_id="kvl_ak_" + secrets.token_hex(8), secret_enc=encrypt_secret(secret), created_by=actor
    )
    db.add(cred)
    db.flush()
    return cred, secret


def revoke_credentials(db: Session, server_id: uuid.UUID) -> int:
    res = db.execute(
        update(AgentCredential)
        .where(AgentCredential.server_id == server_id, AgentCredential.status == "active")
        .values(status="revoked", revoked_at=utcnow())
    )
    return res.rowcount or 0


def create_enrollment_token(db: Session, server: Server, actor: uuid.UUID | None, ttl_hours: int = ENROLL_TTL_HOURS) -> tuple[EnrollmentToken, str]:
    # Only one live token per server: older unused tokens are voided.
    db.execute(
        update(EnrollmentToken)
        .where(EnrollmentToken.server_id == server.id, EnrollmentToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    token = "kvl_enr_" + new_token(32)
    row = EnrollmentToken(server_id=server.id, token_hash=sha256(token), expires_at=utcnow() + timedelta(hours=ttl_hours), created_by=actor)
    db.add(row)
    db.flush()
    return row, token


def redeem_enrollment_token(db: Session, token: str) -> Server:
    """Atomically consume a token (single use). Every failure looks the same to the caller."""
    bad = api_error(401, "invalid_enrollment_token", "Enrollment token is invalid, expired or already used.")
    now = utcnow()
    row = db.execute(
        update(EnrollmentToken)
        .where(EnrollmentToken.token_hash == sha256(token), EnrollmentToken.used_at.is_(None), EnrollmentToken.expires_at > now)
        .values(used_at=now)
        .returning(EnrollmentToken.server_id)
    ).first()
    if row is None:
        raise bad
    server = db.get(Server, row[0])
    if server is None or server.status == "disabled":
        raise bad
    return server


# ---------------------------------------------------------------- request authentication
_UNAUTH = ("invalid_agent_auth", "Agent authentication failed.")


def authenticate_agent(db: Session, *, method: str, path: str, headers: Any, body: bytes, now: datetime | None = None) -> AgentIdentity:
    """Verify an agent request. Any problem -> 401 with one generic message (fail closed, no oracle)."""
    now = now or utcnow()
    key_id = headers.get("x-kvl-key-id", "")
    ts = headers.get("x-kvl-timestamp", "")
    nonce = headers.get("x-kvl-nonce", "")
    sig = headers.get("x-kvl-signature", "")
    if not (key_id and ts and nonce and sig) or len(key_id) > 40 or not (16 <= len(nonce) <= 64) or len(sig) != 64:
        raise api_error(401, *_UNAUTH)
    cred = db.scalar(select(AgentCredential).where(AgentCredential.key_id == key_id))
    secret = decrypt_secret(cred.secret_enc) if cred else None
    if cred is None or secret is None or cred.status != "active":
        raise api_error(401, *_UNAUTH)
    try:
        skew = abs(int(ts) - int(now.timestamp()))
    except ValueError:
        raise api_error(401, *_UNAUTH) from None
    if skew > TIMESTAMP_WINDOW:
        raise api_error(401, "stale_timestamp", "Request timestamp is outside the allowed window.")
    if not hmac_matches(secret, canonical(method, path, ts, nonce, body), sig):
        raise api_error(401, *_UNAUTH)
    # Only a validly signed request may consume a nonce (so unauthenticated callers cannot fill the table).
    ins = db.execute(pg_insert(AgentNonce).values(key_id=key_id, nonce=nonce).on_conflict_do_nothing(constraint="uq_agent_nonce").returning(AgentNonce.id)).first()
    if ins is None:
        db.rollback()
        raise api_error(401, "replayed_request", "Request nonce was already used.")
    server = db.get(Server, cred.server_id)
    if server is None or server.status == "disabled":
        db.commit()
        raise api_error(403, "server_disabled", "This server is disabled.")
    cred.last_used_at = now
    db.commit()
    return AgentIdentity(server, cred, secret, nonce)


def purge_nonces(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(seconds=NONCE_RETENTION)
    return db.execute(delete(AgentNonce).where(AgentNonce.created_at < cutoff)).rowcount or 0


# ---------------------------------------------------------------- job state machine
def transition(db: Session, job: AgentJob, new: str, *, actor: uuid.UUID | None = None, result: dict | None = None, error: str | None = None, now: datetime | None = None) -> AgentJob:
    if new not in TRANSITIONS.get(job.status, set()):
        raise api_error(409, "invalid_job_state", f"Job is {job.status}; cannot move to {new}.")
    now = now or utcnow()
    prev = job.status
    job.status = new
    if new == "running":
        job.started_at = now
        job.attempts += 1
    else:
        job.finished_at = now
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    action = {"running": "job.started", "cancelled": "job.cancelled"}.get(new, f"job.{new}")
    audit(db, action, actor=actor, tenant_id=job.tenant_id, target_type="job", target_id=job.id, detail={"operation": job.operation, "from": prev, "server_id": str(job.server_id)})
    return job


def claim_jobs(db: Session, server_id: uuid.UUID, limit: int) -> list[AgentJob]:
    """Atomically claim queued jobs; concurrent pollers never receive the same job (SKIP LOCKED)."""
    rows = db.scalars(
        select(AgentJob)
        .where(AgentJob.server_id == server_id, AgentJob.status == "queued")
        .order_by(AgentJob.created_at)
        .limit(max(1, min(limit, MAX_CONCURRENT_CLAIM)))
        .with_for_update(skip_locked=True)
    ).all()
    for j in rows:
        transition(db, j, "running")
    return list(rows)


def sweep_timed_out_jobs(db: Session, now: datetime | None = None) -> int:
    """Mark running jobs that exceeded timeout + grace as timed_out. Safe to call from any periodic task."""
    now = now or utcnow()
    rows = db.scalars(select(AgentJob).where(AgentJob.status == "running").with_for_update(skip_locked=True)).all()
    n = 0
    for j in rows:
        if j.started_at and j.started_at + timedelta(seconds=j.timeout_seconds + TIMEOUT_GRACE_SECONDS) < now:
            transition(db, j, "timed_out", error="No result reported before the timeout elapsed.", now=now)
            n += 1
    return n
