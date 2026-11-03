import re
import uuid
from datetime import UTC, datetime, timedelta

import pyotp
from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, api_error, client_ip
from app.core.security import (
    decrypt_secret,
    hash_otp,
    hash_password,
    issue_jwt,
    new_otp,
    new_token,
    sha256,
)
from app.models.identity import AuthSession, LoginEvent, Membership, OtpChallenge, User
from app.services.audit import audit
from app.services.mail import queue_email

COMMON = {"password", "password1", "1234567890", "qwertyuiop", "letmein123", "admin12345", "welcome123", "iloveyou12"}


def now() -> datetime:
    return datetime.now(UTC)


def norm_email(e: str) -> str:
    return e.strip().lower()


def validate_password(pw: str, email: str = "") -> None:
    problems = []
    if len(pw) < 10:
        problems.append("at least 10 characters")
    if len(pw) > 128:
        problems.append("at most 128 characters")
    if pw.lower() in COMMON or (email and email.split("@")[0].lower() in pw.lower() and len(email.split("@")[0]) > 3):
        problems.append("not be a common password or contain your email name")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        problems.append("contain letters and numbers")
    if problems:
        raise api_error(422, "weak_password", "Password must " + ", ".join(problems) + ".")


# ---------------- OTP ----------------
def issue_otp(db: Session, email: str, purpose: str, *, enforce_cooldown: bool = True) -> str | None:
    """Create a fresh OTP and queue the email. Returns the challenge id (None when throttled by cooldown)."""
    s = get_settings()
    email = norm_email(email)
    last = db.scalar(
        select(OtpChallenge).where(OtpChallenge.email == email, OtpChallenge.purpose == purpose).order_by(OtpChallenge.created_at.desc())
    )
    if enforce_cooldown and last and (now() - last.created_at).total_seconds() < s.otp_resend_cooldown_seconds:
        return None
    # invalidate older outstanding challenges
    db.execute(
        update(OtpChallenge)
        .where(OtpChallenge.email == email, OtpChallenge.purpose == purpose, OtpChallenge.consumed_at.is_(None))
        .values(consumed_at=now())
    )
    code = new_otp()
    cid = uuid.uuid4()
    db.add(OtpChallenge(id=cid, email=email, purpose=purpose, code_hash=hash_otp(cid, code), expires_at=now() + timedelta(seconds=s.otp_ttl_seconds)))
    subjects = {"verify_email": "Verify your email", "login": "Your sign-in code", "password_reset": "Reset your password"}
    queue_email(
        db, email, f"KVL Panel: {subjects.get(purpose, 'Your code')}",
        f"Your code is {code}. It expires in {s.otp_ttl_seconds // 60} minutes. If you did not request it, ignore this message.",
        f"otp_{purpose}",
    )
    return str(cid)


def check_otp(db: Session, email: str, purpose: str, code: str) -> bool:
    """Single-use verification with attempt limiting. Returns False (never raises) on any failure."""
    s = get_settings()
    email = norm_email(email)
    ch = db.scalar(
        select(OtpChallenge)
        .where(OtpChallenge.email == email, OtpChallenge.purpose == purpose, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc())
    )
    if not ch or ch.expires_at < now() or ch.attempts >= s.otp_max_attempts:
        return False
    ch.attempts += 1
    import hmac as _h

    if not _h.compare_digest(ch.code_hash, hash_otp(ch.id, code.strip())):
        db.commit()
        return False
    ch.consumed_at = now()
    return True


# ---------------- sessions / cookies ----------------
def set_auth_cookies(response: Response, access: str, refresh: str | None, csrf: str) -> None:
    s = get_settings()
    kw = {"httponly": True, "secure": s.cookie_secure, "samesite": "lax"}
    response.set_cookie(ACCESS_COOKIE, access, max_age=s.access_ttl_seconds, path="/", **kw)
    if refresh:
        response.set_cookie(REFRESH_COOKIE, refresh, max_age=s.refresh_ttl_seconds, path="/api/v1/auth", **kw)
    response.set_cookie(CSRF_COOKIE, csrf, max_age=s.refresh_ttl_seconds, path="/", httponly=False, secure=s.cookie_secure, samesite="lax")


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def default_membership(db: Session, user: User) -> Membership | None:
    ms = db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.status == "active").order_by(Membership.created_at)).all()
    ms.sort(key=lambda m: m.tenant_id is not None)  # platform memberships first
    return ms[0] if ms else None


