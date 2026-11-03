"""Control-plane schemas for application/runtime operations. Mirrors agent/kvl_agent/operations/apps.py."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ops import register_op

APP_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
RELEASE_RE = re.compile(r"^\d{14}-[a-f0-9]{6}$")
VERSION_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,2}$")
BRANCH_RE = re.compile(r"^(?!.*\.\.)(?!.*//)(?!/)(?!.*/$)(?!.*\.lock$)(?!.*@\{)[A-Za-z0-9][A-Za-z0-9._/-]{0,98}$")
HEALTH_RE = re.compile(r"^/[A-Za-z0-9/_.\-]{0,150}(\?[A-Za-z0-9_=&.\-]{0,40})?$")
ENTRY_NODE_RE = re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./-]{0,98}\.(?:js|mjs|cjs)$")
ENTRY_PY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,60}:[A-Za-z_][A-Za-z0-9_]{0,40}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
_HTTPS = re.compile(r"^https://(?P<host>[a-z0-9.-]+)/(?P<path>[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){1,3}?)(?:\.git)?/?$")
_SSH = re.compile(r"^(?:ssh://git@(?P<h1>[a-z0-9.-]+)/|git@(?P<h2>[a-z0-9.-]+):)(?P<path>[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){1,3}?)(?:\.git)?$")


def check_repo_url(url: str, hosts: tuple[str, ...] = GIT_HOSTS) -> str:
    """https:// or ssh git URLs on allow-listed hosts only. No credentials, ports, file://, local paths or option-like values."""
    if not isinstance(url, str) or len(url) > 300 or url != url.strip() or url.startswith("-"):
        raise ValueError("invalid repository URL")
    m = _HTTPS.match(url) or _SSH.match(url)
    if not m:
        raise ValueError("Repository URL must be https://<host>/<owner>/<repo>[.git] or git@<host>:<owner>/<repo>.git")
    host = m.groupdict().get("host") or m.groupdict().get("h1") or m.groupdict().get("h2")
    if host not in hosts:
        raise ValueError(f"Git host '{host}' is not on the allow-list ({', '.join(hosts)})")
    if ".." in m.group("path"):
        raise ValueError("invalid repository path")
    return url


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppRef(_Strict):
    app: str

    @field_validator("app")
    @classmethod
    def _app(cls, v: str) -> str:
        if not APP_RE.fullmatch(v):
            raise ValueError("invalid app name")
        return v


@register_op("apps.detect_runtimes", timeout=60)
class DetectRuntimes(_Strict):
    pass


@register_op("apps.deploy", timeout=900)
class Deploy(AppRef):
    runtime: Literal["php", "wordpress", "nodejs", "python"]
    version: str = Field(pattern=VERSION_RE.pattern)
    repo_url: str
    branch: str
    release_id: str = Field(pattern=RELEASE_RE.pattern)
    port: int | None = Field(default=None, ge=20000, le=29999)
    canary_port: int | None = Field(default=None, ge=20000, le=29999)
    health_path: str = "/"
    entrypoint: str | None = None
    env: dict[str, str] = Field(default_factory=dict, max_length=100)
    keep_releases: int = Field(default=5, ge=2, le=10)

    @field_validator("repo_url")
    @classmethod
    def _repo(cls, v: str) -> str:
        return check_repo_url(v)

    @field_validator("branch")
    @classmethod
    def _branch(cls, v: str) -> str:
        if not BRANCH_RE.fullmatch(v):
            raise ValueError("invalid branch name")
        return v

    @field_validator("health_path")
    @classmethod
    def _health(cls, v: str) -> str:
        if not HEALTH_RE.fullmatch(v):
            raise ValueError("invalid health check path")
        return v

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if not ENV_NAME_RE.fullmatch(k):
                raise ValueError(f"invalid environment variable name {k[:30]!r}")
            if len(val) > 4096 or any(c in val for c in "\x00\r\n"):
                raise ValueError(f"invalid value for {k}")
        return v


@register_op("apps.rollback", timeout=300)
class Rollback(AppRef):
    to_release: str | None = Field(default=None, pattern=RELEASE_RE.pattern)
    runtime: Literal["php", "wordpress", "nodejs", "python"]
    port: int | None = Field(default=None, ge=20000, le=29999)
    health_path: str = "/"


@register_op("apps.process", timeout=60)
class Process(AppRef):
    action: Literal["start", "stop", "restart", "status"]
    runtime: Literal["nodejs", "python"]


@register_op("apps.logs", timeout=30)
class Logs(AppRef):
    stream: Literal["out", "err"] = "out"
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=100, ge=1, le=500)


@register_op("apps.usage", timeout=60)
class Usage(AppRef):
    pass


@register_op("apps.remove", timeout=120, destructive=True)
class Remove(AppRef):
    pass
