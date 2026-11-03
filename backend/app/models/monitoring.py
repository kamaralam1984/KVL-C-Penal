"""Monitoring storage: metric samples (raw + rollups), service status, thresholds and alerts."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin

RESOLUTIONS = ("raw", "5m", "1h")


class MetricSample(Base):
    """One observation (or one rollup bucket). Every measurement is nullable: NULL means 'not measured', never zero."""

    __tablename__ = "metric_samples"
    __table_args__ = (UniqueConstraint("server_id", "resolution", "ts", name="uq_metric_sample"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    resolution: Mapped[str] = mapped_column(String(4), default="raw")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    load1: Mapped[float | None] = mapped_column(Float)
    load5: Mapped[float | None] = mapped_column(Float)
    load15: Mapped[float | None] = mapped_column(Float)
    mem_total: Mapped[int | None] = mapped_column(BigInteger)
    mem_used: Mapped[int | None] = mapped_column(BigInteger)
    mem_available: Mapped[int | None] = mapped_column(BigInteger)
    mem_percent: Mapped[float | None] = mapped_column(Float)
    swap_total: Mapped[int | None] = mapped_column(BigInteger)
    swap_used: Mapped[int | None] = mapped_column(BigInteger)
    swap_percent: Mapped[float | None] = mapped_column(Float)
    disk_max_percent: Mapped[float | None] = mapped_column(Float)
    net_rx_bps: Mapped[float | None] = mapped_column(Float)
    net_tx_bps: Mapped[float | None] = mapped_column(Float)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    disks: Mapped[dict[str, Any] | None]  # {"mounts": [...]}; last observation in the bucket for rollups
    interfaces: Mapped[dict[str, Any] | None]
    detail: Mapped[dict[str, Any] | None]  # {"cpu_cores": [...], "processes": [...], "cpu_count": n}; raw samples only


Index("ix_metric_samples_range", MetricSample.server_id, MetricSample.resolution, MetricSample.ts)


class ServiceStatus(Base, IdMixin):
    __tablename__ = "service_statuses"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_service_status"),)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(20))  # running | stopped | failed | not_configured
    detail: Mapped[str | None] = mapped_column(String(300))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertThreshold(Base, IdMixin):
    """Per-server override. Servers without a row use the defaults in services/monitoring.py."""

    __tablename__ = "alert_thresholds"
    __table_args__ = (UniqueConstraint("server_id", "metric", name="uq_alert_threshold"),)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(40))
    warning: Mapped[float] = mapped_column(Float)
    critical: Mapped[float] = mapped_column(Float)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=900)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Alert(Base, IdMixin):
    """Alert lifecycle: open -> acknowledged -> resolved. At most one unresolved alert per (server, metric)."""

    __tablename__ = "alerts"
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    metric: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # warning | critical
    state: Mapped[str] = mapped_column(String(15), default="open", index=True)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column()  # NULL with resolved_at set = auto-resolved
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_alerts_one_unresolved",
    Alert.server_id,
    Alert.metric,
    unique=True,
    postgresql_where=text("state <> 'resolved'"),
)


class SiteUsageSample(Base):
    """Per-website (or app) resource usage, one row per 5-minute bucket. Byte/request counters are cumulative since the
    agent first read the access log; consumers take differences (a decrease means the counter restarted)."""

    __tablename__ = "site_usage_samples"
    __table_args__ = (UniqueConstraint("server_id", "site", "ts", name="uq_site_usage_sample"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    site: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(8), default="site")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    rss_bytes: Mapped[int | None] = mapped_column(BigInteger)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    procs: Mapped[int | None] = mapped_column(Integer)
    bytes_out_total: Mapped[int | None] = mapped_column(BigInteger)
    requests_total: Mapped[int | None] = mapped_column(BigInteger)
    errors_4xx_total: Mapped[int | None] = mapped_column(BigInteger)
    errors_5xx_total: Mapped[int | None] = mapped_column(BigInteger)


Index("ix_site_usage_range", SiteUsageSample.server_id, SiteUsageSample.site, SiteUsageSample.ts)
