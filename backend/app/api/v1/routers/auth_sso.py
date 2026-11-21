"""Sign in with Google / Microsoft (OpenID Connect). See app/services/sso.py for the security model."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, get_ctx
from app.core.ratelimit import enforce
from app.models.identity import User
from app.models.sso import UserIdentity
from app.services import auth as asvc
from app.services import sso
from app.services.audit import audit

router = APIRouter(prefix="/auth/sso", tags=["auth"])
Provider = Literal["google", "microsoft"]


def _cookie(resp: Response, name: str, value: str, ttl: int) -> None:
    s = get_settings()
    resp.set_cookie(name, value, max_age=ttl, path="/api/v1/auth/sso", httponly=True, secure=s.cookie_secure, samesite="lax")


def _front(path: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}{path}"


@router.get("/providers")
def list_providers():
    return [{"id": k, "label": v["label"]} for k, v in sso.providers().items()]


class StartIn(BaseModel):
    mode: Literal["login", "link"] = "login"


@router.post("/{provider}/start")
def start(provider: Provider, body: StartIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Returns the provider URL to navigate to. 'link' requires a signed-in session (and CSRF, via the POST)."""
    enforce(f"sso-start:{client_ip(request)}", 30, 600)
    user_id = None
    if body.mode == "link":
        user_id = get_ctx(request, db).user.id
    url, cookie = sso.start(provider, body.mode, user_id)
    _cookie(response, sso.STATE_COOKIE, cookie, sso.STATE_TTL)
    return {"url": url}


@router.get("/{provider}/callback")
def callback(provider: Provider, request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    def fail(reason: str) -> RedirectResponse:
        r = RedirectResponse(_front(f"/login?sso_error={reason}"), status_code=303)
        r.delete_cookie(sso.STATE_COOKIE, path="/api/v1/auth/sso")
        return r

    if error or not code or not state or len(code) > 4096 or len(state) > 200:
        return fail("cancelled")
    enforce(f"sso-cb:{client_ip(request)}", 30, 600)
    try:
        claims, flow = sso.finish(provider, code, state, request.cookies.get(sso.STATE_COOKIE))
    except Exception as e:  # noqa: BLE001 - every failure becomes a generic redirect; details are audited
        audit(db, "auth.sso.failed", detail={"provider": provider, "reason": getattr(e, "detail", {}).get("code") if isinstance(getattr(e, "detail", None), dict) else type(e).__name__}, ip=client_ip(request))
        db.commit()
        return fail("invalid")
    sub = sso.subject(provider, claims)
    email = str(claims.get("email") or "").lower() or None
    ident = db.scalar(select(UserIdentity).where(UserIdentity.provider == provider, UserIdentity.subject == sub))

    if flow.get("m") == "link":
        uid = flow.get("u")
        cur = None
        try:
            cur = get_ctx(request, db).user
        except Exception:  # noqa: BLE001
            pass
        if not uid or cur is None or str(cur.id) != uid:
            return fail("session")
        if ident and ident.user_id != cur.id:
            return RedirectResponse(_front("/account?sso=in_use"), status_code=303)
        if not ident:
            existing = db.scalar(select(UserIdentity).where(UserIdentity.user_id == cur.id, UserIdentity.provider == provider))
            if existing:
                db.delete(existing)
                db.flush()
            db.add(UserIdentity(user_id=cur.id, provider=provider, subject=sub, email=email))
        audit(db, "auth.sso.linked", actor=cur.id, detail={"provider": provider}, ip=client_ip(request))
        db.commit()
        r = RedirectResponse(_front("/account?sso=linked"), status_code=303)
        r.delete_cookie(sso.STATE_COOKIE, path="/api/v1/auth/sso")
        return r

    user = db.get(User, ident.user_id) if ident else None
    if user is None and provider == "google" and email and claims.get("email_verified") is True:
        cand = db.scalar(select(User).where(User.email == email))
        if cand is not None and db.scalar(select(UserIdentity).where(UserIdentity.user_id == cand.id, UserIdentity.provider == provider)) is None:
            user = cand
            db.add(UserIdentity(user_id=cand.id, provider=provider, subject=sub, email=email))
    if user is None:
        audit(db, "auth.sso.no_account", detail={"provider": provider}, ip=client_ip(request))
        db.commit()
        return fail("no_account")
    if user.status != "active" or not user.email_verified_at or (user.locked_until and user.locked_until > asvc.now()):
        return fail("account_unavailable")
    if ident:
        ident.last_used_at = asvc.now()
    if user.totp_enabled:
        db.commit()
        r = RedirectResponse(_front("/login?sso=2fa"), status_code=303)
        r.delete_cookie(sso.STATE_COOKIE, path="/api/v1/auth/sso")
        _cookie(r, sso.PENDING_COOKIE, sso.pending_cookie(user.id, provider), sso.PENDING_TTL)
        return r
    r = RedirectResponse(_front("/"), status_code=303)
    from app.api.v1.routers.auth import _finish_login

    _finish_login(db, user, request, r, f"sso_{provider}")
    r.delete_cookie(sso.STATE_COOKIE, path="/api/v1/auth/sso")
    return r


class TotpIn(BaseModel):
    code: str = Field(min_length=6, max_length=8, pattern=r"^\d+$")


@router.post("/2fa")
def complete_2fa(body: TotpIn, request: Request, response: Response, db: Session = Depends(get_db)):
    pend = sso.read_pending(request.cookies.get(sso.PENDING_COOKIE))
    if not pend:
        raise api_error(401, "sso_2fa_expired", "The sign-in expired. Please start again.")
    enforce(f"sso-2fa:{pend['two_fa']}", 10, 900)
    user = db.get(User, uuid.UUID(pend["two_fa"]))
    if user is None or user.status != "active" or not user.totp_enabled:
        raise api_error(401, "sso_2fa_expired", "The sign-in expired. Please start again.")
    if not asvc.verify_totp(user, body.code):
        asvc.register_failure(db, user)
        asvc.record_login(db, user.email, "otp_failed", request, user)
        db.commit()
        raise api_error(401, "invalid_totp", "Authenticator code is incorrect.")
    from app.api.v1.routers.auth import _finish_login

    out = _finish_login(db, user, request, response, f"sso_{pend.get('p')}")
    response.delete_cookie(sso.PENDING_COOKIE, path="/api/v1/auth/sso")
    return out


@router.get("/identities")
def identities(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    rows = db.scalars(select(UserIdentity).where(UserIdentity.user_id == ctx.user.id)).all()
    return [{"provider": r.provider, "email": r.email, "created_at": r.created_at, "last_used_at": r.last_used_at} for r in rows]


@router.delete("/identities/{provider}", status_code=204)
def unlink(provider: Provider, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    row = db.scalar(select(UserIdentity).where(UserIdentity.user_id == ctx.user.id, UserIdentity.provider == provider))
    if row is None:
        raise api_error(404, "not_found", "Not linked.")
    db.delete(row)
    audit(db, "auth.sso.unlinked", actor=ctx.user.id, detail={"provider": provider}, ip=client_ip(request))
    db.commit()
