"""Control-plane mirror of agent/kvl_agent/operations/db_browser.py parameters (read-only database browser)."""
import re
from typing import Literal

from pydantic import Field, field_validator

from app.ops import register_op
from app.ops.databases import DbRef

NAME_RE = re.compile(r"^[^\x00-\x1f`\"\\]{1,64}$")


def _name(v: str) -> str:
    if not NAME_RE.fullmatch(v):
        raise ValueError("invalid name")
    return v


@register_op("db.browse_tables", timeout=45)
class Tables(DbRef):
    pass


@register_op("db.browse_describe", timeout=45)
class Describe(DbRef):
    table: str
    _t = field_validator("table")(_name)


@register_op("db.browse_rows", timeout=45)
class Rows(DbRef):
    table: str
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10_000_000)
    order_by: str | None = None
    order_dir: Literal["asc", "desc"] = "asc"
    filter_column: str | None = None
    filter_op: Literal["eq", "contains"] = "eq"
    filter_value: str | None = Field(None, max_length=200)

    _t = field_validator("table")(_name)
    _o = field_validator("order_by", "filter_column")(lambda v: None if v is None else _name(v))

    @field_validator("filter_value")
    @classmethod
    def _fv(cls, v: str | None) -> str | None:
        if v is not None and ("\x00" in v):
            raise ValueError("invalid filter value")
        return v
