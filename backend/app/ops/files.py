"""Control-plane mirror of agent file-manager operation parameters (agent/kvl_agent/operations/files.py)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ops import register_op

_ROOT = r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$"
_TID = r"^[0-9a-f]{32}$"
MAX_CHUNK = 1024 * 1024
DEFAULT_MAX_UPLOAD = 10 * 1024 * 1024


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Site(_B):
    root: str = Field(pattern=_ROOT)

    @field_validator("root")
    @classmethod
    def _no_dots(cls, v: str) -> str:
        if ".." in v.split("/"):
            raise ValueError("invalid root")
        return v


@register_op("files.list", timeout=30)
class ListP(Site):
    path: str = Field("", max_length=4096)
    limit: int = Field(200, ge=1, le=500)
    offset: int = Field(0, ge=0)
    show_hidden: bool = False
    sort: Literal["name", "size", "mtime"] = "name"


@register_op("files.stat", timeout=15)
class StatP(Site):
    path: str = Field("", max_length=4096)


@register_op("files.read", timeout=30)
class ReadP(Site):
    path: str = Field(max_length=4096)
    max_bytes: int = Field(2 * 1024 * 1024, ge=1, le=2 * 1024 * 1024)


@register_op("files.usage", timeout=120)
class UsageP(Site):
    pass


@register_op("files.write", timeout=60)
class WriteP(Site):
    path: str = Field(min_length=1, max_length=4096)
    content_b64: str = Field(max_length=int(MAX_CHUNK * 1.4) + 16)
    upload_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    chunk_index: int = Field(0, ge=0, le=2000)
    total_chunks: int = Field(1, ge=1, le=2000)
    total_size: int = Field(ge=0)
    overwrite: bool = False
    parents: bool = False
    max_bytes: int = Field(DEFAULT_MAX_UPLOAD, ge=1, le=100 * 1024**2)


@register_op("files.mkdir", timeout=15)
class MkdirP(Site):
    path: str = Field(min_length=1, max_length=4096)
    parents: bool = False


@register_op("files.rename", timeout=30)
class RenameP(Site):
    src: str = Field(min_length=1, max_length=4096)
    dst: str = Field(min_length=1, max_length=4096)
    overwrite: bool = False


@register_op("files.delete", timeout=60, destructive=True)
class DeleteP(Site):
    path: str = Field(min_length=1, max_length=4096)
    confirm: Literal[True]


@register_op("files.trash_list", timeout=15)
class TrashListP(Site):
    pass


@register_op("files.restore", timeout=60)
class RestoreP(Site):
    trash_id: str = Field(pattern=_TID)
    new_path: str | None = Field(None, max_length=4096)


@register_op("files.purge", timeout=300, destructive=True)
class PurgeP(Site):
    trash_id: str | None = Field(None, pattern=_TID)
    empty: bool = False
    confirm: Literal[True]


@register_op("files.archive", timeout=600)
class ArchiveP(Site):
    paths: list[str] = Field(min_length=1, max_length=200)
    dest: str = Field(min_length=1, max_length=4096)
    format: Literal["zip", "tar.gz"] = "zip"
    max_bytes: int = Field(1024**3, ge=1, le=8 * 1024**3)


@register_op("files.extract", timeout=600)
class ExtractP(Site):
    archive: str = Field(min_length=1, max_length=4096)
    dest_dir: str = Field("", max_length=4096)
    overwrite: bool = False
    max_total_bytes: int = Field(1024**3, ge=1, le=8 * 1024**3)
    max_file_bytes: int = Field(512 * 1024**2, ge=1, le=8 * 1024**3)
    max_files: int = Field(10_000, ge=1, le=100_000)
    max_ratio: float = Field(100.0, ge=1, le=1000)


@register_op("files.search", timeout=60)
class SearchP(Site):
    path: str = Field("", max_length=4096)
    query: str = Field(min_length=1, max_length=100)
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)
    max_scan: int = Field(50_000, ge=100, le=500_000)
