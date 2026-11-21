"""Typed per-site web options rendered into the Nginx template (mirrored in backend/app/ops/site_options.py).

Covers the cPanel-style site tools: PHP settings, redirects, password-protected directories, IP deny list and
hotlink protection. Every value is strictly validated; nothing free-form reaches the template.
"""
import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PATH_RE = re.compile(r"^/(?:[A-Za-z0-9._~-]+/?)*$")
USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
SSHA_RE = re.compile(r"^\{SSHA\}[A-Za-z0-9+/]{24,120}={0,2}$")
TARGET_RE = re.compile(r"^(?:https?://(?=[A-Za-z0-9.-]{1,253}(?::\d{2,5})?/)[A-Za-z0-9.-]+(?::\d{2,5})?)?/[A-Za-z0-9._~\-/%?=&+,:@!*()]{0,400}$")
REF_HOST_RE = re.compile(r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_REDIRECTS, MAX_PROTECTED, MAX_USERS, MAX_DENY, MAX_HOTLINK_HOSTS = 50, 10, 20, 200, 20


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _path(v: str) -> str:
    if not PATH_RE.fullmatch(v) or ".." in v.split("/") or "//" in v or len(v) > 200:
        raise ValueError("path must look like /dir/ or /page.html (letters, digits, . _ ~ - /)")
    return v


class PhpSettings(_S):
    memory_limit_mb: int = Field(128, ge=32, le=1024)
    upload_max_mb: int = Field(64, ge=1, le=512)
    post_max_mb: int = Field(64, ge=1, le=512)
    max_execution_time: int = Field(60, ge=5, le=300)
    max_input_vars: int = Field(1000, ge=1000, le=20000)
    display_errors: bool = False

    def php_value(self) -> str:
        return (f"memory_limit={self.memory_limit_mb}M \\n upload_max_filesize={self.upload_max_mb}M \\n post_max_size={self.post_max_mb}M"
                f" \\n max_execution_time={self.max_execution_time} \\n max_input_vars={self.max_input_vars}"
                f" \\n display_errors={'On' if self.display_errors else 'Off'}")

    def body_limit_mb(self) -> int:
        return max(self.upload_max_mb, self.post_max_mb) + 1


class Redirect(_S):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=500)
    code: Literal[301, 302, 307, 308] = 301
    match: Literal["exact", "prefix"] = "exact"

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        if v == "/":
            raise ValueError("cannot redirect the site root; use a domain redirect instead")
        return _path(v)

    @field_validator("target")
    @classmethod
    def _tgt(cls, v: str) -> str:
        if not TARGET_RE.fullmatch(v) or "#" in v or ".." in v.split("?")[0].split("/"):
            raise ValueError("target must be a path like /new or a URL like https://example.com/new")
        return v


class HtUser(_S):
    username: str
    hash: str

    @field_validator("username")
    @classmethod
    def _u(cls, v: str) -> str:
        if not USER_RE.fullmatch(v):
            raise ValueError("invalid username")
        return v

    @field_validator("hash")
    @classmethod
    def _h(cls, v: str) -> str:
        if not SSHA_RE.fullmatch(v):
            raise ValueError("invalid password hash")
        return v


class ProtectedPath(_S):
    path: str = Field(min_length=1, max_length=200)
    users: list[HtUser] = Field(min_length=1, max_length=MAX_USERS)

    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        v = _path(v)
        if not v.endswith("/"):
            raise ValueError("protected path must be a directory ending with /")
        return v

    @model_validator(mode="after")
    def _uniq(self):
        names = [u.username for u in self.users]
        if len(set(names)) != len(names):
            raise ValueError("duplicate usernames")
        return self


class Hotlink(_S):
    enabled: bool = False
    allow_empty_referer: bool = True
    allowed_hosts: list[str] = Field(default_factory=list, max_length=MAX_HOTLINK_HOSTS)

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        out = [h.lower() for h in v]
        for h in out:
            if not REF_HOST_RE.fullmatch(h):
                raise ValueError(f"invalid host: {h[:60]!r}")
        return sorted(set(out))


class SiteOptions(_S):
    php: PhpSettings | None = None
    redirects: list[Redirect] = Field(default_factory=list, max_length=MAX_REDIRECTS)
    protected: list[ProtectedPath] = Field(default_factory=list, max_length=MAX_PROTECTED)
    deny_ips: list[str] = Field(default_factory=list, max_length=MAX_DENY)
    hotlink: Hotlink = Field(default_factory=Hotlink)

    @field_validator("deny_ips")
    @classmethod
    def _ips(cls, v: list[str]) -> list[str]:
        out = []
        for raw in v:
            try:
                net = ipaddress.ip_network(raw.strip(), strict=False)
            except ValueError:
                raise ValueError(f"invalid IP or CIDR: {raw[:60]!r}") from None
            if net.prefixlen < (8 if net.version == 4 else 32):
                raise ValueError(f"range too broad: {net}")
            if net.is_loopback:
                raise ValueError("loopback addresses cannot be blocked")
            out.append(str(net))
        return sorted(set(out), key=lambda s: (":" in s, s))

    @model_validator(mode="after")
    def _no_conflicts(self):
        seen: dict[str, str] = {}
        for r in self.redirects:
            key = ("=" if r.match == "exact" else "^~") + r.source
            if key in seen:
                raise ValueError(f"duplicate redirect for {r.source}")
            seen[key] = "redirect"
        for p in self.protected:
            key = "^~" + p.path
            if key in seen:
                raise ValueError(f"{p.path} is used by both a redirect and a protected directory")
            seen[key] = "protected"
        return self
