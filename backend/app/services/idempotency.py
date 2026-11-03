"""Idempotency-Key support for POST endpoints.

    result = run_idempotent(db, ctx, request, payload, lambda: (201, {...}))
    return result  # JSONResponse (replays carry `Idempotent-Replayed: true`)

Same key + same payload replays the stored response; same key + different payload is 422; a concurrent duplicate is 409.
"""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import Ctx, api_error
from app.models.security import IdempotencyRecord

TTL = timedelta(hours=24)


def run_idempotent(db: Session, ctx: Ctx, request: Request, payload: Any, fn: Callable[[], tuple[int, dict]]) -> JSONResponse:
    key = request.headers.get("idempotency-key")
    if not key:
        status, body = fn()
        return JSONResponse(status_code=status, content=jsonable_encoder(body))
    if not (8 <= len(key) <= 128) or not key.isascii() or not key.isprintable():
        raise api_error(422, "invalid_idempotency_key", "Idempotency-Key must be 8-128 printable ASCII characters.")
    route = f"{request.method} {request.url.path}"[:200]
    rhash = hashlib.sha256(json.dumps(jsonable_encoder(payload), sort_keys=True, default=str).encode()).hexdigest()
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at < datetime.now(UTC) - TTL))
    rec = IdempotencyRecord(user_id=ctx.user.id, key=key, route=route, request_hash=rhash)
    db.add(rec)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == ctx.user.id, IdempotencyRecord.route == route, IdempotencyRecord.key == key))
        if existing is None:
            raise api_error(409, "idempotency_conflict", "Retry the request.") from None
        if existing.request_hash != rhash:
            raise api_error(422, "idempotency_key_reuse", "This Idempotency-Key was already used with a different request.")
        if existing.state != "done":
            raise api_error(409, "idempotency_in_progress", "A request with this key is still being processed.")
        return JSONResponse(status_code=existing.status_code or 200, content=existing.response, headers={"Idempotent-Replayed": "true"})
    try:
        status, body = fn()
    except BaseException:
        db.rollback()
        db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == rec.id))
        db.commit()
        raise
    enc = jsonable_encoder(body)
    row = db.get(IdempotencyRecord, rec.id)
    row.state, row.status_code, row.response = "done", status, enc
    db.commit()
    return JSONResponse(status_code=status, content=enc)
