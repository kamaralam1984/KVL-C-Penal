import re

from sqlalchemy import select

from app.core.security import verify_password
from app.models.identity import AuthSession, EmailOutbox, LoginEvent, OtpChallenge, User
from tests.conftest import PW, login


def outbox_code(db, email, kind):
    row = db.scalars(select(EmailOutbox).where(EmailOutbox.to_email == email, EmailOutbox.kind == kind).order_by(EmailOutbox.created_at.desc())).first()
    return re.search(r"code is (\d{6})", row.body).group(1)


def test_password_is_argon2id_and_never_plaintext(client, db):
    r = client.post("/api/v1/auth/register", json={"email": "a@x.example.com", "name": "Alice", "password": PW})
    assert r.status_code == 202
    u = db.scalar(select(User).where(User.email == "a@x.example.com"))
    assert u.password_hash.startswith("$argon2id$")
    assert PW not in u.password_hash and verify_password(PW, u.password_hash)


def test_register_verify_login_flow(client, db):
    client.post("/api/v1/auth/register", json={"email": "b@x.example.com", "name": "Bob", "password": PW})
    assert login(client, "b@x.example.com").json()["error"]["code"] == "email_not_verified"
    code = outbox_code(db, "b@x.example.com", "otp_verify_email")
    assert client.post("/api/v1/auth/verify-email", json={"email": "b@x.example.com", "code": code}).status_code == 200
    assert login(client, "b@x.example.com").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "customer" and me["tenant_status"] == "trial"


def test_register_does_not_reveal_existing_accounts(client, make_user):
    make_user("exists@x.example.com")
    new = client.post("/api/v1/auth/register", json={"email": "fresh@x.example.com", "name": "Fresh", "password": PW})
    dup = client.post("/api/v1/auth/register", json={"email": "exists@x.example.com", "name": "Dup", "password": PW})
    assert new.status_code == dup.status_code == 202 and new.json() == dup.json()


def test_weak_passwords_rejected(client):
    for pw in ["short1", "alllettersonly", "1234567890", "Password1"[:9]]:
        r = client.post("/api/v1/auth/register", json={"email": "w@x.example.com", "name": "Weak", "password": pw})
        assert r.status_code == 422, pw


def test_login_errors_are_uniform(client, make_user):
    make_user("real@x.example.com")
    a = login(client, "real@x.example.com", "Wrong-Pass-1")
    b = login(client, "ghost@x.example.com", "Wrong-Pass-1")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


def test_lockout_after_repeated_failures(client, make_user, db):
    make_user("lock@x.example.com")
    for _ in range(5):
        login(client, "lock@x.example.com", "Wrong-Pass-1")
    r = login(client, "lock@x.example.com")  # correct password now refused
    assert r.status_code == 423 and r.json()["error"]["code"] == "account_locked"
    assert db.scalar(select(LoginEvent).where(LoginEvent.outcome == "locked"))


def test_login_rate_limited(client):
    codes = [client.post("/api/v1/auth/login", json={"email": "rl@x.example.com", "password": "x"}).status_code for _ in range(12)]
    assert 429 in codes


def test_otp_expiry_reuse_and_attempt_limit(client, make_user, db):
    make_user("otp@x.example.com")
    client.post("/api/v1/auth/otp/request", json={"email": "otp@x.example.com", "purpose": "login"})
    code = outbox_code(db, "otp@x.example.com", "otp_login")
    assert client.post("/api/v1/auth/otp/login", json={"email": "otp@x.example.com", "code": code}).status_code == 200
    # single use
    c2 = client.__class__(client.app)
    assert c2.post("/api/v1/auth/otp/login", json={"email": "otp@x.example.com", "code": code}).status_code == 401
    # attempt limit: 5 wrong guesses burn the challenge, even the right code then fails
    from app.core import ratelimit

    ratelimit.reset_all()
    c2.post("/api/v1/auth/otp/request", json={"email": "otp@x.example.com", "purpose": "login"})
    # cooldown prevents a second code; clear it by aging the first challenge
    db.execute(OtpChallenge.__table__.update().values(created_at=OtpChallenge.created_at - __import__("datetime").timedelta(minutes=5)))
    db.commit()
    c2.post("/api/v1/auth/otp/request", json={"email": "otp@x.example.com", "purpose": "login"})
    good = outbox_code(db, "otp@x.example.com", "otp_login")
    for _ in range(5):
        c2.post("/api/v1/auth/otp/login", json={"email": "otp@x.example.com", "code": "000000" if good != "000000" else "111111"})
    assert c2.post("/api/v1/auth/otp/login", json={"email": "otp@x.example.com", "code": good}).status_code == 401


