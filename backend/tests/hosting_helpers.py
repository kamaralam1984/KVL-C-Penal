from datetime import UTC, datetime

from app.models.infra import AgentJob, Server
from app.services import dnsverify
from tests.conftest import login


class FakeResolver:
    def __init__(self, records: dict[str, list[str]] | None = None, unavailable: bool = False):
        self.records, self.unavailable = records or {}, unavailable

    def txt(self, name: str) -> list[str]:
        if self.unavailable:
            raise dnsverify.ResolverUnavailable("down")
        return self.records.get(name, [])


def use_resolver(monkeypatch, resolver) -> None:
    monkeypatch.setattr(dnsverify, "resolver_factory", lambda: resolver)


def make_server(db, name="srv1", status="registered") -> Server:
    s = Server(name=name, status=status)
    db.add(s)
    db.commit()
    return s


def finish_job(db, job_id, ok=True, result=None, status=None) -> AgentJob:
    j = db.get(AgentJob, job_id)
    j.status = status or ("succeeded" if ok else "failed")
    j.result = result if result is not None else ({"ok": True} if ok else {"ok": False})
    if status == "failed" or (not ok and status is None):
        j.error = (result or {}).get("message", "failed")
    j.finished_at = datetime.now(UTC)
    db.commit()
    return j


def last_job(db, operation=None):
    from sqlalchemy import select

    stmt = select(AgentJob).order_by(AgentJob.created_at.desc())
    if operation:
        stmt = stmt.where(AgentJob.operation == operation)
    return db.scalars(stmt).first()


def tenant_client(make_user, make_client, email, role="customer", tenant=None, tenant_name=None):
    u, t = make_user(email, role, tenant=tenant, tenant_name=tenant_name)
    c = make_client()
    assert login(c, email).status_code == 200
    return c, u, t


def verify_domain_ok(client, domain_id, monkeypatch, hostname, token):
    use_resolver(monkeypatch, FakeResolver({f"_kvl-verify.{hostname}": [f"kvl-verify={token}"]}))
    r = client.post(f"/api/v1/domains/{domain_id}/verify")
    assert r.status_code == 200, r.text
    return r.json()
