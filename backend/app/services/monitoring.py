"""Monitoring: sample ingestion, availability, time-series queries, retention, thresholds and alerting.

Policies (documented in docs/phase-reports/PHASE-05.md):
- Heartbeat every 30 s. Availability: connected <= 90 s, stale <= 300 s, disconnected > 300 s, unknown = never seen.
- Retention: raw samples 48 h, 5-minute rollups 14 d, 1-hour rollups 400 d (see `downsample_and_prune`).
- Missing is not zero: every measurement is nullable and empty buckets are returned as null.
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import api_error
from app.models.infra import Server
from app.models.hosting import Website
from app.models.monitoring import Alert, AlertThreshold, MetricSample, ServiceStatus, SiteUsageSample
from app.models.tenant import Tenant
from app.services.audit import audit

HEARTBEAT_INTERVAL = 30
STALE_AFTER = 90
DISCONNECTED_AFTER = 300
RETENTION = {"raw": timedelta(hours=48), "5m": timedelta(days=14), "1h": timedelta(days=400)}
KNOWN_SERVICES = ("nginx", "php-fpm", "mariadb", "redis", "pm2")
SERVICE_STATES = {"running", "stopped", "failed", "not_configured"}

# metric -> (warning, critical, cooldown_seconds, unit, label)
DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "cpu_percent": {"warning": 80.0, "critical": 95.0, "cooldown_seconds": 900, "unit": "%", "label": "CPU utilisation"},
    "mem_percent": {"warning": 85.0, "critical": 95.0, "cooldown_seconds": 900, "unit": "%", "label": "Memory used"},
    "disk_percent": {"warning": 80.0, "critical": 90.0, "cooldown_seconds": 3600, "unit": "%", "label": "Fullest disk mount"},
    "swap_percent": {"warning": 50.0, "critical": 80.0, "cooldown_seconds": 900, "unit": "%", "label": "Swap used"},
    "heartbeat_age_seconds": {"warning": 90.0, "critical": 300.0, "cooldown_seconds": 300, "unit": "s", "label": "Time since last heartbeat"},
}

# interval (seconds) used for each named range; every choice yields <= ~170 points
RANGES: dict[str, tuple[timedelta, int]] = {
    "1h": (timedelta(hours=1), 60),
    "6h": (timedelta(hours=6), 300),
    "24h": (timedelta(hours=24), 900),
    "7d": (timedelta(days=7), 3600),
    "30d": (timedelta(days=30), 21600),
    "90d": (timedelta(days=90), 86400),
}
BUCKET_CHOICES = (60, 300, 900, 3600, 21600, 86400)
MAX_POINTS = 600
MAX_SPAN = timedelta(days=400)

SERIES_COLUMNS = (
    "cpu_percent", "load1", "load5", "load15", "mem_percent", "mem_used", "mem_total", "swap_percent",
    "disk_max_percent", "net_rx_bps", "net_tx_bps",
)
SITE_BUCKET = 300
SITE_RETENTION = timedelta(days=30)
SITE_FIELDS = ("disk_bytes", "rss_bytes", "cpu_percent", "procs", "bytes_out_total", "requests_total", "errors_4xx_total", "errors_5xx_total")
ROLLUP_COLUMNS = SERIES_COLUMNS + ("mem_available", "swap_total", "swap_used")


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ availability
def availability(last_heartbeat_at: datetime | None, now: datetime | None = None) -> str:
    if last_heartbeat_at is None:
        return "unknown"
    age = ((now or utcnow()) - last_heartbeat_at).total_seconds()
    if age <= STALE_AFTER:
        return "connected"
    return "stale" if age <= DISCONNECTED_AFTER else "disconnected"


def server_availability(server: Server, now: datetime | None = None) -> str:
    if server.status == "disabled":
        return "disabled"
    return availability(server.last_heartbeat_at, now)


# ------------------------------------------------------------------ ingestion
def _pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None  # no total (or no swap configured) is "not applicable", not 0 %
    return round(used / total * 100, 2)


def build_sample(server_id: uuid.UUID, metrics: dict[str, Any], now: datetime) -> MetricSample:
    mem, swap, load, net = (metrics.get(k) or {} for k in ("memory", "swap", "load", "network"))
    disks = [d for d in (metrics.get("disks") or []) if d.get("percent") is not None]
    return MetricSample(
        server_id=server_id,
        resolution="raw",
        sample_count=1,
        ts=now.replace(microsecond=0),
        cpu_percent=metrics.get("cpu_percent"),
        load1=load.get("load1"),
        load5=load.get("load5"),
        load15=load.get("load15"),
        mem_total=mem.get("total"),
        mem_used=mem.get("used"),
        mem_available=mem.get("available"),
        mem_percent=_pct(mem.get("used"), mem.get("total")),
        swap_total=swap.get("total"),
        swap_used=swap.get("used"),
        swap_percent=_pct(swap.get("used"), swap.get("total")),
        disk_max_percent=max((d["percent"] for d in disks), default=None),
        net_rx_bps=net.get("rx_bytes_per_s"),
        net_tx_bps=net.get("tx_bytes_per_s"),
        uptime_seconds=metrics.get("uptime_seconds"),
        disks={"mounts": metrics.get("disks")} if metrics.get("disks") is not None else None,
        interfaces={"interfaces": net.get("interfaces")} if net.get("interfaces") is not None else None,
        detail={k: v for k, v in (("cpu_cores", metrics.get("cpu_cores")), ("processes", metrics.get("processes")), ("cpu_count", metrics.get("cpu_count"))) if v is not None} or None,
    )


def ingest_heartbeat(db: Session, server: Server, payload: dict[str, Any], now: datetime | None = None) -> MetricSample | None:
    now = now or utcnow()
    server.last_heartbeat_at = now
    server.agent_version = payload.get("agent_version") or server.agent_version
    info = dict(server.os_info or {})
    if payload.get("os_info"):
        info.update(payload["os_info"])
    if payload.get("update_status"):
        info["update_status"] = payload["update_status"]
    server.os_info = info or None
    if payload.get("hostname") and not server.hostname:
        server.hostname = payload["hostname"]
    sample = None
    if payload.get("metrics"):
        sample = build_sample(server.id, payload["metrics"], now)
        values = {c.name: getattr(sample, c.name) for c in MetricSample.__table__.columns if c.name != "id"}
        db.execute(pg_insert(MetricSample).values(**values).on_conflict_do_nothing(constraint="uq_metric_sample"))
    for site in payload.get("sites") or []:
        ts5 = datetime.fromtimestamp(int(now.timestamp()) // SITE_BUCKET * SITE_BUCKET, UTC)
        vals = {k: site.get(k) for k in SITE_FIELDS}
        stmt = pg_insert(SiteUsageSample).values(server_id=server.id, site=site["name"], kind=site.get("kind") or "site", ts=ts5, **vals)
        db.execute(stmt.on_conflict_do_update(constraint="uq_site_usage_sample", set_={"kind": stmt.excluded.kind, **{k: getattr(stmt.excluded, k) for k in SITE_FIELDS}}))
    for svc in payload.get("services") or []:
        state = svc["state"] if svc["state"] in SERVICE_STATES else "not_configured"
        stmt = pg_insert(ServiceStatus).values(server_id=server.id, name=svc["name"], state=state, detail=svc.get("detail"), checked_at=now)
        db.execute(stmt.on_conflict_do_update(constraint="uq_service_status", set_={"state": state, "detail": svc.get("detail"), "checked_at": now}))
    if sample is not None:
        evaluate_sample(db, server, sample, now)
    evaluate_heartbeat(db, server, now)
    return sample


# ------------------------------------------------------------------ thresholds
def effective_thresholds(db: Session, server_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    out = {m: dict(d, enabled=True, customized=False) for m, d in DEFAULT_THRESHOLDS.items()}
    for t in db.scalars(select(AlertThreshold).where(AlertThreshold.server_id == server_id)):
        if t.metric in out:
            out[t.metric].update(warning=t.warning, critical=t.critical, cooldown_seconds=t.cooldown_seconds, enabled=t.enabled, customized=True)
    return out


def validate_threshold(metric: str, warning: float, critical: float) -> None:
    if metric not in DEFAULT_THRESHOLDS:
        raise api_error(422, "unknown_metric", f"Unknown metric '{metric}'.")
    if not warning < critical:
        raise api_error(422, "invalid_threshold", "Warning must be lower than critical.")
    pct = DEFAULT_THRESHOLDS[metric]["unit"] == "%"
    lo, hi = (1.0, 100.0) if pct else (30.0, 86400.0)
    if not (lo <= warning and critical <= hi):
        raise api_error(422, "invalid_threshold", f"Thresholds for {metric} must be between {lo:g} and {hi:g}.")


# ------------------------------------------------------------------ alerting
def _level(value: float | None, warning: float, critical: float) -> str | None:
    if value is None:
        return None
    if value >= critical:
        return "critical"
    return "warning" if value >= warning else None


def evaluate_metric(db: Session, server: Server, metric: str, value: float | None, thr: dict[str, Any], now: datetime) -> str:
    """Apply one reading. Returns opened | updated | escalated | resolved | suppressed | none | skipped."""
    if not thr["enabled"] or value is None:
        return "skipped"  # a missing reading never opens or resolves anything
    level = _level(value, thr["warning"], thr["critical"])
    limit = thr["critical"] if level == "critical" else thr["warning"]
    unit = DEFAULT_THRESHOLDS[metric]["unit"]
    label = DEFAULT_THRESHOLDS[metric]["label"]
    open_alert = db.scalar(select(Alert).where(Alert.server_id == server.id, Alert.metric == metric, Alert.state != "resolved"))
    if open_alert:
        if level is None:
            open_alert.state, open_alert.resolved_at, open_alert.resolved_by, open_alert.last_seen_at = "resolved", now, None, now
            audit(db, "alert.auto_resolved", target_type="alert", target_id=open_alert.id, detail={"server_id": str(server.id), "metric": metric})
            return "resolved"
        open_alert.occurrences += 1
        open_alert.last_seen_at, open_alert.value = now, value
        if level != open_alert.severity:
            escalated = level == "critical"
            open_alert.severity, open_alert.threshold = level, limit
            open_alert.message = f"{label} {value:g}{unit} reached the {level} threshold ({limit:g}{unit})."
            if escalated:
                open_alert.state, open_alert.acknowledged_at, open_alert.acknowledged_by = "open", None, None
            audit(db, "alert.severity_changed", target_type="alert", target_id=open_alert.id, detail={"server_id": str(server.id), "metric": metric, "severity": level})
            return "escalated"
        return "updated"
    if level is None:
        return "none"
    last_resolved = db.scalar(select(func.max(Alert.resolved_at)).where(Alert.server_id == server.id, Alert.metric == metric))
    if last_resolved and (now - last_resolved).total_seconds() < thr["cooldown_seconds"]:
        return "suppressed"  # cooldown: do not re-open a flapping or manually resolved alert
    try:
        with db.begin_nested():
            alert = Alert(
                server_id=server.id, metric=metric, severity=level, value=value, threshold=limit, opened_at=now, last_seen_at=now,
                message=f"{label} {value:g}{unit} reached the {level} threshold ({limit:g}{unit}).",
            )
            db.add(alert)
            db.flush()
    except IntegrityError:
        return "updated"  # lost a race with a concurrent evaluation; the existing alert is the dedup target
    audit(db, "alert.opened", target_type="alert", target_id=alert.id, detail={"server_id": str(server.id), "metric": metric, "severity": level})
    return "opened"


def evaluate_sample(db: Session, server: Server, sample: MetricSample, now: datetime) -> dict[str, str]:
    thr = effective_thresholds(db, server.id)
    readings = {
        "cpu_percent": sample.cpu_percent,
        "mem_percent": sample.mem_percent,
        "disk_percent": sample.disk_max_percent,
        "swap_percent": sample.swap_percent,
    }
    return {m: evaluate_metric(db, server, m, v, thr[m], now) for m, v in readings.items()}


def evaluate_heartbeat(db: Session, server: Server, now: datetime) -> str:
    if server.status == "disabled" or server.last_heartbeat_at is None:
        return "skipped"  # never seen = unknown, not an outage
    age = (now - server.last_heartbeat_at).total_seconds()
    return evaluate_metric(db, server, "heartbeat_age_seconds", max(age, 0.0), effective_thresholds(db, server.id)["heartbeat_age_seconds"], now)


def sweep_heartbeats(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    changed = 0
    for s in db.scalars(select(Server).where(Server.status == "registered")):
        if evaluate_heartbeat(db, s, now) in {"opened", "escalated", "resolved"}:
            changed += 1
    return changed


# ------------------------------------------------------------------ retention
def _floor(ts: datetime, bucket: int) -> datetime:
    return datetime.fromtimestamp(int(ts.timestamp()) // bucket * bucket, UTC)


def _rollup(db: Session, src: str, dst: str, bucket: int, cutoff: datetime, batch: int = 50000) -> tuple[int, int]:
    cutoff = _floor(cutoff, bucket)  # only whole buckets are rolled up
    rows = db.scalars(select(MetricSample).where(MetricSample.resolution == src, MetricSample.ts < cutoff).order_by(MetricSample.server_id, MetricSample.ts).limit(batch)).all()
    groups: dict[tuple[uuid.UUID, datetime], list[MetricSample]] = {}
    for r in rows:
        groups.setdefault((r.server_id, _floor(r.ts, bucket)), []).append(r)
    batch_rows: list[dict[str, Any]] = []
    for (server_id, start), members in groups.items():
        vals: dict[str, Any] = {"server_id": server_id, "resolution": dst, "ts": start, "sample_count": sum(m.sample_count for m in members)}
        for col in ROLLUP_COLUMNS:
            pairs = [(getattr(m, col), m.sample_count) for m in members if getattr(m, col) is not None]
            w = sum(c for _, c in pairs)
            avg = (sum(v * c for v, c in pairs) / w) if w else None
            vals[col] = None if avg is None else (round(avg) if col.startswith(("mem_", "swap_")) and not col.endswith("percent") else avg)
        last = members[-1]
        vals.update(uptime_seconds=last.uptime_seconds, disks=last.disks, interfaces=last.interfaces)
        batch_rows.append(vals)
    made = len(batch_rows)
    if batch_rows:  # one round trip per 1000 buckets; existing buckets are left untouched
        for i in range(0, len(batch_rows), 1000):
            db.execute(pg_insert(MetricSample).on_conflict_do_nothing(constraint="uq_metric_sample"), batch_rows[i : i + 1000])
    if rows:
        for i in range(0, len(rows), 5000):
            db.execute(delete(MetricSample).where(MetricSample.id.in_([r.id for r in rows[i : i + 5000]])))
    return len(rows), made


def downsample_and_prune(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Apply the retention policy. Idempotent; call periodically (hourly is plenty)."""
    now = now or utcnow()
    raw_rolled, m5 = _rollup(db, "raw", "5m", 300, now - RETENTION["raw"])
    m5_rolled, h1 = _rollup(db, "5m", "1h", 3600, now - RETENTION["5m"])
    dropped = db.execute(delete(MetricSample).where(MetricSample.resolution == "1h", MetricSample.ts < now - RETENTION["1h"])).rowcount or 0
    sites_dropped = db.execute(delete(SiteUsageSample).where(SiteUsageSample.ts < now - SITE_RETENTION)).rowcount or 0
    return {"raw_rolled": raw_rolled, "created_5m": m5, "5m_rolled": m5_rolled, "created_1h": h1, "expired_1h": dropped, "expired_site_usage": sites_dropped}