def test_otp_expired_code_rejected(client, make_user, db):
    import datetime as dt

    make_user("exp@x.example.com")
    client.post("/api/v1/auth/otp/request", json={"email": "exp@x.example.com", "purpose": "login"})
    code = outbox_code(db, "exp@x.example.com", "otp_login")
    db.execute(OtpChallenge.__table__.update().values(expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)))
    db.commit()
    assert client.post("/api/v1/auth/otp/login", json={"email": "exp@x.example.com", "code": code}).status_code == 401


def test_otp_resend_cooldown(client, make_user, db):
    make_user("cd@x.example.com")
    for _ in range(3):
        client.post("/api/v1/auth/otp/request", json={"email": "cd@x.example.com", "purpose": "login"})
    assert len(db.scalars(select(EmailOutbox).where(EmailOutbox.kind == "otp_login")).all()) == 1


def test_refresh_rotates_and_reuse_revokes_family(client, make_user, db):
    make_user("rot@x.example.com")
    login(client, "rot@x.example.com")
    old_refresh = client.cookies.get("kvl_rt")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    new_refresh = client.cookies.get("kvl_rt")
    assert new_refresh != old_refresh
    # replay the stolen, already-rotated token
    thief = client.__class__(client.app)
    thief.cookies.set("kvl_rt", old_refresh, path="/api/v1/auth")
    thief.cookies.set("kvl_csrf", client.cookies.get("kvl_csrf"))
    thief.headers["X-CSRF-Token"] = client.cookies.get("kvl_csrf")
    r = thief.post("/api/v1/auth/refresh")
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_reuse"
    # legitimate holder's newest token is dead too
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert all(s.revoked_at for s in db.scalars(select(AuthSession)))


def test_logout_revokes_session_immediately(client, make_user):
    make_user("out@x.example.com")
    login(client, "out@x.example.com")
    assert client.get("/api/v1/auth/me").status_code == 200
    stolen_access = client.cookies.get("kvl_at")
    client.post("/api/v1/auth/logout")
    client.cookies.set("kvl_at", stolen_access)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_required_for_authenticated_writes(client, make_user):
    make_user("csrf@x.example.com")
    login(client, "csrf@x.example.com")
    saved = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/v1/auth/password/change", json={"current_password": PW, "new_password": "Another-Pass-77"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = saved
    assert client.post("/api/v1/auth/password/change", json={"current_password": PW, "new_password": "Another-Pass-77"}).status_code == 200


def test_password_change_revokes_other_sessions(make_client, make_user):
    make_user("pc@x.example.com")
    a, b = make_client(), make_client()
    login(a, "pc@x.example.com")
    login(b, "pc@x.example.com")
    assert a.post("/api/v1/auth/password/change", json={"current_password": PW, "new_password": "Another-Pass-77"}).status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401
    assert a.get("/api/v1/auth/me").status_code == 200


def test_password_reset_flow(client, make_user, db):
    make_user("reset@x.example.com")
    client.post("/api/v1/auth/password/forgot", json={"email": "reset@x.example.com"})
    code = outbox_code(db, "reset@x.example.com", "otp_password_reset")
    r = client.post("/api/v1/auth/password/reset", json={"email": "reset@x.example.com", "code": code, "new_password": "Brand-New-Pass-5"})
    assert r.status_code == 200
    assert login(client, "reset@x.example.com", "Brand-New-Pass-5").status_code == 200
    assert client.post("/api/v1/auth/password/reset", json={"email": "reset@x.example.com", "code": code, "new_password": "Brand-New-Pass-6"}).status_code == 400


