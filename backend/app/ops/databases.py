"""Control-plane schemas for customer database operations. Mirrors agent/kvl_agent/operations/databases.py.

Passwords travel in job params only until the job finishes (the API scrubs them afterwards) and never in results.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ops import OPERATIONS, register_op

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
# Names the panel generates: <label>_<6 hex> for databases, <db>_<suffix> for users. Anything else (root, postgres, mysql...) is refused.
MANAGED_DB_RE = re.compile(r"^[a-z][a-z0-9]{1,13}_[a-f0-9]{6}$")
MANAGED_USER_RE = re.compile(r"^[a-z][a-z0-9]{1,13}_[a-f0-9]{6}_[a-z][a-z0-9]{0,5}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
ARTIFACT_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}-\d{8}T\d{6}Z\.sql$")
Engine = Literal["mariadb", "postgresql"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _ident(v: str) -> str:
    if not IDENT_RE.fullmatch(v):
        raise ValueError("identifier must match ^[a-z][a-z0-9_]{2,31}$")
    return v


def _db(v: str) -> str:
    _ident(v)
    if not MANAGED_DB_RE.fullmatch(v):
        raise ValueError("not a panel-managed database name")
    return v


def _user(v: str) -> str:
    _ident(v)
    if not MANAGED_USER_RE.fullmatch(v):
        raise ValueError("not a panel-managed user name")
    return v


class DbRef(_Strict):
    engine: Engine
    db_name: str

    _v = field_validator("db_name")(_db)


class UserSpec(DbRef):
    username: str
    privilege: Literal["readwrite", "readonly"] = "readwrite"
    password: str = Field(pattern=PASSWORD_RE.pattern)

    _u = field_validator("username")(_user)


@register_op("db.provision", timeout=120)
class Provision(UserSpec):
    pass


@register_op("db.create_user", timeout=60)
class CreateUser(UserSpec):
    pass


@register_op("db.rotate_password", timeout=60)
class RotatePassword(_Strict):
    engine: Engine
    username: str
    password: str = Field(pattern=PASSWORD_RE.pattern)

    _u = field_validator("username")(_user)


@register_op("db.drop_user", timeout=60, destructive=True)
class DropUser(_Strict):
    engine: Engine
    username: str

    _u = field_validator("username")(_user)


@register_op("db.drop", timeout=120, destructive=True)
class Drop(DbRef):
    confirm_name: str
    users: list[str] = Field(default_factory=list, max_length=20)

    _u = field_validator("users")(lambda v: [_user(x) for x in v])

    @field_validator("confirm_name")
    @classmethod
    def _c(cls, v: str) -> str:
        return _db(v)


@register_op("db.size", timeout=60)
class Size(DbRef):
    pass


class Dump(DbRef):
    artifact: str = Field(pattern=ARTIFACT_RE.pattern)


class Restore(DbRef):
    artifact: str = Field(pattern=ARTIFACT_RE.pattern)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


# The backup engine (another module) may own db.dump/db.restore; register ours only if nobody has.
if "db.dump" not in OPERATIONS:
    register_op("db.dump", timeout=900)(Dump)
if "db.restore" not in OPERATIONS:
    register_op("db.restore", timeout=900, destructive=True)(Restore)


@register_op("db.detect_engines", timeout=60)
class DetectEngines(_Strict):
    pass
