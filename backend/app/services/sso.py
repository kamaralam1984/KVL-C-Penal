"""OpenID Connect sign-in with Google and Microsoft (authorization code + PKCE + state + nonce).

- Flow state lives in a short-lived signed HttpOnly cookie (no server storage needed).
- The id_token signature is verified against the provider's JWKS; aud, iss, exp and nonce are checked.
- Account matching: an already-linked identity (provider + subject) always wins. Google may additionally match an
  existing user by *verified* email on first use. Microsoft identities must be linked while signed in (email claims are
  not a reliable proof of ownership there), which prevents account takeover via unverified email addresses.
- SSO never bypasses the panel's own 2FA: users with an authenticator must still enter a code.
"""
import base64
import hashlib
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import jwt

from app.core.config import Settings, get_settings
from app.core.deps import api_error

STATE_COOKIE = "kvl_sso"
PENDING_COOKIE = "kvl_sso_2fa"
STATE_TTL = 600
PENDING_TTL = 300


def providers(s: Settings | None = None) -> dict[str, dict[str, Any]]:
    s = s or get_settings()
    t = s.microsoft_oauth_tenant or "organizations"
    out: dict[str, dict[str, Any]] = {}
    if s.google_oauth_client_id and s.google_oauth_client_secret:
        out["google"] = {"label": "Google", "client_id": s.google_oauth_client_id, "client_secret": s.google_oauth_client_secret,
                         "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                         "jwks": "https://www.googleapis.com/oauth2/v3/certs", "issuers": {"https://accounts.google.com", "accounts.google.com"}}
    if s.microsoft_oauth_client_id and s.microsoft_oauth_client_secret:
        out["microsoft"] = {"label": "Microsoft", "client_id": s.microsoft_oauth_client_id, "client_secret": s.microsoft_oauth_client_secret,
                            "authorize": f"https://login.microsoftonline.com/{t}/oauth2/v2.0/authorize", "token": f"https://login.microsoftonline.com/{t}/oauth2/v2.0/token",
                            "jwks": f"https://login.microsoftonline.com/{t}/discovery/v2.0/keys", "issuers": None}
    return out


def redirect_uri(provider: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/auth/sso/{provider}/callback"


def _sign(payload: dict[str, Any], ttl: int) -> str:
    s = get_settings()
    return jwt.encode({**payload, "exp": int(time.time()) + ttl, "aud": "kvl-sso"}, s.secret_key, algorithm="HS256")


def _unsign(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], audience="kvl-sso")
    except jwt.PyJWTError:
        return None


def start(provider: str, mode: str, user_id: uuid.UUID | None) -> tuple[str, str]:
    """Return (authorize URL, signed state cookie)."""
    p = providers().get(provider)
    if p is None:
        raise api_error(404, "not_configured", "This sign-in provider is not configured.")
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": p["client_id"], "response_type": "code", "redirect_uri": redirect_uri(provider), "scope": "openid email profile",
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    cookie = _sign({"p": provider, "s": state, "n": nonce, "v": verifier, "m": mode, "u": str(user_id) if user_id else None}, STATE_TTL)
    return f"{p['authorize']}?{urlencode(q)}", cookie


# ---- network seams (replaced in tests) ----
TokenExchange = Callable[[str, dict[str, str]], dict[str, Any]]
SigningKey = Callable[[str, str], Any]


def http_token(url: str, form: dict[str, str]) -> dict[str, Any]:
    import httpx

    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as c:
        r = c.post(url, data=form, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise api_error(502, "sso_token_failed", "The sign-in provider rejected the request.")
    return r.json()


_jwks: dict[str, jwt.PyJWKClient] = {}


def jwks_key(jwks_url: str, id_token: str) -> Any:
    client = _jwks.setdefault(jwks_url, jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600))
    return client.get_signing_key_from_jwt(id_token).key


exchange: TokenExchange = http_token
signing_key: SigningKey = jwks_key


def finish(provider: str, code: str, state: str, cookie: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate state, exchange the code, verify the id_token. Returns (claims, flow)."""
    flow = _unsign(cookie)
    if not flow or flow.get("p") != provider or not secrets.compare_digest(str(flow.get("s")), state):
        raise api_error(400, "sso_state_invalid", "The sign-in attempt expired or was tampered with. Please try again.")
    p = providers().get(provider)
    if p is None:
        raise api_error(404, "not_configured", "This sign-in provider is not configured.")
    tok = exchange(p["token"], {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider), "client_id": p["client_id"],
                                "client_secret": p["client_secret"], "code_verifier": flow["v"]})
    idt = tok.get("id_token") if isinstance(tok, dict) else None
    if not idt:
        raise api_error(502, "sso_token_failed", "The sign-in provider did not return an identity token.")
    try:
        key = signing_key(p["jwks"], idt)
        claims = jwt.decode(idt, key, algorithms=["RS256"], audience=p["client_id"], options={"require": ["exp", "iat", "iss", "sub", "aud"]}, leeway=60)
    except jwt.PyJWTError:
        raise api_error(401, "sso_token_invalid", "The identity token could not be verified.") from None
    if claims.get("nonce") != flow.get("n"):
        raise api_error(401, "sso_token_invalid", "The identity token could not be verified.")
    if p["issuers"] is not None and claims.get("iss") not in p["issuers"]:
        raise api_error(401, "sso_token_invalid", "Unexpected token issuer.")
    if provider == "microsoft":
        tid = claims.get("tid")
        if not tid or claims.get("iss") != f"https://login.microsoftonline.com/{tid}/v2.0" or not claims.get("oid"):
            raise api_error(401, "sso_token_invalid", "Unexpected token issuer.")
    return claims, flow


def subject(provider: str, claims: dict[str, Any]) -> str:
    return f"{claims['tid']}:{claims['oid']}" if provider == "microsoft" else str(claims["sub"])


def pending_cookie(user_id: uuid.UUID, provider: str) -> str:
    return _sign({"two_fa": str(user_id), "p": provider}, PENDING_TTL)


def read_pending(cookie: str | None) -> dict[str, Any] | None:
    d = _unsign(cookie)
    return d if d and d.get("two_fa") else None