def test_forgot_password_does_not_enumerate(client, make_user):
    make_user("known@x.example.com")
    a = client.post("/api/v1/auth/password/forgot", json={"email": "known@x.example.com"})
    b = client.post("/api/v1/auth/password/forgot", json={"email": "nobody@x.example.com"})
    assert a.status_code == b.status_code and a.json() == b.json()


def test_totp_setup_enable_and_required_at_login(client, make_user):
    import pyotp

    make_user("tf@x.example.com")
    login(client, "tf@x.example.com")
    setup = client.post("/api/v1/auth/2fa/setup").json()
    totp = pyotp.TOTP(setup["secret"])
    assert client.post("/api/v1/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    assert client.post("/api/v1/auth/2fa/enable", json={"code": totp.now()}).status_code == 200
    fresh = client.__class__(client.app)
    assert login(fresh, "tf@x.example.com").json()["error"]["code"] == "totp_required"
    assert login(fresh, "tf@x.example.com", totp_code="123456").status_code == 401
    assert login(fresh, "tf@x.example.com", totp_code=totp.now()).status_code == 200


def test_otp_login_cannot_bypass_totp(client, make_user, db):
    import pyotp

    make_user("bp@x.example.com")
    login(client, "bp@x.example.com")
    secret = client.post("/api/v1/auth/2fa/setup").json()["secret"]
    client.post("/api/v1/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
    other = client.__class__(client.app)
    other.post("/api/v1/auth/otp/request", json={"email": "bp@x.example.com", "purpose": "login"})
    code = outbox_code(db, "bp@x.example.com", "otp_login")
    assert other.post("/api/v1/auth/otp/login", json={"email": "bp@x.example.com", "code": code}).status_code == 401


def test_session_listing_and_revocation(make_client, make_user):
    make_user("ses@x.example.com")
    a, b = make_client(), make_client()
    login(a, "ses@x.example.com")
    login(b, "ses@x.example.com")
    sessions = a.get("/api/v1/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"/api/v1/auth/sessions/{other['id']}").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401


def test_cannot_revoke_someone_elses_session(make_client, make_user):
    make_user("s1@x.example.com")
    make_user("s2@x.example.com")
    a, b = make_client(), make_client()
    login(a, "s1@x.example.com")
    login(b, "s2@x.example.com")
    sid = b.get("/api/v1/auth/sessions").json()[0]["id"]
    assert a.delete(f"/api/v1/auth/sessions/{sid}").status_code == 404


def test_suspicious_login_flagged_and_history_recorded(make_client, make_user, db):
    make_user("sus@x.example.com")
    a = make_client()
    login(a, "sus@x.example.com")
    b = make_client()
    b.headers["User-Agent"] = "BrandNewBrowser/1.0"
    # same IP (testclient) so only UA differs -> not suspicious; change both by editing prior event
    db.execute(LoginEvent.__table__.update().values(ip="9.9.9.9", user_agent="old"))
    db.commit()
    assert login(b, "sus@x.example.com").json()["suspicious"] is True
    hist = b.get("/api/v1/auth/login-history").json()
    assert hist[0]["suspicious"] is True


def test_unauthenticated_requests_rejected(client):
    for path in ["/api/v1/auth/me", "/api/v1/auth/sessions", "/api/v1/tenants", "/api/v1/users", "/api/v1/rbac/roles", "/api/v1/audit"]:
        assert client.get(path).status_code == 401, path


def test_security_headers_and_error_shape(client):
    r = client.get("/api/v1/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
    body = r.json()["error"]
    assert body["code"] == "unauthenticated" and body["request_id"] == r.headers["x-request-id"]
