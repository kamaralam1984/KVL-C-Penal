"""Monitoring API: metrics, service health, overview, alerts and thresholds. Platform staff only."""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, require
from app.models.infra import Server
from app.models.monitoring import Alert, AlertThreshold, MetricSample
from app.api.v1.routers.servers import get_server_or_404, platform_only
from app.services import agents, maintenance, monitoring
from app.services.audit import audit

router = APIRouter(tags=["monitoring"])


def read_ctx(ctx: Ctx = Depends(require("monitoring:read"))) -> Ctx:
    return platform_only(ctx)


def write_ctx(ctx: Ctx = Depends(require("monitoring:write"))) -> Ctx:
    return platform_only(ctx)


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/servers/{server_id}/metrics")
def server_metrics(
    server_id: uuid.UUID,
    _: Ctx = Depends(read_ctx),
    db: Session = Depends(get_db),
    range: str = Query(default="24h", max_length=10, description="1h | 6h | 24h | 7d | 30d | 90d | custom"),
    start: datetime | None = None,
    end: datetime | None = None,
    tz: str = Query(default="UTC", max_length=64),
    interval: int | None = Query(default=None, description="bucket seconds: 60, 300, 900, 3600, 21600, 86400"),
):
    server = get_server_or_404(db, server_id)
    now = agents.utcnow()
    if range == "custom":
        start, end = _aware(start), _aware(end)
        if start is None or end is None:
            raise api_error(422, "invalid_range", "start and end are required for a custom range.")
        bucket = interval or monitoring.pick_bucket(end - start)
    elif range in monitoring.RANGES:
        span, default_bucket = monitoring.RANGES[range]
        start, end, bucket = now - span, now, interval or default_bucket
    else:
        raise api_error(422, "invalid_range", f"range must be one of {', '.join(monitoring.RANGES)} or custom.")
    series = monitoring.query_series(db, server.id, start, end, tz, bucket)
    latest = monitoring.latest_samples(db, [server.id]).get(server.id)
    return {
        "server_id": str(server.id),
        "range": range,
        "start": series.start,
        "end": series.end,
        "tz": series.tz,
        "bucket_seconds": series.bucket_seconds,
        "availability": monitoring.server_availability(server, now),
        "last_heartbeat_at": server.last_heartbeat_at,
        "has_data": any(v is not None for p in series.points for k, v in p.items() if k != "t"),
        "points": series.points,
        "latest": monitoring.sample_dict(latest),
    }


