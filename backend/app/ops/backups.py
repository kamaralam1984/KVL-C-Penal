"""Control-plane mirror of agent backup operation parameters (agent/kvl_agent/operations/backups.py)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ops import register_op

_REL = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$"
_SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_DISK = r"^[A-Za-z0-9._-]{6,64}$"
_SHA = r"^[0-9a-f]{64}$"
KINDS = Literal["website_files", "database", "website_config", "saas_config"]


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register_op("backups.init_key", timeout=15)
class InitKeyP(_B):
    pass


@register_op("backups.init_dest", timeout=15)
class InitDestP(_B):
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=_DISK)


@register_op("backups.disk_check", timeout=15)
class DiskCheckP(_B):
    dest_root: str = Field("backups", pattern=_REL)
    required_bytes: int = Field(0, ge=0)
    disk_id: str | None = Field(None, pattern=_DISK)
    reserve_bytes: int = Field(64 * 1024**2, ge=0)


@register_op("backups.create", timeout=3600)
class CreateP(_B):
    kind: KINDS
    name: str = Field(pattern=_SLUG)
    scope: str = Field(pattern=_SLUG)
    sources: list[str] = Field(default_factory=list, max_length=20)
    inline_files: dict[str, str] = Field(default_factory=dict)
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=_DISK)
    encrypt: bool = False
    reserve_bytes: int = Field(64 * 1024**2, ge=0)

    @model_validator(mode="after")
    def _chk(self):
        if not self.sources and not self.inline_files:
            raise ValueError("sources or inline_files required")
        return self


@register_op("backups.verify", timeout=3600)
class VerifyP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=_SHA)
    deep: bool = False
    dest_root: str = Field("backups", pattern=_REL)


@register_op("backups.inspect", timeout=900)
class InspectP(_B):
    archive: str = Field(pattern=_REL)
    dest_root: str = Field("backups", pattern=_REL)
    sample: int = Field(30, ge=0, le=200)


@register_op("backups.restore", timeout=7200, destructive=True)
class RestoreP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=_SHA)
    restore_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mode: Literal["isolated", "overwrite"] = "isolated"
    target: str | None = Field(None, pattern=_REL)
    confirm_overwrite: bool = False
    dest_root: str = Field("backups", pattern=_REL)
    restore_root: str = Field("restores", pattern=_REL)
    disk_id: str | None = Field(None, pattern=_DISK)
    max_total_bytes: int = Field(8 * 1024**3, ge=1)
    reserve_bytes: int = Field(64 * 1024**2, ge=0)

    @model_validator(mode="after")
    def _chk(self):
        if self.mode == "overwrite" and not (self.confirm_overwrite and self.target):
            raise ValueError("overwrite mode requires target and confirm_overwrite=true")
        return self


class ArchRef(_B):
    archive: str = Field(pattern=_REL)
    sha256: str = Field(pattern=_SHA)


@register_op("backups.prune", timeout=900, destructive=True)
class PruneP(_B):
    confirm: Literal[True]
    recovery_point: ArchRef
    delete: list[ArchRef] = Field(min_length=1, max_length=500)
    dest_root: str = Field("backups", pattern=_REL)


@register_op("backups.offsite_copy_local", timeout=3600)
class OffsiteLocalP(_B):
    archive: str = Field(pattern=_REL)
    expected_sha256: str = Field(pattern=_SHA)
    dest_dir: str = Field(pattern=_REL)
    dest_root: str = Field("backups", pattern=_REL)
    disk_id: str | None = Field(None, pattern=_DISK)
