import io

import pytest
from sqlalchemy import select

from app import bootstrap_owner as bo
from app.models.identity import Membership, Role
from app.models.identity import AuditEvent
from tests.conftest import login

STRONG = "Blue-Harbor-Lamp-42"


def test_creates_owner_who_can_log_in(db, client):
    u = bo.bootstrap_owner(db, "  Owner@Example.com ", "Site Owner", STRONG)
    assert u.email == "owner@example.com" and u.email_verified_at is not None
    role = db.scalar(select(Role).where(Role.name == "super_admin"))
    m = db.scalar(select(Membership).where(Membership.user_id == u.id))
    assert m.role_id == role.id and m.tenant_id is None and m.status == "active"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "owner.bootstrapped")) is not None
    r = login(client, "owner@example.com", STRONG)
    assert r.status_code == 200, r.text


def test_refuses_second_owner(db):
    bo.bootstrap_owner(db, "owner@example.com", "Owner", STRONG)
    with pytest.raises(bo.BootstrapError, match="already exists"):
        bo.bootstrap_owner(db, "other@example.com", "Other", STRONG)


def test_weak_password_rejected_and_nothing_created(db):
    with pytest.raises(bo.BootstrapError):
        bo.bootstrap_owner(db, "owner@example.com", "Owner", "short1")
    assert db.scalar(select(Membership)) is None


def test_bad_email_or_name_rejected(db):
    with pytest.raises(bo.BootstrapError):
        bo.bootstrap_owner(db, "not-an-email", "Owner", STRONG)
    with pytest.raises(bo.BootstrapError):
        bo.bootstrap_owner(db, "owner@example.com", "  ", STRONG)


def test_cli_password_from_stdin_and_never_echoed(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG + "\n"))
    rc = bo.main(["--email", "cli@example.com", "--name", "Cli Owner", "--password-stdin"])
    out = capsys.readouterr()
    assert rc == 0 and "cli@example.com" in out.out
    assert STRONG not in out.out + out.err
    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG + "\n"))
    assert bo.main(["--email", "again@example.com", "--name", "Again", "--password-stdin"]) == 1
