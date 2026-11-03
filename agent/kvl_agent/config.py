"""Agent configuration: environment variables and an optional JSON file. Secrets are never logged or repr'd."""
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ENV_PREFIX = "KVL_AGENT_"


class ConfigError(Exception):
    pass


@dataclass
class AgentConfig:
    api_url: str = ""
    key_id: str = ""
    secret: str = field(default="", repr=False)
    data_dir: Path = Path("/var/lib/kvl-agent")
    heartbeat_interval: float = 30.0
    poll_interval: float = 5.0
    max_concurrency: int = 2
    max_output: int = 16 * 1024
    health_port: int = 9710
    verify_tls: bool = True
    ca_bundle: str | None = None
    allow_insecure_http: bool = False  # development only; loopback http is always accepted
    request_timeout: float = 15.0
    shutdown_grace: float = 20.0
    dry_run: bool = False
    log_level: str = "INFO"

    def __repr__(self) -> str:  # never include the secret
        return f"AgentConfig(api_url={self.api_url!r}, key_id={self.key_id!r}, data_dir={str(self.data_dir)!r}, secret={'set' if self.secret else 'unset'})"

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def enrolled(self) -> bool:
        return bool(self.key_id and self.secret)

    def validate(self, require_credentials: bool = True) -> None:
        u = urlparse(self.api_url)
        if u.scheme not in {"http", "https"} or not u.hostname:
            raise ConfigError("api_url must be an http(s) URL")
        loopback = u.hostname in {"127.0.0.1", "localhost", "::1"}
        if u.scheme == "http" and not (loopback or self.allow_insecure_http):
            raise ConfigError("api_url must use https (plain http is only accepted for loopback or with allow_insecure_http)")
        if not self.verify_tls and u.scheme == "https" and not self.allow_insecure_http:
            raise ConfigError("TLS verification cannot be disabled without allow_insecure_http")
        if require_credentials and not self.enrolled:
            raise ConfigError("agent is not enrolled: run `python -m kvl_agent enroll`")
        if not 1 <= self.max_concurrency <= 8:
            raise ConfigError("max_concurrency must be 1..8")
        if self.heartbeat_interval < 5 or self.poll_interval < 1:
            raise ConfigError("intervals too small")


def _bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on"}


def check_private_file(path: Path) -> None:
    """Refuse credential files readable by group/other (POSIX). Fail closed."""
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(f"{path} must not be accessible by group/other (chmod 600)")


def load_config(env: dict | None = None, config_file: str | None = None) -> AgentConfig:
    env = dict(os.environ if env is None else env)
    cfg = AgentConfig()
    file_path = config_file or env.get(ENV_PREFIX + "CONFIG")
    data: dict = {}
    if file_path:
        p = Path(file_path)
        check_private_file(p)
        data = json.loads(p.read_text())
    for k, v in env.items():
        if k.startswith(ENV_PREFIX) and k != ENV_PREFIX + "CONFIG":
            data[k[len(ENV_PREFIX):].lower()] = v
    types = {"heartbeat_interval": float, "poll_interval": float, "request_timeout": float, "shutdown_grace": float,
             "max_concurrency": int, "max_output": int, "health_port": int, "verify_tls": _bool, "allow_insecure_http": _bool, "dry_run": _bool, "data_dir": Path}
    for k, v in data.items():
        if k == "secret_file":
            sp = Path(v)
            check_private_file(sp)
            cfg.secret = sp.read_text().strip()
        elif hasattr(cfg, k) and k not in {"credentials_path", "enrolled"}:
            try:
                setattr(cfg, k, types.get(k, str)(v))
            except (TypeError, ValueError) as e:
                raise ConfigError(f"invalid value for {k}") from e
    # Credentials written by `enroll` are used unless explicitly provided.
    if not cfg.enrolled and cfg.credentials_path.exists():
        check_private_file(cfg.credentials_path)
        creds = json.loads(cfg.credentials_path.read_text())
        cfg.key_id, cfg.secret = creds.get("key_id", ""), creds.get("secret", "")
    return cfg


def save_credentials(cfg: AgentConfig, key_id: str, secret: str) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.credentials_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"key_id": key_id, "secret": secret}, f)
    os.replace(tmp, cfg.credentials_path)
    cfg.key_id, cfg.secret = key_id, secret
