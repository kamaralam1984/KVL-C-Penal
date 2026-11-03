"""Shared helpers for the Phase 9-11 test modules."""
import uuid
from datetime import UTC, datetime

from app.models.infra import AgentJob, Server
from app.models.ownership import ResourceRef
from app.services import cloudflare as cfmod


def add_server(db) -> Server:
    s = Server(name=f"srv-{uuid.uuid4().hex[:6]}", status="registered")
    db.add(s)
    db.commit()
    return s


def add_ref(db, tenant, kind, ref, **kw) -> ResourceRef:
    r = ResourceRef(tenant_id=tenant.id, kind=kind, ref=ref, **kw)
    db.add(r)
    db.commit()
    return r


def finish_job(db, job_id, status="succeeded", result=None, error=None):
    j = db.get(AgentJob, uuid.UUID(str(job_id)))
    j.status, j.result, j.error, j.finished_at = status, result, error, datetime.now(UTC)
    db.commit()
    return j


def last_job(db, op=None):
    from sqlalchemy import select

    stmt = select(AgentJob).order_by(AgentJob.created_at.desc())
    if op:
        stmt = stmt.where(AgentJob.operation == op)
    return db.scalars(stmt).first()


class FakeResolver:
    def __init__(self, table=None, fail=False):
        self.table, self.fail = table or {}, fail

    def lookup(self, name, rtype):
        if self.fail:
            raise RuntimeError("Timeout")
        return list(self.table.get((name, rtype), []))


class FakeCF:
    """In-memory Cloudflare. Records every mutating call; can simulate a buggy provider that touches other records."""

    def __init__(self, zones=None, records=None):
        self.zones = zones if zones is not None else [{"id": "z1", "name": "example.com", "status": "active"}]
        self.records = {r["id"]: dict(r) for r in (records or [])}
        self.calls: list[tuple] = []
        self.tamper = False
        self.fail_with: Exception | None = None
        self.n = 100

    def close(self):
        pass

    def verify_token(self):
        if self.fail_with:
            raise self.fail_with
        return {"status": "active"}

    def list_zones(self):
        return list(self.zones)

    def list_records(self, zone_id):
        return [dict(r) for r in self.records.values()]

    def create_record(self, zone_id, payload):
        if self.fail_with:
            raise self.fail_with
        self.n += 1
        rec = {"id": f"r{self.n}", **self._flat(payload)}
        self.records[rec["id"]] = rec
        self.calls.append(("create", rec["id"], payload))
        self._maybe_tamper(rec["id"])
        return rec

    def update_record(self, zone_id, rid, payload):
        if self.fail_with:
            raise self.fail_with
        self.records[rid].update(self._flat(payload))
        self.calls.append(("update", rid, payload))
        self._maybe_tamper(rid)
        return self.records[rid]

    def delete_record(self, zone_id, rid):
        if self.fail_with:
            raise self.fail_with
        del self.records[rid]
        self.calls.append(("delete", rid, None))
        self._maybe_tamper(rid)

    def _maybe_tamper(self, skip):
        if self.tamper:
            for k, r in self.records.items():
                if k != skip and r["type"] == "MX":
                    r["content"] = "evil.example.net"

    @staticmethod
    def _flat(p):
        p = dict(p)
        d = p.pop("data", None)
        if d and "target" in d:
            p["content"] = f"{d['weight']} {d['port']} {d['target']}"
            p["priority"] = d["priority"]
        elif d:
            p["content"] = f'{d["flags"]} {d["tag"]} "{d["value"]}"'
        return p


BASE_RECORDS = [
    {"id": "a1", "type": "A", "name": "example.com", "content": "203.0.113.10", "ttl": 300, "proxied": False},
    {"id": "a2", "type": "A", "name": "www.example.com", "content": "203.0.113.10", "ttl": 1, "proxied": True},
    {"id": "m1", "type": "MX", "name": "example.com", "content": "mail.example.com", "priority": 10, "ttl": 3600},
    {"id": "m2", "type": "MX", "name": "example.com", "content": "mail2.example.com", "priority": 20, "ttl": 3600},
    {"id": "t1", "type": "TXT", "name": "example.com", "content": '"v=spf1 include:_spf.example.com ~all"', "ttl": 3600},
    {"id": "t2", "type": "TXT", "name": "_dmarc.example.com", "content": '"v=DMARC1; p=quarantine; rua=mailto:d@example.com"', "ttl": 3600},
    {"id": "t3", "type": "TXT", "name": "sel1._domainkey.example.com", "content": '"v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A"', "ttl": 3600},
    {"id": "c1", "type": "CNAME", "name": "blog.example.com", "content": "kvl.example.net", "ttl": 300, "proxied": False},
    {"id": "n1", "type": "NS", "name": "dev.example.com", "content": "ns1.example.net", "ttl": 3600},
]


# ---------------------------------------------------------------- run queued jobs through the REAL agent operation handlers
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_AGENT = Path(__file__).resolve().parents[2] / "agent"
if _AGENT.is_dir() and str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))


def run_agent_job(db, job_id, data_dir):
    """Execute a queued job with the actual kvl_agent handler in a sandbox dir and record the outcome like the daemon would."""
    from kvl_agent.registry import InvalidParams, validate
    from kvl_agent.safe_paths import OpError
    from kvl_agent.testing import make_ctx

    j = db.get(AgentJob, uuid.UUID(str(job_id)))
    ctx = make_ctx(Path(data_dir))
    try:
        op, p = validate(j.operation, j.params)
        res = op.handler(p, ctx)
        return finish_job(db, j.id, "succeeded", res)
    except OpError as e:
        return finish_job(db, j.id, "failed", None, f"{e.code}: {e.message}")
    except InvalidParams as e:
        return finish_job(db, j.id, "failed", None, f"invalid_params: {e}")
