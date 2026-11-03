"""Password hashing, tokens, OTP codes and secret encryption. No plaintext secrets are ever stored."""
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

# Argon2id. Defaults follow RFC 9106 (t=3, m=64 MiB, p=4); the test env uses cheap parameters purely for speed.
_ph = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1) if get_settings().env == "test" else PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


# Constant-time dummy verification so unknown accounts cost the same as wrong passwords.
_DUMMY_HASH = _ph.hash("not-a-real-password")


def dummy_verify() -> None:
    verify_password("x", _DUMMY_HASH)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hmac_sha256(key: str, msg: str) -> str:
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def hmac_matches(key: str, msg: str, signature: str) -> bool:
    return hmac.compare_digest(hmac_sha256(key, msg), signature)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(challenge_id: uuid.UUID | str, code: str) -> str:
    # Keyed with the challenge id and server secret so a leaked table cannot be brute-forced offline cheaply.
    return hmac_sha256(get_settings().secret_key, f"{challenge_id}:{code}")


# ---- JWT ----
def issue_jwt(subject: str, purpose: str, ttl: int, extra: dict[str, Any] | None = None) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": subject,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "purpose": purpose,
        **(extra or {}),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_jwt(token: str, purpose: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        data = jwt.decode(
            token,
            s.secret_key,
            algorithms=["HS256"],
            issuer=s.jwt_issuer,
            audience=s.jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    return data if data.get("purpose") == purpose else None


# ---- Secret encryption at rest (Fernet) ----
def _fernet() -> Fernet:
    s = get_settings()
    if s.encryption_key:
        return Fernet(s.encryption_key.encode())
    if not s.is_dev:
        raise RuntimeError("ENCRYPTION_KEY required")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
