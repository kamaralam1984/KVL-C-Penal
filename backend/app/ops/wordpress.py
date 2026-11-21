"""Control-plane mirror of agent/kvl_agent/operations/wordpress.py (no PHP is executed by these ops)."""
import re
from typing import Literal

from pydantic import field_validator

from app.ops import register_op
from app.ops.apps import AppRef, _Strict  # noqa: F401

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
DL_RE = re.compile(r"^https://downloads\.wordpress\.org/(plugin|theme)/([a-z0-9][a-z0-9-]{0,99})(?:\.[0-9][0-9A-Za-z.\-]{0,30})?\.zip$")


def _slug(v: str) -> str:
    if not SLUG_RE.fullmatch(v):
        raise ValueError("invalid slug")
    return v


@register_op("wp.status", timeout=60)
class Status(AppRef):
    pass


@register_op("wp.update_item", timeout=300)
class UpdateItem(AppRef):
    kind: Literal["plugin", "theme"]
    slug: str
    package_url: str
    _s = field_validator("slug")(_slug)

    @field_validator("package_url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not DL_RE.fullmatch(v):
            raise ValueError("package must come from https://downloads.wordpress.org")
        return v


@register_op("wp.rollback_item", timeout=120)
class RollbackItem(AppRef):
    kind: Literal["plugin", "theme"]
    slug: str
    _s = field_validator("slug")(_slug)


@register_op("wp.maintenance", timeout=30)
class Maintenance(AppRef):
    enabled: bool
