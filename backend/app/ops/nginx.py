"""Control-plane schemas for Nginx site operations. Mirrors agent/kvl_agent/operations/nginx.py."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ops import register_op

DOC_ROOT_PREFIX = "/var/www/kvl/"
SITE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
ROOT_RE = re.compile(r"^/var/www/kvl/[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9_-]{1,32}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
BACKUP_RE = re.compile(r"^\d{8}T\d{6}Z(-\d{2})?\.conf$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteRef(_Strict):
    site: str

    @field_validator("site")
    @classmethod
    def _site(cls, v: str) -> str:
        if not SITE_RE.fullmatch(v):
            raise ValueError("invalid site name")
        return v


@register_op("nginx.stage_site", timeout=60)
class StageSite(SiteRef):
    server_names: list[str] = Field(min_length=1, max_length=20)
    document_root: str
    runtime: Literal["static", "php", "proxy"] = "static"
    php_version: str | None = Field(default=None, pattern=r"^\d\.\d{1,2}$")
    proxy_port: int | None = Field(default=None, ge=1024, le=65535)

    @field_validator("server_names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        for h in v:
            if not HOST_RE.fullmatch(h):
                raise ValueError(f"invalid hostname: {h[:60]!r}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate hostnames")
        return v

    @field_validator("document_root")
    @classmethod
    def _root(cls, v: str) -> str:
        if not ROOT_RE.fullmatch(v) or ".." in v:
            raise ValueError("document_root must be under /var/www/kvl/<tenant>/<site>/<dir>")
        return v


@register_op("nginx.activate_site", timeout=90)
class ActivateSite(SiteRef):
    expected_hash: str = Field(pattern=HASH_RE.pattern)


@register_op("nginx.reload", timeout=60)
class Reload(_Strict):
    pass


@register_op("nginx.rollback_site", timeout=90)
class RollbackSite(SiteRef):
    backup: str | None = Field(default=None, pattern=BACKUP_RE.pattern)


@register_op("nginx.remove_site", timeout=90, destructive=True)
class RemoveSite(SiteRef):
    pass
