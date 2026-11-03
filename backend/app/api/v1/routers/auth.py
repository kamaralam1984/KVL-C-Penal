import uuid
import pyotp
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import REFRESH_COOKIE, Ctx, api_error, client_ip, get_ctx
from app.core.ratelimit import enforce
from app.core.security import dummy_verify, encrypt_secret, hash_password, needs_rehash, sha256, verify_password
from app.models.identity import AuthSession, Invitation, LoginEvent, Membership, Role, User
from app.services import auth as svc
from app.services.audit import audit
from app.services.mail import queue_email
from app.services.tenants import provision_tenant

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC = {"status": "ok", "message": "If the details are valid, a code has been sent."}


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    organization: str | None = Field(default=None, max_length=120)


class EmailCodeIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class EmailIn(BaseModel):
    email: EmailStr


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)
    totp_code: str | None = Field(default=None, max_length=10)


class ResetIn(BaseModel):
    email: EmailStr
    code: str
    new_password: str = Field(max_length=200)


class ChangePwIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class OtpPurposeIn(BaseModel):
    email: EmailStr
    purpose: str = Field(pattern="^(verify_email|login)$")


class AcceptInviteIn(BaseModel):
    token: str
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(max_length=200)


class SwitchIn(BaseModel):
    membership_id: uuid.UUID


class TotpCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class DisableTotpIn(BaseModel):
    password: str = Field(max_length=200)
    code: str = Field(min_length=6, max_length=10)


def _ip_limit(request: Request, name: str, limit: int, window: int) -> None:
    enforce(f"{name}:ip:{client_ip(request)}", limit, window)