@router.get("/servers/{server_id}/metrics/latest")
def latest_metrics(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    server = get_server_or_404(db, server_id)
    s = monitoring.latest_samples(db, [server.id]).get(server.id)
    return {"availability": monitoring.server_availability(server), "last_heartbeat_at": server.last_heartbeat_at, "sample": monitoring.sample_dict(s)}


@router.get("/servers/{server_id}/metrics/samples")
def raw_samples(
    server_id: uuid.UUID,
    _: Ctx = Depends(read_ctx),
    db: Session = Depends(get_db),
    before: datetime | None = Query(default=None, description="cursor: return samples older than this timestamp"),
    limit: int = Query(default=100, ge=1, le=500),
):
    get_server_or_404(db, server_id)
    q = select(MetricSample).where(MetricSample.server_id == server_id, MetricSample.resolution == "raw")
    if before:
        q = q.where(MetricSample.ts < _aware(before))
    rows = db.scalars(q.order_by(MetricSample.ts.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    return {"items": [monitoring.sample_dict(r) for r in page], "next_before": page[-1].ts if len(rows) > limit else None}


@router.get("/servers/{server_id}/sites-usage")
def sites_usage(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, server_id)
    return monitoring.site_usage_latest(db, server_id)


@router.get("/servers/{server_id}/sites-usage/{site}/series")
def site_usage_series(
    server_id: uuid.UUID, site: str = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"),
    _: Ctx = Depends(read_ctx), db: Session = Depends(get_db), range: str = Query(default="24h", max_length=10),
):
    get_server_or_404(db, server_id)
    return monitoring.site_usage_series(db, server_id, site, range)


@router.get("/servers/{server_id}/services")
def server_services(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, server_id)
    return monitoring.service_list(db, server_id)


@router.get("/monitoring/overview")
def overview(_: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    now = agents.utcnow()
    servers = db.scalars(select(Server).order_by(Server.name)).all()
    latest = monitoring.latest_samples(db, [s.id for s in servers]) if servers else {}
    alert_counts = {(r.server_id, r.severity): r.n for r in db.execute(select(Alert.server_id, Alert.severity, func.count().label("n")).where(Alert.state != "resolved").group_by(Alert.server_id, Alert.severity))}
    cards, avail = [], {"connected": 0, "stale": 0, "disconnected": 0, "unknown": 0, "disabled": 0}
    for s in servers:
        a = monitoring.server_availability(s, now)
        avail[a] += 1
        smp = latest.get(s.id)
        svc = monitoring.service_list(db, s.id, now)
        cards.append(
            {
                "id": str(s.id), "name": s.name, "hostname": s.hostname, "availability": a, "agent_version": s.agent_version,
                "last_heartbeat_at": s.last_heartbeat_at,
                "cpu_percent": smp.cpu_percent if smp else None, "mem_percent": smp.mem_percent if smp else None,
                "disk_percent": smp.disk_max_percent if smp else None, "uptime_seconds": smp.uptime_seconds if smp else None,
                "services": {"running": sum(x["state"] == "running" for x in svc), "stopped": sum(x["state"] in {"stopped", "failed"} for x in svc), "not_configured": sum(x["state"] == "not_configured" for x in svc)},
                "open_alerts": {"warning": alert_counts.get((s.id, "warning"), 0), "critical": alert_counts.get((s.id, "critical"), 0)},
            }
        )
    return {
        "generated_at": now,
        "totals": {"servers": len(servers), **avail, "open_alerts": sum(alert_counts.values()), "critical_alerts": sum(v for (_, sev), v in alert_counts.items() if sev == "critical")},
        "servers": cards,
        "policy": {"heartbeat_interval_seconds": monitoring.HEARTBEAT_INTERVAL, "stale_after_seconds": monitoring.STALE_AFTER, "disconnected_after_seconds": monitoring.DISCONNECTED_AFTER},
    }


def alert_out(a: Alert, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(a.id), "server_id": str(a.server_id), "server_name": names.get(a.server_id), "metric": a.metric, "severity": a.severity,
        "state": a.state, "value": a.value, "threshold": a.threshold, "message": a.message, "occurrences": a.occurrences,
        "opened_at": a.opened_at, "last_seen_at": a.last_seen_at, "acknowledged_by": str(a.acknowledged_by) if a.acknowledged_by else None,
        "acknowledged_at": a.acknowledged_at, "resolved_by": str(a.resolved_by) if a.resolved_by else None, "resolved_at": a.resolved_at,
        "auto_resolved": bool(a.resolved_at and a.resolved_by is None),
    }


@router.get("/monitoring/alerts")
def list_alerts(
    _: Ctx = Depends(read_ctx),
    db: Session = Depends(get_db),
    server_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, pattern="^(open|acknowledged|resolved|active)$"),
    severity: str | None = Query(default=None, pattern="^(warning|critical)$"),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=25, ge=1, le=100),
):
    q = select(Alert)
    if server_id:
        q = q.where(Alert.server_id == server_id)
    if state == "active":
        q = q.where(Alert.state != "resolved")
    elif state:
        q = q.where(Alert.state == state)
    if severity:
        q = q.where(Alert.severity == severity)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(Alert.opened_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    names = {s.id: s.name for s in db.scalars(select(Server).where(Server.id.in_({a.server_id for a in rows})))} if rows else {}
    return {"items": [alert_out(a, names) for a in rows], "total": total, "page": page, "page_size": page_size}


def _alert_or_404(db: Session, alert_id: uuid.UUID) -> Alert:
    a = db.scalar(select(Alert).where(Alert.id == alert_id).with_for_update())
    if a is None:
        raise api_error(404, "not_found", "Alert not found.")
    return a


@router.post("/monitoring/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: uuid.UUID, request: Request, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    a = _alert_or_404(db, alert_id)
    if a.state != "open":
        raise api_error(409, "invalid_alert_state", f"Alert is {a.state}; only open alerts can be acknowledged.")
    a.state, a.acknowledged_by, a.acknowledged_at = "acknowledged", ctx.user.id, agents.utcnow()
    audit(db, "alert.acknowledged", actor=ctx.user.id, target_type="alert", target_id=a.id, detail={"server_id": str(a.server_id), "metric": a.metric}, ip=client_ip(request))
    db.commit()
    return alert_out(a, {})


@router.post("/monitoring/alerts/{alert_id}/resolve")
def resolve(alert_id: uuid.UUID, request: Request, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    a = _alert_or_404(db, alert_id)
    if a.state == "resolved":
        raise api_error(409, "invalid_alert_state", "Alert is already resolved.")
    a.state, a.resolved_by, a.resolved_at = "resolved", ctx.user.id, agents.utcnow()
    audit(db, "alert.resolved", actor=ctx.user.id, target_type="alert", target_id=a.id, detail={"server_id": str(a.server_id), "metric": a.metric}, ip=client_ip(request))
    db.commit()
    return alert_out(a, {})


@router.get("/monitoring/thresholds")
def get_thresholds(server_id: uuid.UUID, _: Ctx = Depends(read_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, server_id)
    return [{"metric": m, **d} for m, d in monitoring.effective_thresholds(db, server_id).items()]


class ThresholdIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_id: uuid.UUID
    metric: str = Field(max_length=40)
    warning: float
    critical: float
    cooldown_seconds: int = Field(default=900, ge=60, le=86400)
    enabled: bool = True


@router.put("/monitoring/thresholds")
def put_threshold(body: ThresholdIn, request: Request, ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    get_server_or_404(db, body.server_id)
    monitoring.validate_threshold(body.metric, body.warning, body.critical)
    row = db.scalar(select(AlertThreshold).where(AlertThreshold.server_id == body.server_id, AlertThreshold.metric == body.metric))
    if row is None:
        row = AlertThreshold(server_id=body.server_id, metric=body.metric, warning=body.warning, critical=body.critical)
        db.add(row)
    row.warning, row.critical, row.cooldown_seconds, row.enabled, row.updated_by = body.warning, body.critical, body.cooldown_seconds, body.enabled, ctx.user.id
    audit(db, "threshold.updated", actor=ctx.user.id, target_type="server", target_id=body.server_id, detail={"metric": body.metric, "warning": body.warning, "critical": body.critical, "enabled": body.enabled}, ip=client_ip(request))
    db.commit()
    return [{"metric": m, **d} for m, d in monitoring.effective_thresholds(db, body.server_id).items()]


@router.post("/monitoring/sweep")
def sweep(ctx: Ctx = Depends(write_ctx), db: Session = Depends(get_db)):
    """Run housekeeping now (job timeouts, missing-heartbeat alerts, nonce purge, retention)."""
    return maintenance.run_maintenance(db)
