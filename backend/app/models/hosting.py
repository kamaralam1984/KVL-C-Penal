"""Phase 6-8 tenant-owned resources: websites, domains, applications, customer databases.

Customer databases live on customer servers; nothing here references the control-plane database credentials.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin


class ResourceEvent(Base, IdMixin):
    """Activity / operation history for a website, application or database."""

    __tablename__ = "resource_events"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    resource_type: Mapped[str] = mapped_column(String(20))  # website | domain | app | database
    resource_id: Mapped[uuid.UUID] = mapped_column(index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ------------------------------------------------------------------ websites / domains
class Website(Base, IdMixin, TimestampMixin):
    __tablename__ = "websites"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(40))
    plan_code: Mapped[str | None] = mapped_column(String(60))
    runtime_type: Mapped[str] = mapped_column(String(20), default="static")  # static | php | proxy
    runtime_version: Mapped[str | None] = mapped_column(String(20))
    public_subdir: Mapped[str] = mapped_column(String(32), default="public")
    document_root: Mapped[str] = mapped_column(String(300))
    proxy_port: Mapped[int | None] = mapped_column(Integer)  # for runtime_type=proxy (apps)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | suspended | archived
    provision_state: Mapped[str] = mapped_column(String(24), default="pending_server")
    provision_message: Mapped[str | None] = mapped_column(String(500))
    stage_job_id: Mapped[uuid.UUID | None] = mapped_column()
    activate_job_id: Mapped[uuid.UUID | None] = mapped_column()
    reload_job_id: Mapped[uuid.UUID | None] = mapped_column()
    rollback_job_id: Mapped[uuid.UUID | None] = mapped_column()
    config_hash: Mapped[str | None] = mapped_column(String(64))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disk_usage_mb: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_website_tenant_slug"),)


class Domain(Base, IdMixin, TimestampMixin):
    __tablename__ = "domains"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    website_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("websites.id", ondelete="SET NULL"), index=True)
    hostname: Mapped[str] = mapped_column(String(253), unique=True)  # normalised IDNA/ASCII, globally unique
    unicode_name: Mapped[str | None] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(12), default="primary")  # primary | alias | subdomain
    verification_token: Mapped[str] = mapped_column(String(64))
    verification_state: Mapped[str] = mapped_column(String(16), default="pending")  # pending | verified | failed | unavailable
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_via: Mapped[str | None] = mapped_column(String(20))  # dns_txt | parent_domain
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registrar: Mapped[str | None] = mapped_column(String(120))


# ------------------------------------------------------------------ applications
class ServerRuntime(Base, IdMixin, TimestampMixin):
    """Catalog of runtime versions a server reports as installed (never free text)."""

    __tablename__ = "server_runtimes"
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    runtime: Mapped[str] = mapped_column(String(12))  # php | nodejs | python
    version: Mapped[str] = mapped_column(String(20))
    installed: Mapped[bool] = mapped_column(Boolean, default=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("server_id", "runtime", "version", name="uq_server_runtime"),)


class Application(Base, IdMixin, TimestampMixin):
    __tablename__ = "applications"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"))
    website_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("websites.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(40))
    runtime: Mapped[str] = mapped_column(String(12))  # php | wordpress | nodejs | python
    runtime_version: Mapped[str] = mapped_column(String(20))
    repo_url: Mapped[str | None] = mapped_column(String(300))
    branch: Mapped[str] = mapped_column(String(100), default="main")
    entrypoint: Mapped[str | None] = mapped_column(String(120))  # node: relative script; python: module:callable
    health_path: Mapped[str] = mapped_column(String(200), default="/")
    port: Mapped[int | None] = mapped_column(Integer)
    canary_port: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="stopped")  # stopped | running | errored | unknown | archived
    provision_state: Mapped[str] = mapped_column(String(24), default="pending_server")
    provision_message: Mapped[str | None] = mapped_column(String(500))
    current_release: Mapped[str | None] = mapped_column(String(40))
    previous_release: Mapped[str | None] = mapped_column(String(40))
    last_job_id: Mapped[uuid.UUID | None] = mapped_column()
    usage: Mapped[dict[str, Any] | None]
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_app_tenant_slug"),)


class AppDeployment(Base, IdMixin):
    __tablename__ = "app_deployments"
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    release_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10), default="deploy")  # deploy | rollback
    branch: Mapped[str | None] = mapped_column(String(100))
    commit: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | succeeded | failed | health_failed
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(1000))
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppEnvVar(Base, IdMixin, TimestampMixin):
    __tablename__ = "app_env_vars"
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(64))
    value_encrypted: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("app_id", "name", name="uq_app_env_name"),)


# ------------------------------------------------------------------ customer databases
class CustomerDatabase(Base, IdMixin, TimestampMixin):
    __tablename__ = "customer_databases"
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"))
    engine: Mapped[str] = mapped_column(String(12))  # mariadb | postgresql
    label: Mapped[str] = mapped_column(String(14))
    db_name: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="pending_server")  # pending_server | provisioning | ready | failed | deleting | deleted
    message: Mapped[str | None] = mapped_column(String(500))
    host: Mapped[str] = mapped_column(String(100), default="localhost")
    port: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    size_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provision_job_id: Mapped[uuid.UUID | None] = mapped_column()
    cleanup_job_id: Mapped[uuid.UUID | None] = mapped_column()
    size_job_id: Mapped[uuid.UUID | None] = mapped_column()
    destroy_job_id: Mapped[uuid.UUID | None] = mapped_column()
    destroy_token_hash: Mapped[str | None] = mapped_column(String(64))
    destroy_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("server_id", "engine", "db_name", name="uq_customer_db_name"),)


class DatabaseUser(Base, IdMixin, TimestampMixin):
    """Only metadata. The password is shown once at creation/rotation and never stored."""

    __tablename__ = "database_users"
    database_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_databases.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    username: Mapped[str] = mapped_column(String(32))
    privilege: Mapped[str] = mapped_column(String(12), default="readwrite")  # readwrite | readonly
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | failed | dropped
    password_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (UniqueConstraint("database_id", "username", name="uq_database_user"),)


class DatabaseBackupRequest(Base, IdMixin):
    __tablename__ = "database_backup_requests"
    database_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_databases.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(10))  # backup | restore
    state: Mapped[str] = mapped_column(String(12), default="requested")  # requested | approved | running | succeeded | failed | rejected
    artifact_ref: Mapped[str | None] = mapped_column(String(120))  # backup file name inside the agent backup area
    source_request_id: Mapped[uuid.UUID | None] = mapped_column()  # restore -> backup request
    checksum: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(500))
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    requested_by: Mapped[uuid.UUID | None] = mapped_column()
    approved_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_app_deploy_app_created", AppDeployment.app_id, AppDeployment.created_at)
