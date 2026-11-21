"""SMTP transport: honest not_configured, delivery, retry/backoff, permanent failure, secret redaction, admin API."""
import smtplib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.identity import EmailOutbox
from app.services import mail
from tests.conftest import login


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_connect: Exception | None = None
    fail_for: dict[str, Exception] = {}

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail_connect:
            raise FakeSMTP.fail_connect
        self.host, self.port, self.sent, self.calls = host, port, [], []
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, u, p):
        self.calls.append(("login", u))

    def send_message(self, m):
        to = m["To"]
        if to in FakeSMTP.fail_for:
            raise FakeSMTP.fail_for[to]
        self.sent.append(m)

    def quit(self):
        self.calls.append("quit")


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail_connect, FakeSMTP.fail_for = [], None, {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def cfg(**kw) -> Settings:
    base = {"smtp_host": "smtp.example.com", "smtp_from": "KVL <no-reply@kvl.example.com>", "smtp_username": "u", "smtp_password": "s3cret-pw-XYZ"}
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def configured(monkeypatch):
    s = cfg()
    monkeypatch.setattr(mail, "get_settings", lambda: s)
    return s


def test_not_configured_keeps_mail_queued(db, fake_smtp):
    mail.queue_email(db, "a@x.example", "hi", "body", "t")
    db.commit()
    r = mail.deliver_due(db, settings=Settings(smtp_host="", smtp_from=""))
    assert r["status"] == "not_configured" and r["sent"] == 0
    assert db.scalar(select(EmailOutbox)).status == "queued"
    assert fake_smtp.instances == []


@pytest.mark.parametrize("kw,state", [
    ({"smtp_security": "none"}, "misconfigured"),  # plaintext to a remote host is refused
    ({"smtp_security": "bogus"}, "misconfigured"),
    ({"smtp_from": "not-an-address"}, "misconfigured"),
    ({"smtp_security": "none", "smtp_host": "127.0.0.1"}, "configured"),
])
def test_transport_validation(kw, state):
    assert mail.transport_state(cfg(**kw))["state"] == state


def test_delivers_with_starttls_and_login(db, fake_smtp):
    mail.queue_email(db, "A@X.example", "Subject\r\nBcc: evil@x", "hello", "t")
    db.commit()
    r = mail.deliver_due(db, settings=cfg())
    db.commit()
    assert r == {"status": "ok", "sent": 1, "failed": 0, "retry": 0}
    c = fake_smtp.instances[0]
    assert c.calls[:3] == ["ehlo", "starttls", "ehlo"] and ("login", "u") in c.calls and c.calls[-1] == "quit"
    m = c.sent[0]
    assert m["To"] == "a@x.example" and "\n" not in m["Subject"] and m["Bcc"] is None  # header injection neutralised
    row = db.scalar(select(EmailOutbox))
    assert row.status == "sent" and row.sent_at is not None and row.attempts == 1


def test_connect_failure_retries_with_backoff_then_fails(db, fake_smtp):
    fake_smtp.fail_connect = OSError("connection refused password=s3cret-pw-XYZ")
    mail.queue_email(db, "a@x.example", "s", "b", "t")
    db.commit()
    s = cfg(smtp_max_attempts=2)
    r = mail.deliver_due(db, settings=s)
    db.commit()
    row = db.scalar(select(EmailOutbox))
    assert r["retry"] == 1 and row.status == "queued" and row.next_attempt_at is not None
    assert "s3cret-pw-XYZ" not in row.last_error  # secrets never stored in errors
    # not due yet -> skipped
    assert mail.deliver_due(db, settings=s)["retry"] == 0
    r = mail.deliver_due(db, now=row.next_attempt_at + timedelta(seconds=1), settings=s)
    db.commit()
    db.refresh(row)
    assert r["failed"] == 1 and row.status == "failed" and row.attempts == 2


def test_recipient_refused_is_permanent_others_still_sent(db, fake_smtp):
    fake_smtp.fail_for = {"bad@x.example": smtplib.SMTPRecipientsRefused({"bad@x.example": (550, b"no")})}
    mail.queue_email(db, "bad@x.example", "s", "b", "t")
    mail.queue_email(db, "good@x.example", "s", "b", "t")
    db.commit()
    r = mail.deliver_due(db, settings=cfg())
    db.commit()
    assert r["sent"] == 1 and r["failed"] == 1
    st = {e.to_email: e.status for e in db.scalars(select(EmailOutbox))}
    assert st == {"bad@x.example": "failed", "good@x.example": "sent"}


def test_scheduler_runs_mail_task(db, fake_smtp, configured):
    from app.services import scheduler

    mail.queue_email(db, "a@x.example", "s", "b", "t")
    db.commit()
    out = scheduler.run_all(minute=0)
    assert out["mail"]["sent"] == 1


def test_admin_mail_status_and_test(admin, client, make_user, make_client, fake_smtp, configured):
    r = client.get("/api/v1/settings/mail")
    assert r.status_code == 200 and r.json()["transport"]["state"] == "configured"
    assert "s3cret" not in r.text and "password" not in r.json()["transport"]
    r = client.post("/api/v1/settings/mail/test", json={})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert fake_smtp.instances[-1].sent[0]["To"] == "owner@kvl.example.com"
    assert client.post("/api/v1/settings/mail/test", json={"to": "x@y\nBcc: z@w"}).status_code == 422
    # customers cannot see or use it
    make_user("cust@x.example", "customer")
    c2 = make_client()
    assert login(c2, "cust@x.example").status_code == 200
    assert c2.get("/api/v1/settings/mail").status_code == 403
    assert c2.post("/api/v1/settings/mail/test", json={}).status_code == 403


def test_admin_mail_test_not_configured(admin, client, monkeypatch):
    monkeypatch.setattr(mail, "get_settings", lambda: Settings(smtp_host="", smtp_from=""))
    r = client.post("/api/v1/settings/mail/test", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"
