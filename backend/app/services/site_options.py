"""Website site tools (PHP settings, redirects, password-protected directories, IP deny list, hotlink protection).

Stored on Website.options as the validated agent schema (password *hashes* only). Saving re-stages the vhost through
the normal nginx pipeline (stage -> nginx -t -> activate -> reload), so a bad change can never reach the live config.
"""
import base64
import hashlib
import hmac
import os
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.hosting import Website
from app.ops.site_options import Hotlink, PhpSettings, Redirect, SiteOptions

RESTAGE_STATES = {"staged", "installed", "active", "rolled_back", "failed", "not_configured"}


def ssha(password: str) -> str:
    """Salted SHA-1 in the {SSHA} format understood by nginx auth_basic_user_file."""
    salt = os.urandom(16)
    return "{SSHA}" + base64.b64encode(hashlib.sha1(password.encode() + salt).digest() + salt).decode()


def ssha_verify(password: str, hashed: str) -> bool:
    raw = base64.b64decode(hashed[6:])
    digest, salt = raw[:20], raw[20:]
    return hmac.compare_digest(hashlib.sha1(password.encode() + salt).digest(), digest)


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserIn(_S):
    username: str = Field(min_length=1, max_length=32)
    password: str | None = Field(None, min_length=8, max_length=128)  # omitted = keep the existing password


class ProtectedIn(_S):
    path: str = Field(min_length=1, max_length=200)
    users: list[UserIn] = Field(min_length=1, max_length=20)


class OptionsIn(_S):
    php: PhpSettings | None = None
    redirects: list[Redirect] = Field(default_factory=list, max_length=50)
    protected: list[ProtectedIn] = Field(default_factory=list, max_length=10)
    deny_ips: list[str] = Field(default_factory=list, max_length=200)
    hotlink: Hotlink = Field(default_factory=Hotlink)


def public_view(site: Website) -> dict[str, Any]:
    """Options for the UI: never returns password hashes."""
    o = SiteOptions.model_validate(site.options or {})
    d = o.model_dump()
    d["protected"] = [{"path": p["path"], "users": [{"username": u["username"]} for u in p["users"]]} for p in d["protected"]]
    return {"options": d, "runtime": site.runtime_type, "php_available": site.runtime_type == "php", "provision_state": site.provision_state}


def build(site: Website, body: OptionsIn) -> SiteOptions:
    if body.php and site.runtime_type != "php":
        raise api_error(422, "validation_error", "PHP settings apply only to PHP websites.")
    old = SiteOptions.model_validate(site.options or {})
    old_hashes = {(p.path, u.username): u.hash for p in old.protected for u in p.users}
    protected = []
    for p in body.protected:
        users = []
        for u in p.users:
            if u.password:
                h = ssha(u.password)
            elif (p.path, u.username) in old_hashes:
                h = old_hashes[(p.path, u.username)]
            else:
                raise api_error(422, "password_required", f"Set a password for new user '{u.username}' on {p.path}.")
            users.append({"username": u.username, "hash": h})
        protected.append({"path": p.path, "users": users})
    try:
        return SiteOptions.model_validate({**body.model_dump(exclude={"protected"}), "protected": protected})
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise api_error(422, "validation_error", f"{loc}: {first.get('msg', 'invalid')}".strip(": ")) from None


def save(db: Session, site: Website, tenant, body: OptionsIn, actor: uuid.UUID | None) -> dict[str, Any]:
    from app.services import websites as wsvc

    if site.status != "active":
        raise api_error(409, "website_not_active", f"Website is {site.status}.")
    if site.provision_state in wsvc.BUSY_STATES:
        raise api_error(409, "provision_in_progress", "A provisioning operation is in progress; try again when it finishes.")
    opts = build(site, body)
    site.options = opts.model_dump()
    restaged = False
    if site.provision_state in RESTAGE_STATES:
        wsvc.start_stage(db, site, tenant, actor)
        restaged = True
    return {"restaged": restaged}


def stage_params(site: Website) -> dict[str, Any] | None:
    if not site.options:
        return None
    d = SiteOptions.model_validate(site.options).model_dump()
    if site.runtime_type != "php":
        d["php"] = None
    return d


def summary(site: Website) -> dict[str, Any]:
    """Counts for audit logs (no hashes)."""
    o = SiteOptions.model_validate(site.options or {})
    return {"php": o.php is not None, "redirects": len(o.redirects), "protected": [p.path for p in o.protected], "deny_ips": len(o.deny_ips), "hotlink": o.hotlink.enabled}