# ------------------------------------------------------------------ queries
def parse_tz(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise api_error(422, "invalid_timezone", f"Unknown timezone '{tz}'.") from None


def pick_bucket(span: timedelta) -> int:
    for b in BUCKET_CHOICES:
        if span.total_seconds() / b <= MAX_POINTS:
            return b
    return BUCKET_CHOICES[-1]


_ORIGIN = datetime(2000, 1, 1)


def _local_floor(local: datetime, step: int) -> datetime:
    return _ORIGIN + timedelta(seconds=int((local - _ORIGIN).total_seconds()) // step * step)


@dataclass
class Series:
    start: datetime
    end: datetime
    tz: str
    bucket_seconds: int
    points: list[dict[str, Any]]


def query_series(db: Session, server_id: uuid.UUID, start: datetime, end: datetime, tz: str, bucket_seconds: int) -> Series:
    """Bucketed averages in the caller's timezone; buckets with no samples are returned as nulls (never 0)."""
    zone = parse_tz(tz)
    if bucket_seconds not in BUCKET_CHOICES:
        raise api_error(422, "invalid_interval", f"interval must be one of {BUCKET_CHOICES}.")
    if end <= start or end - start > MAX_SPAN:
        raise api_error(422, "invalid_range", "Range must be positive and at most 400 days.")
    if (end - start).total_seconds() / bucket_seconds > MAX_POINTS * 2:
        raise api_error(422, "too_many_points", "Interval too small for this range.")
    local_ts = func.timezone(tz, MetricSample.ts)
    bucket = func.date_bin(literal_column(f"INTERVAL '{int(bucket_seconds)} seconds'"), local_ts, literal_column("TIMESTAMP '2000-01-01 00:00:00'")).label("b")
    aggs = []
    for c in SERIES_COLUMNS:
        col = getattr(MetricSample, c)
        num = func.sum(col * MetricSample.sample_count).filter(col.is_not(None))
        den = func.nullif(func.sum(MetricSample.sample_count).filter(col.is_not(None)), 0)
        aggs.append((num / den).label(c))
    rows = db.execute(
        select(bucket, *aggs).where(MetricSample.server_id == server_id, MetricSample.ts >= start, MetricSample.ts < end).group_by(bucket).order_by(bucket)
    ).all()
    found = {r.b: r for r in rows}
    cur = _local_floor(start.astimezone(zone).replace(tzinfo=None), bucket_seconds)
    last = end.astimezone(zone).replace(tzinfo=None)
    points: list[dict[str, Any]] = []
    while cur < last:
        r = found.get(cur)
        p: dict[str, Any] = {"t": cur.replace(tzinfo=zone).isoformat()}
        for c in SERIES_COLUMNS:
            v = getattr(r, c) if r is not None else None
            p[c] = None if v is None else round(float(v), 3)
        points.append(p)
        cur += timedelta(seconds=bucket_seconds)
    return Series(start, end, tz, bucket_seconds, points)


def latest_samples(db: Session, server_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, MetricSample]:
    q = select(MetricSample).where(MetricSample.resolution == "raw").distinct(MetricSample.server_id).order_by(MetricSample.server_id, MetricSample.ts.desc())
    if server_ids is not None:
        q = q.where(MetricSample.server_id.in_(server_ids))
    return {s.server_id: s for s in db.scalars(q)}


def sample_dict(s: MetricSample | None) -> dict[str, Any] | None:
    if s is None:
        return None
    d = {c: getattr(s, c) for c in (*ROLLUP_COLUMNS, "uptime_seconds", "sample_count")}
    det = s.detail or {}
    d.update(ts=s.ts.isoformat(), resolution=s.resolution, disks=(s.disks or {}).get("mounts"), interfaces=(s.interfaces or {}).get("interfaces"),
             cpu_cores=det.get("cpu_cores"), processes=det.get("processes"), cpu_count=det.get("cpu_count"))
    return d


def service_list(db: Session, server_id: uuid.UUID, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    have = {s.name: s for s in db.scalars(select(ServiceStatus).where(ServiceStatus.server_id == server_id))}
    out = []
    for name in [*KNOWN_SERVICES, *sorted(set(have) - set(KNOWN_SERVICES))]:
        s = have.get(name)
        if s is None:
            out.append({"name": name, "state": "not_configured", "detail": "No report from the agent yet.", "checked_at": None, "stale": False})
        else:
            out.append({"name": name, "state": s.state, "detail": s.detail, "checked_at": s.checked_at.isoformat(), "stale": (now - s.checked_at).total_seconds() > DISCONNECTED_AFTER})
    return out


# ------------------------------------------------------------------ per-website usage
def _delta(vals: list[int | None]) -> list[int | None]:
    """Differences of a cumulative counter. The first point and any restart (value went down) have no delta: None, not 0."""
    out: list[int | None] = [None]
    for a, b in zip(vals, vals[1:], strict=False):
        out.append(b - a if a is not None and b is not None and b >= a else None)
    return out


def site_owners(db: Session, server_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """Agent-side names (w-<16 hex of website id>, app names) -> website/tenant labels."""
    out: dict[str, dict[str, Any]] = {}
    for w, t in db.execute(select(Website, Tenant).join(Tenant, Tenant.id == Website.tenant_id).where(Website.server_id == server_id)):
        out[f"w-{w.id.hex[:16]}"] = {"kind": "website", "id": str(w.id), "name": w.name, "tenant_id": str(t.id), "tenant": t.name}
    return out


def site_usage_latest(db: Session, server_id: uuid.UUID, now: datetime | None = None, only: str | None = None) -> dict[str, Any]:
    """Newest sample per site, plus data transferred in the last 24 h (counter difference over the window)."""
    now = now or utcnow()
    rows = db.scalars(select(SiteUsageSample).where(SiteUsageSample.server_id == server_id, SiteUsageSample.ts >= now - timedelta(days=2), *([SiteUsageSample.site == only] if only else [])).order_by(SiteUsageSample.site, SiteUsageSample.ts)).all()
    by_site: dict[str, list[SiteUsageSample]] = {}
    for r in rows:
        by_site.setdefault(r.site, []).append(r)
    owners = site_owners(db, server_id)
    items = []
    for name, series in by_site.items():
        last = series[-1]
        win = [r for r in series if r.ts >= now - timedelta(hours=24)]
        # sum of valid positive deltas inside the window; a counter restart contributes nothing instead of a wrong negative
        def window_total(attr: str) -> int | None:
            ds = [d for d in _delta([getattr(r, attr) for r in win]) if d is not None]
            return sum(ds) if ds else None
        o = owners.get(name)
        items.append({
            "name": name, "kind": last.kind, "label": o["name"] if o else name, "owner": o, "sampled_at": last.ts.isoformat(),
            "stale": (now - last.ts).total_seconds() > 900,
            "disk_bytes": last.disk_bytes, "rss_bytes": last.rss_bytes, "cpu_percent": last.cpu_percent, "procs": last.procs,
            "bytes_out_total": last.bytes_out_total, "requests_total": last.requests_total,
            "bytes_out_24h": window_total("bytes_out_total"), "requests_24h": window_total("requests_total"),
            "errors_4xx_24h": window_total("errors_4xx_total"), "errors_5xx_24h": window_total("errors_5xx_total"),
        })
    items.sort(key=lambda i: (i["rss_bytes"] or 0, i["bytes_out_24h"] or 0), reverse=True)
    return {"generated_at": now.isoformat(), "items": items}


def site_usage_series(db: Session, server_id: uuid.UUID, site: str, rng: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    span = {"6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(rng)
    if span is None:
        raise api_error(422, "invalid_range", "range must be 6h, 24h, 7d or 30d.")
    rows = db.scalars(select(SiteUsageSample).where(SiteUsageSample.server_id == server_id, SiteUsageSample.site == site, SiteUsageSample.ts >= now - span).order_by(SiteUsageSample.ts)).all()
    out_d, req_d = _delta([r.bytes_out_total for r in rows]), _delta([r.requests_total for r in rows])
    points = [
        {"t": r.ts.isoformat(), "rss_bytes": r.rss_bytes, "cpu_percent": r.cpu_percent, "disk_bytes": r.disk_bytes, "bytes_out": out_d[i], "requests": req_d[i]}
        for i, r in enumerate(rows)
    ]
    return {"site": site, "range": rng, "bucket_seconds": SITE_BUCKET, "points": points}