def start_session(db: Session, user: User, request: Request, response: Response, family: uuid.UUID | None = None, membership_id: uuid.UUID | None = None, csrf: str | None = None) -> AuthSession:
    s = get_settings()
    if membership_id is None:
        m = default_membership(db, user)
        membership_id = m.id if m else None
    refresh = new_token(40)
    sess = AuthSession(
        user_id=user.id,
        family_id=family or uuid.uuid4(),
        refresh_hash=sha256(refresh),
        active_membership_id=membership_id,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
        expires_at=now() + timedelta(seconds=s.refresh_ttl_seconds),
        last_used_at=now(),
    )
    db.add(sess)
    db.flush()
    access = issue_jwt(str(user.id), "access", s.access_ttl_seconds, {"sid": str(sess.id)})
    set_auth_cookies(response, access, refresh, csrf or new_token(24))
    return sess


def rotate_session(db: Session, request: Request, response: Response) -> AuthSession:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise api_error(401, "unauthenticated", "No refresh token.")
    sess = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == sha256(token)))
    if not sess:
        raise api_error(401, "unauthenticated", "Invalid refresh token.")
    if sess.rotated_at is not None or sess.revoked_at is not None:
        # A rotated/revoked token was replayed: assume theft and kill the whole family.
        db.execute(update(AuthSession).where(AuthSession.family_id == sess.family_id, AuthSession.revoked_at.is_(None)).values(revoked_at=now()))
        audit(db, "auth.refresh_reuse_detected", actor=sess.user_id, target_type="session", target_id=sess.id, ip=client_ip(request))
        db.commit()
        raise api_error(401, "token_reuse", "Session was revoked. Please sign in again.")
    if sess.expires_at < now():
        raise api_error(401, "session_expired", "Session expired.")
    user = db.get(User, sess.user_id)
    if not user or user.status != "active":
        raise api_error(401, "unauthenticated", "Account unavailable.")
    sess.rotated_at = now()
    new = start_session(db, user, request, response, family=sess.family_id, membership_id=sess.active_membership_id, csrf=request.cookies.get(CSRF_COOKIE))
    return new


# ---------------- login helpers ----------------
def record_login(db: Session, email: str, outcome: str, request: Request, user: User | None = None, suspicious: bool = False) -> None:
    db.add(LoginEvent(user_id=user.id if user else None, email=email, outcome=outcome, suspicious=suspicious, ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300]))


def is_suspicious(db: Session, user: User, request: Request) -> bool:
    ip, ua = client_ip(request), (request.headers.get("user-agent") or "")[:300]
    prior = db.scalars(select(LoginEvent).where(LoginEvent.user_id == user.id, LoginEvent.outcome == "success").limit(20)).all()
    if not prior:
        return False  # first login is the baseline
    return not any(p.ip == ip or p.user_agent == ua for p in prior)


def verify_totp(user: User, code: str) -> bool:
    if not user.totp_secret_enc:
        return False
    secret = decrypt_secret(user.totp_secret_enc)
    return bool(secret) and pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def register_failure(db: Session, user: User) -> None:
    s = get_settings()
    user.failed_logins += 1
    if user.failed_logins >= s.lockout_threshold:
        user.locked_until = now() + timedelta(seconds=s.lockout_seconds)
        user.failed_logins = 0


def create_user(db: Session, email: str, name: str, password: str, verified: bool = False) -> User:
    u = User(email=norm_email(email), name=name.strip(), password_hash=hash_password(password), email_verified_at=now() if verified else None)
    db.add(u)
    db.flush()
    return u
