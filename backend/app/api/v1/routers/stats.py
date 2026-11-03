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
from app.models.monitoring import Alert
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
        return {
            "scope": "platform",
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
                {"id": str(a.id), "action": a.action, "created_at": a.created_at, "target_type": a.target_type}
                for a in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(8))
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
