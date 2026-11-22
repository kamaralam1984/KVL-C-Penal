"""Numbers for the full-page operations centre (platform staff only).

Every figure is computed from live tables. When there is no data the value is None or an empty list, never a
made-up number, so the UI can say "no data yet" honestly.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.identity import AuditEvent, AuthSession, LoginEvent, User
from app.models.infra import AgentJob, Server
from app.models.monitoring import Alert, MetricSample, ServiceStatus
from app.models.support import Ticket
from app.models.tenant import Tenant

IST = ZoneInfo("Asia/Kolkata")
BUCKET = 300  # uptime window in seconds
FLOW = ["cloudflared", "nginx", "php-fpm", "pm2", "mariadb", "redis"]


def _pct(num: int, den: int) -> float | None:
    return round(100 * num / den, 2) if den else None


def _traffic(db: Session, now: datetime) -> list[dict]:
    start = now - timedelta(hours=24)
    hour = func.date_trunc("hour", MetricSample.ts)
    rows = db.execute(
        select(hour, func.avg(MetricSample.net_rx_bps), func.avg(MetricSample.net_tx_bps), func.avg(MetricSample.cpu_percent))
        .where(MetricSample.ts >= start, MetricSample.resolution == "raw").group_by(hour).order_by(hour)
    ).all()
    return [{"t": h.astimezone(IST).strftime("%H:00"), "rx": round(rx or 0), "tx": round(tx or 0), "cpu": None if c is None else round(c, 1)} for h, rx, tx, c in rows]


def _uptime(db: Session, now: datetime) -> dict:
    """Share of 5-minute windows in the last 7 days (since registration) with at least one agent heartbeat.

    Raw samples are rolled up to 5-minute rows after 48 h, so both resolutions are counted.
    """
    start = now - timedelta(days=7)
    servers = db.scalars(select(Server).where(Server.status == "registered")).all()
    if not servers:
        return {"percent": None, "samples": 0}
    bucket = func.floor(func.extract("epoch", MetricSample.ts) / BUCKET)
    got = db.scalar(
        select(func.count()).select_from(
            select(MetricSample.server_id, bucket).where(MetricSample.ts >= start, MetricSample.resolution.in_(("raw", "5m"))).distinct().subquery()
        )
    ) or 0
    expected = sum(max(1, int((now - max(start, s.registered_at or start)).total_seconds() // BUCKET)) for s in servers)
    return {"percent": None if got == 0 else round(min(100.0, 100 * got / expected), 2), "samples": got}


def _support(db: Session, now: datetime) -> dict:
    start = now - timedelta(days=30)
    tickets = db.execute(
        select(Ticket.created_at, Ticket.first_response_at, Ticket.resolved_at, Ticket.first_response_breached, Ticket.resolution_breached, Ticket.assignee_id, Ticket.status)
        .where(Ticket.created_at >= start)
    ).all()
    responded = [t for t in tickets if t.first_response_at]
    within = sum(1 for t in responded if not t.first_response_breached)

    # Average first-response time (minutes) per IST day, last 7 days.
    days = [(now - timedelta(days=i)).astimezone(IST).date() for i in range(6, -1, -1)]
    wait = []
    for d in days:
        mins = [(t.first_response_at - t.created_at).total_seconds() / 60 for t in responded if t.created_at.astimezone(IST).date() == d]
        wait.append({"day": d.strftime("%a"), "minutes": round(sum(mins) / len(mins), 1) if mins else None})

    # Resolved within SLA by weekday of creation.
    week = []
    for wd, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        res = [t for t in tickets if t.resolved_at and t.created_at.astimezone(IST).weekday() == wd]
        week.append({"day": name, "percent": _pct(sum(1 for t in res if not t.resolution_breached), len(res))})

    # Staff productivity (resolved in 30 days).
    staff: dict = {}
    for t in tickets:
        if not t.assignee_id:
            continue
        s = staff.setdefault(t.assignee_id, {"assigned": 0, "resolved": 0, "handle": [], "sla_ok": 0})
        s["assigned"] += 1
        if t.resolved_at:
            s["resolved"] += 1
            s["handle"].append((t.resolved_at - t.created_at).total_seconds() / 3600)
            s["sla_ok"] += 0 if t.resolution_breached else 1
    names = dict(db.execute(select(User.id, User.name).where(User.id.in_(list(staff) or [None]))).all())
    agents = sorted(
        (
            {
                "name": names.get(uid, "Unknown"),
                "assigned": s["assigned"],
                "resolved": s["resolved"],
                "avg_handle_hours": round(sum(s["handle"]) / len(s["handle"]), 1) if s["handle"] else None,
                "sla_percent": _pct(s["sla_ok"], s["resolved"]),
            }
            for uid, s in staff.items()
        ),
        key=lambda a: (-a["resolved"], -a["assigned"]),
    )[:6]
    return {
        "tickets_30d": len(tickets),
        "open": sum(1 for t in tickets if t.status in ("open", "pending")),
        "availability_percent": _pct(within, len(responded)),
        "avg_wait": wait,
        "resolution_by_weekday": week,
        "agents": agents,
    }


def _heatmap(db: Session, now: datetime) -> list[list[int]]:
    """Successful sign-ins in the last 30 days, weekday (Mon..Sun) x hour (IST)."""
    grid = [[0] * 24 for _ in range(7)]
    for (ts,) in db.execute(select(LoginEvent.created_at).where(LoginEvent.created_at >= now - timedelta(days=30), LoginEvent.outcome == "success")):
        t = ts.astimezone(IST)
        grid[t.weekday()][t.hour] += 1
    return grid


def _sessions(db: Session, now: datetime) -> dict:
    active = db.scalar(
        select(func.count()).select_from(AuthSession).where(
            AuthSession.revoked_at.is_(None), AuthSession.rotated_at.is_(None), AuthSession.expires_at > now,
            AuthSession.last_used_at >= now - timedelta(minutes=15),
        )
    ) or 0
    hour = func.date_trunc("hour", LoginEvent.created_at)
    rows = dict(db.execute(select(hour, func.count()).where(LoginEvent.created_at >= now - timedelta(hours=24), LoginEvent.outcome == "success").group_by(hour)).all())
    base = now.replace(minute=0, second=0, microsecond=0)
    spark = [rows.get(base - timedelta(hours=i), 0) for i in range(23, -1, -1)]
    return {"active": active, "logins_24h": spark}


def _jobs(db: Session, now: datetime) -> list[dict]:
    rows = db.execute(
        select(AgentJob.operation, AgentJob.status, func.count())
        .where(AgentJob.created_at >= now - timedelta(days=7)).group_by(AgentJob.operation, AgentJob.status)
    ).all()
    ops: dict = {}
    for op, status, n in rows:
        o = ops.setdefault(op, {"operation": op, "total": 0, "succeeded": 0, "failed": 0, "pending": 0})
        o["total"] += n
        key = "succeeded" if status == "succeeded" else "failed" if status in ("failed", "timed_out") else "pending"
        o[key] += n
    out = sorted(ops.values(), key=lambda o: -o["total"])[:5]
    for o in out:
        done = o["succeeded"] + o["failed"]
        o["success_percent"] = _pct(o["succeeded"], done)
        o["progress_percent"] = _pct(done, o["total"])
    return out


def _timeline(db: Session) -> list[dict]:
    rows = db.execute(
        select(AuditEvent.created_at, AuditEvent.action, AuditEvent.target_type, User.name)
        .join(User, User.id == AuditEvent.actor_user_id, isouter=True)
        .order_by(AuditEvent.created_at.desc()).limit(8)
    ).all()
    return [{"at": ts, "action": action, "target": target, "actor": name or "System"} for ts, action, target, name in rows]


def _services(db: Session) -> list[dict]:
    latest: dict[str, ServiceStatus] = {}
    for s in db.scalars(select(ServiceStatus).order_by(ServiceStatus.checked_at)):
        latest[s.name] = s
    return [{"name": n, "state": latest[n].state if n in latest else None} for n in FLOW]


def _monthly(db: Session, now: datetime) -> dict:
    start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=330)).replace(day=1)
    months = []
    d = start
    while d <= now:
        months.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)

    def series(col):
        m = func.to_char(func.date_trunc("month", col), "YYYY-MM")
        rows = dict(db.execute(select(m, func.count()).where(col >= start).group_by(m)).all())
        return [{"month": k, "count": rows.get(k, 0)} for k in months]

    return {"tenants": series(Tenant.created_at), "tickets": series(Ticket.created_at)}


def _forecast(db: Session, now: datetime) -> dict:
    """Daily average of the fullest disk for 30 days, plus a straight-line projection 30 days ahead."""
    day = func.date_trunc("day", MetricSample.ts)
    rows = db.execute(
        select(day, func.avg(MetricSample.disk_max_percent)).where(MetricSample.ts >= now - timedelta(days=30), MetricSample.disk_max_percent.is_not(None)).group_by(day).order_by(day)
    ).all()
    hist = [{"day": d.date().isoformat(), "disk": round(v, 1)} for d, v in rows]
    proj: list[dict] = []
    if len(hist) >= 3:
        n = len(hist)
        xs, ys = range(n), [h["disk"] for h in hist]
        mx, my = (n - 1) / 2, sum(ys) / n
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sum((x - mx) ** 2 for x in xs) or 1)
        last = datetime.fromisoformat(hist[-1]["day"])
        proj = [{"day": (last + timedelta(days=i)).date().isoformat(), "disk": round(max(0.0, min(100.0, ys[-1] + slope * i)), 1)} for i in range(1, 31)]
    return {"history": hist, "projection": proj}


def build(db: Session) -> dict:
    now = datetime.now(UTC)
    servers = db.scalars(select(Server)).all()
    connected = sum(1 for s in servers if s.status == "registered" and s.last_heartbeat_at and (now - s.last_heartbeat_at).total_seconds() < 180)
    return {
        "generated_at": now,
        "servers": {"total": len(servers), "connected": connected},
        "alerts_open": db.scalar(select(func.count()).select_from(Alert).where(Alert.state != "resolved")) or 0,
        "traffic": _traffic(db, now),
        "uptime": _uptime(db, now),
        "sessions": _sessions(db, now),
        "support": _support(db, now),
        "login_heatmap": _heatmap(db, now),
        "jobs": _jobs(db, now),
        "timeline": _timeline(db),
        "services": _services(db),
        "monthly": _monthly(db, now),
        "disk_forecast": _forecast(db, now),
    }