@router.post("/register", status_code=202)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    _ip_limit(request, "register", 10, 3600)
    email = svc.norm_email(body.email)
    svc.validate_password(body.password, email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        # Same response as success; tell the real owner out-of-band.
        queue_email(db, email, "KVL Panel: sign-up attempt", "Someone tried to register with this email. If it was you, sign in or reset your password.", "register_existing")
        db.commit()
        return GENERIC
    user = svc.create_user(db, email, body.name, body.password)
    provision_tenant(db, body.organization or f"{body.name}'s hosting", user.id, email, status="trial", actor=user.id)
    svc.issue_otp(db, email, "verify_email", enforce_cooldown=False)
    audit(db, "auth.register", actor=user.id, ip=client_ip(request), request_id=getattr(request.state, "request_id", None))
    db.commit()
    return GENERIC


@router.post("/verify-email")
def verify_email(body: EmailCodeIn, request: Request, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    enforce(f"verify:{email}", 10, 900)
    _ip_limit(request, "verify", 30, 900)
    user = db.scalar(select(User).where(User.email == email))
    ok = svc.check_otp(db, email, "verify_email", body.code)
    if not ok or not user:
        db.commit()
        raise api_error(400, "invalid_code", "The code is invalid or has expired.")
    user.email_verified_at = svc.now()
    audit(db, "auth.email_verified", actor=user.id, ip=client_ip(request))
    db.commit()
    return {"status": "verified"}


@router.post("/otp/request")
def otp_request(body: OtpPurposeIn, request: Request, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    _ip_limit(request, "otp_req", 20, 3600)
    enforce(f"otp_req:{email}", 6, 3600)
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user and (body.purpose == "verify_email" or user.email_verified_at):
        svc.issue_otp(db, email, body.purpose)  # silently skipped inside cooldown
        db.commit()
    return GENERIC


def _finish_login(db: Session, user: User, request: Request, response: Response, outcome_ctx: str) -> dict:
    susp = svc.is_suspicious(db, user, request)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = svc.now()
    svc.record_login(db, user.email, "success", request, user, suspicious=susp)
    sess = svc.start_session(db, user, request, response)
    if susp:
        queue_email(db, user.email, "KVL Panel: new sign-in", "A sign-in from a new device or location was detected. If this wasn't you, change your password and revoke sessions.", "suspicious_login")
        audit(db, "auth.suspicious_login", actor=user.id, ip=client_ip(request))
    audit(db, f"auth.login.{outcome_ctx}", actor=user.id, ip=client_ip(request), request_id=getattr(request.state, "request_id", None))
    db.commit()
    return {"status": "ok", "session_id": str(sess.id), "suspicious": susp}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    _ip_limit(request, "login", 30, 900)
    enforce(f"login:{email}", 10, 900)
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        dummy_verify()
        svc.record_login(db, email, "unknown_user", request)
        db.commit()
        raise api_error(401, "invalid_credentials", "Email or password is incorrect.")
    if user.locked_until and user.locked_until > svc.now():
        svc.record_login(db, email, "locked", request, user)
        db.commit()
        raise api_error(423, "account_locked", "Too many failed attempts. Try again later or reset your password.")
    if not verify_password(body.password, user.password_hash):
        svc.register_failure(db, user)
        svc.record_login(db, email, "bad_password", request, user)
        db.commit()
        raise api_error(401, "invalid_credentials", "Email or password is incorrect.")
    if user.status != "active":
        raise api_error(403, "account_disabled", "This account is disabled.")
    if not user.email_verified_at:
        raise api_error(403, "email_not_verified", "Verify your email before signing in.")
    if user.totp_enabled:
        if not body.totp_code:
            raise api_error(401, "totp_required", "Enter your authenticator code.")
        if not svc.verify_totp(user, body.totp_code):
            svc.register_failure(db, user)
            svc.record_login(db, email, "otp_failed", request, user)
            db.commit()
            raise api_error(401, "invalid_totp", "Authenticator code is incorrect.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    return _finish_login(db, user, request, response, "password")


@router.post("/otp/login")
def otp_login(body: EmailCodeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    _ip_limit(request, "otp_login", 30, 900)
    enforce(f"otp_login:{email}", 10, 900)
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    ok = svc.check_otp(db, email, "login", body.code)
    if not ok or not user or not user.email_verified_at or user.totp_enabled:
        # Accounts with 2FA must use password + authenticator; OTP alone would bypass the second factor.
        svc.record_login(db, email, "otp_failed", request, user)
        db.commit()
        raise api_error(401, "invalid_code", "The code is invalid or has expired.")
    return _finish_login(db, user, request, response, "otp")


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    _ip_limit(request, "refresh", 120, 900)
    sess = svc.rotate_session(db, request, response)
    db.commit()
    return {"status": "ok", "session_id": str(sess.id)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        sess = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == sha256(token)))
        if sess and not sess.revoked_at:
            sess.revoked_at = svc.now()
            audit(db, "auth.logout", actor=sess.user_id, ip=client_ip(request))
            db.commit()
    svc.clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    ms = db.scalars(select(Membership).where(Membership.user_id == ctx.user.id, Membership.status == "active")).all()
    roles = {r.id: r for r in db.scalars(select(Role))}
    return {
        "user": {"id": str(ctx.user.id), "email": ctx.user.email, "name": ctx.user.name, "totp_enabled": ctx.user.totp_enabled, "email_verified": bool(ctx.user.email_verified_at)},
        "active_membership_id": str(ctx.membership.id) if ctx.membership else None,
        "role": ctx.role,
        "scope": ctx.scope,
        "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None,
        "tenant_status": ctx.tenant_status,
        "permissions": sorted(ctx.permissions),
        "memberships": [{"id": str(m.id), "role": roles[m.role_id].name, "role_label": roles[m.role_id].label, "tenant_id": str(m.tenant_id) if m.tenant_id else None} for m in ms],
    }


@router.post("/switch-membership")
def switch_membership(body: SwitchIn, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    m = db.scalar(select(Membership).where(Membership.id == body.membership_id, Membership.user_id == ctx.user.id, Membership.status == "active"))
    if not m:  # not owned by this user -> indistinguishable from nonexistent
        raise api_error(404, "not_found", "Membership not found.")
    ctx.session.active_membership_id = m.id
    audit(db, "auth.switch_membership", actor=ctx.user.id, tenant_id=m.tenant_id, target_type="membership", target_id=m.id)
    db.commit()
    return {"status": "ok"}


# ---------------- passwords ----------------
@router.post("/password/forgot")
def password_forgot(body: EmailIn, request: Request, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    _ip_limit(request, "pw_forgot", 10, 3600)
    enforce(f"pw_forgot:{email}", 5, 3600)
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user:
        svc.issue_otp(db, email, "password_reset")
        db.commit()
    return GENERIC


@router.post("/password/reset")
def password_reset(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    email = svc.norm_email(body.email)
    enforce(f"pw_reset:{email}", 10, 900)
    _ip_limit(request, "pw_reset", 30, 900)
    svc.validate_password(body.new_password, email)
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if not svc.check_otp(db, email, "password_reset", body.code) or not user:
        db.commit()
        raise api_error(400, "invalid_code", "The code is invalid or has expired.")
    user.password_hash = hash_password(body.new_password)
    user.failed_logins, user.locked_until = 0, None
    user.email_verified_at = user.email_verified_at or svc.now()  # proves mailbox control
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=svc.now()))
    audit(db, "auth.password_reset", actor=user.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.post("/password/change")
def password_change(body: ChangePwIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    enforce(f"pw_change:{ctx.user.id}", 10, 900)
    if not verify_password(body.current_password, ctx.user.password_hash):
        raise api_error(400, "invalid_credentials", "Current password is incorrect.")
    svc.validate_password(body.new_password, ctx.user.email)
    ctx.user.password_hash = hash_password(body.new_password)
    db.execute(update(AuthSession).where(AuthSession.user_id == ctx.user.id, AuthSession.id != ctx.session.id, AuthSession.revoked_at.is_(None)).values(revoked_at=svc.now()))
    audit(db, "auth.password_change", actor=ctx.user.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ---------------- TOTP ----------------
@router.post("/2fa/setup")
def totp_setup(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    if ctx.user.totp_enabled:
        raise api_error(409, "totp_already_enabled", "Two-factor authentication is already enabled.")
    secret = pyotp.random_base32()
    ctx.user.totp_secret_enc = encrypt_secret(secret)
    db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=ctx.user.email, issuer_name="KVL Panel")
    return {"secret": secret, "otpauth_uri": uri}  # shown once, never retrievable again


@router.post("/2fa/enable")
def totp_enable(body: TotpCodeIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    enforce(f"totp_enable:{ctx.user.id}", 10, 900)
    if not svc.verify_totp(ctx.user, body.code):
        raise api_error(400, "invalid_totp", "Authenticator code is incorrect.")
    ctx.user.totp_enabled = True
    audit(db, "auth.2fa_enabled", actor=ctx.user.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


@router.post("/2fa/disable")
def totp_disable(body: DisableTotpIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    enforce(f"totp_disable:{ctx.user.id}", 10, 900)
    if not verify_password(body.password, ctx.user.password_hash) or not svc.verify_totp(ctx.user, body.code):
        raise api_error(400, "invalid_credentials", "Password or code is incorrect.")
    ctx.user.totp_enabled, ctx.user.totp_secret_enc = False, None
    audit(db, "auth.2fa_disabled", actor=ctx.user.id, ip=client_ip(request))
    db.commit()
    return {"status": "ok"}


# ---------------- sessions + history ----------------
@router.get("/sessions")
def list_sessions(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == ctx.user.id, AuthSession.revoked_at.is_(None), AuthSession.rotated_at.is_(None), AuthSession.expires_at > svc.now()).order_by(AuthSession.created_at.desc())
    ).all()
    return [{"id": str(s.id), "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at, "last_used_at": s.last_used_at, "current": s.family_id == ctx.session.family_id} for s in rows]


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: uuid.UUID, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    s = db.scalar(select(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == ctx.user.id))
    if not s:
        raise api_error(404, "not_found", "Session not found.")
    db.execute(update(AuthSession).where(AuthSession.family_id == s.family_id, AuthSession.revoked_at.is_(None)).values(revoked_at=svc.now()))
    audit(db, "auth.session_revoked", actor=ctx.user.id, target_type="session", target_id=session_id)
    db.commit()
    return {"status": "ok"}


@router.post("/sessions/revoke-others")
def revoke_others(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    db.execute(update(AuthSession).where(AuthSession.user_id == ctx.user.id, AuthSession.family_id != ctx.session.family_id, AuthSession.revoked_at.is_(None)).values(revoked_at=svc.now()))
    audit(db, "auth.sessions_revoked_others", actor=ctx.user.id)
    db.commit()
    return {"status": "ok"}


@router.get("/login-history")
def login_history(limit: int = 25, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    rows = db.scalars(select(LoginEvent).where(LoginEvent.user_id == ctx.user.id).order_by(LoginEvent.created_at.desc()).limit(min(limit, 100))).all()
    return [{"id": str(r.id), "outcome": r.outcome, "suspicious": r.suspicious, "ip": r.ip, "user_agent": r.user_agent, "created_at": r.created_at} for r in rows]


# ---------------- invitation acceptance ----------------
@router.post("/invitations/accept")
def accept_invitation(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _ip_limit(request, "invite_accept", 20, 900)
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == sha256(body.token)))
    if not inv or inv.accepted_at or inv.revoked_at or inv.expires_at < svc.now():
        raise api_error(400, "invalid_invitation", "This invitation is invalid or has expired.")
    svc.validate_password(body.password, inv.email)
    user = db.scalar(select(User).where(User.email == inv.email))
    if user:
        raise api_error(409, "account_exists", "An account already exists for this email. Sign in and ask an admin to add the role.")
    user = svc.create_user(db, inv.email, body.name, body.password, verified=True)  # invite link proves mailbox control
    db.add(Membership(user_id=user.id, role_id=inv.role_id, tenant_id=inv.tenant_id))
    inv.accepted_at = svc.now()
    audit(db, "staff.invitation_accepted", actor=user.id, tenant_id=inv.tenant_id, target_type="invitation", target_id=inv.id)
    db.flush()
    svc.start_session(db, user, request, response)
    db.commit()
    return {"status": "ok"}

