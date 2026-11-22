"""Role-aware dashboard numbers computed from live tables. Tenant users only ever see their own tenant."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, get_ctx
from app.models.backups import BackupRecord
from app.models.billing import Invoice, Subscription
from app.models.hosting import Application, CustomerDatabase, Domain, Website
from app.models.identity import AuditEvent, User
from app.models.infra import AgentJob, Server
from app.models.monitoring import Alert, MetricSample, ServiceStatus
from app.models.ssl import Certificate
from app.models.support import Ticket
from app.models.tenant import Tenant, TenantQuota

router = APIRouter(prefix="/stats", tags=["stats"])


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _daily(db: Session, col, where, days: int = 14) -> list[dict]:
    """Rows per UTC day for the last `days` days (zero-filled so charts show gaps honestly as 0 events)."""
    start = (datetime.now(UTC) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day = func.date_trunc("day", col)
    rows = {r[0].date(): r[1] for r in db.execute(select(day, func.count()).where(col >= start, *where).group_by(day))}
    return [{"day": (start + timedelta(days=i)).date().isoformat(), "count": rows.get((start + timedelta(days=i)).date(), 0)} for i in range(days)]


OPTIONAL_SERVICES = {"pm2"}  # may legitimately be stopped when no Node apps are deployed


def _health(servers_total: int, connected: int, alerts: list[Alert], services: list[dict]) -> dict:
    """Explainable 0-100 score: start at 100 and subtract a fixed penalty per problem. None when no server exists."""
    if not servers_total:
        return {"score": None, "factors": []}
    factors = []
    if connected < servers_total:
        factors.append({"label": f"{servers_total - connected} server(s) not reporting", "impact": -30 * (servers_total - connected)})
    for a in alerts:
        factors.append({"label": a.message, "impact": -20 if a.severity == "critical" else -8})
    for sv in services:
        if sv["state"] == "failed" or (sv["state"] == "stopped" and sv["name"] not in OPTIONAL_SERVICES):
            factors.append({"label": f"{sv['name']} is {sv['state']}", "impact": -10})
    return {"score": max(0, 100 + sum(f["impact"] for f in factors)), "factors": factors}


def _resources(db: Session, servers: list[Server], now: datetime) -> list[dict]:
    out = []
    for s in servers:
        if s.status != "registered":
            continue
        recent = db.scalars(
            select(MetricSample).where(MetricSample.server_id == s.id, MetricSample.resolution == "raw", MetricSample.ts >= now - timedelta(hours=2)).order_by(MetricSample.ts)
        ).all()
        last = recent[-1] if recent else None
        step = max(1, len(recent) // 40)
        out.append({
            "server": s.name,
            "age_seconds": int((now - last.ts).total_seconds()) if last else None,
            "cpu": last and last.cpu_percent, "mem": last and last.mem_percent, "swap": last and last.swap_percent,
            "disk": last and last.disk_max_percent, "load1": last and last.load1,
            "cpu_count": (last.detail or {}).get("cpu_count") if last else None,
            "uptime_seconds": last and last.uptime_seconds,
            "rx_bps": last and last.net_rx_bps, "tx_bps": last and last.net_tx_bps,
            "spark": [{"t": m.ts, "cpu": m.cpu_percent, "mem": m.mem_percent} for m in recent[::step]],
        })
    return sorted(out, key=lambda r: (r["age_seconds"] is None, r["age_seconds"] or 0))  # freshest reporting server first


def _services(db: Session, servers: list[Server]) -> list[dict]:
    names = {s.id: s.name for s in servers}
    latest: dict = {}
    for st in db.scalars(select(ServiceStatus).order_by(ServiceStatus.checked_at)):
        latest[(st.server_id, st.name)] = st
    return sorted(
        ({"server": names.get(k[0], "?"), "name": st.name, "state": st.state, "checked_at": st.checked_at} for k, st in latest.items()),
        key=lambda x: (x["server"], x["name"]),
    )


@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    soon = now + timedelta(days=30)

    if ctx.is_platform:
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        by_status = dict(db.execute(select(Tenant.status, func.count()).group_by(Tenant.status)).all())
        servers = db.scalars(select(Server)).all()
        connected = sum(
            1 for s in servers if s.status == "registered" and s.last_heartbeat_at and (now - s.last_heartbeat_at).total_seconds() < 180
        )
        open_alerts = db.scalars(select(Alert).where(Alert.state != "resolved").order_by(Alert.opened_at.desc())).all()
        services = _services(db, servers)
        events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(10)).all()
        actor_ids = list({e.actor_user_id for e in events if e.actor_user_id})
        names = dict(db.execute(select(User.id, User.name).where(User.id.in_(actor_ids))).all()) if actor_ids else {}
        return {
            "scope": "platform",
            "health": _health(len(servers), connected, open_alerts, services),
            "alerts": [
                {"id": str(a.id), "severity": a.severity, "metric": a.metric, "message": a.message, "state": a.state, "opened_at": a.opened_at}
                for a in open_alerts[:6]
            ],
            "resources": _resources(db, servers, now),
            "services": services,
            "tenants": {"total": sum(by_status.values()), "by_status": by_status},
            "users": _count(db, User),
            "servers": {"total": len(servers), "connected": connected},
            "alerts_open": _count(db, Alert, Alert.state != "resolved"),
            "websites": _count(db, Website),
            "domains": _count(db, Domain),
            "databases": _count(db, CustomerDatabase),
            "apps": _count(db, Application),
            "tickets_open": _count(db, Ticket, Ticket.status.in_(("open", "pending"))),
            "subscriptions_active": _count(db, Subscription, Subscription.status.in_(("active", "trialing"))),
            "revenue_month_paise": db.scalar(select(func.coalesce(func.sum(Invoice.total_paise), 0)).where(Invoice.status == "paid", Invoice.paid_at >= month)) or 0,
            "certs_expiring_30d": _count(db, Certificate, Certificate.not_after.is_not(None), Certificate.not_after < soon, Certificate.status != "expired"),
            "jobs_failed_24h": _count(db, AgentJob, AgentJob.status.in_(("failed", "timed_out")), AgentJob.created_at >= now - timedelta(hours=24)),
            "signups_14d": _daily(db, Tenant.created_at, []),
            "recent_activity": [
                {"id": str(a.id), "action": a.action, "created_at": a.created_at, "target_type": a.target_type, "actor": names.get(a.actor_user_id) if a.actor_user_id else None}
                for a in events
            ],
        }

    if ctx.scope == "reseller":
        return {"scope": "reseller"}  # the reseller dashboard uses /reseller/* endpoints

    t = ctx.tenant_id
    quotas = [{"resource": q.resource, "limit": q.limit_value, "used": q.used_value} for q in db.scalars(select(TenantQuota).where(TenantQuota.tenant_id == t))]
    tenant = db.get(Tenant, t)
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == t, Subscription.status.in_(("active", "trialing", "past_due"))))
    return {
        "scope": "tenant",
        "tenant": {"name": tenant.name, "status": tenant.status, "plan_code": tenant.plan_code, "trial_ends_at": tenant.trial_ends_at},
        "subscription": {"status": sub.status} if sub else None,
        "quotas": quotas,
        "websites": _count(db, Website, Website.tenant_id == t),
        "domains": _count(db, Domain, Domain.tenant_id == t),
        "databases": _count(db, CustomerDatabase, CustomerDatabase.tenant_id == t),
        "apps": _count(db, Application, Application.tenant_id == t),
        "tickets_open": _count(db, Ticket, Ticket.tenant_id == t, Ticket.status.in_(("open", "pending"))),
        "invoices_unpaid": _count(db, Invoice, Invoice.tenant_id == t, Invoice.status == "open"),
        "certs_expiring_30d": _count(db, Certificate, Certificate.tenant_id == t, Certificate.not_after.is_not(None), Certificate.not_after < soon),
        "backups_verified": _count(db, BackupRecord, BackupRecord.tenant_id == t, BackupRecord.verification_status == "verified"),
        "last_backup_at": db.scalar(select(func.max(BackupRecord.created_at)).where(BackupRecord.tenant_id == t, BackupRecord.status == "completed")),
    }


@router.get("/ops-center")
def ops_center(ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    """Full-page operations centre. Platform staff only: it aggregates every tenant."""
    from app.core.deps import api_error
    from app.services import ops_center as svc

    if not ctx.is_platform or "monitoring:read" not in ctx.permissions:
        raise api_error(403, "forbidden", "You do not have permission to perform this action.")
    return svc.build(db)
