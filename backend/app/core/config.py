from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | staging | production
    database_url: str = "postgresql+psycopg://kvl:kvl_dev_only_pw@127.0.0.1:54329/kvl_panel"
    redis_url: str = "redis://127.0.0.1:6379/5"
    secret_key: str = "dev-only-secret-change-me-0123456789abcdef"
    encryption_key: str = ""  # Fernet key; derived from secret_key in dev/test only
    jwt_issuer: str = "kvl-panel"
    jwt_audience: str = "kvl-panel-web"
    access_ttl_seconds: int = 15 * 60
    refresh_ttl_seconds: int = 14 * 24 * 3600
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"
    otp_ttl_seconds: int = 600
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 30
    lockout_threshold: int = 5
    lockout_seconds: int = 15 * 60
    public_base_url: str = "http://localhost:3000"
    agent_data_dir: str = "/tmp/kvl-agent"

    # Phase 16/17
    support_upload_dir: str = "/tmp/kvl-support-uploads"  # attachment sandbox; must be outside any web root
    support_max_attachment_bytes: int = 5 * 1024 * 1024
    support_sla_business_hours: bool = False  # False = 24x7 SLA clock; True = Mon-Fri 09:00-18:00 UTC
    metrics_token: str = ""  # optional bearer token for Prometheus scrapes of /metrics
    log_json: bool = True
    webhook_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 5

    # Integrations: all optional. Features that need them report "not configured" instead of faking.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    cloudflare_api_base: str = "https://api.cloudflare.com/client/v4"
    n8n_webhook_secret: str = ""
    n8n_base_url: str = ""  # e.g. http://127.0.0.1:5678 (private network only)
    razorpay_api_base: str = "https://api.razorpay.com/v1"

    @property
    def is_dev(self) -> bool:
        return self.env in {"dev", "test"}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.is_dev and (s.secret_key.startswith("dev-only") or not s.encryption_key):
        raise RuntimeError("SECRET_KEY and ENCRYPTION_KEY must be set outside dev/test")
    return s
