import io
import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from kvl_agent import registry
from kvl_agent.client import ControlPlaneClient, ControlPlaneError, UntrustedResponse, response_signature, sign
from kvl_agent.config import AgentConfig, ConfigError, load_config, save_credentials
from kvl_agent.context import BinaryNotAllowed, OpContext, PathEscape, allow_binary
from kvl_agent.executor import execute_job, redact, run_with_timeout, OperationTimeout
from kvl_agent.health import make_server, serve_in_thread
from kvl_agent.jsonlog import setup_logging
from kvl_agent.metrics import MetricsCollector
from kvl_agent.service import Agent
from kvl_agent.services import check_services
from kvl_agent.testing import make_ctx


class P(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = 1


def _register_test_ops():
    if "test.sleep" in registry.OPERATIONS:
        return

    @registry.operation("test.sleep", P, timeout=1)
    def _sleep(p, ctx):
        time.sleep(3)
        return {}

    @registry.operation("test.big", P, timeout=5)
    def _big(p, ctx):
        return {"data": "x" * 100_000}

    @registry.operation("test.secret", P, timeout=5)
    def _secret(p, ctx):
        return {"password": "hunter2", "log": "connected token=abc123 ok", "fine": 1}

    @registry.operation("test.boom", P, timeout=5)
    def _boom(p, ctx):
        raise RuntimeError("kaput")

    @registry.operation("test.shell", P, timeout=5)
    def _shell(p, ctx):
        ctx.run(["sh", "-c", "id"])
        return {}


@pytest.fixture
def ctx(tmp_path):
    _register_test_ops()
    c = make_ctx(tmp_path, max_output=2000)
    return c


# ---------------------------------------------------------------- allow-list / no arbitrary command path
def test_registry_has_no_shell_operation(tmp_path):
    make_ctx(tmp_path)
    real = [n for n in registry.OPERATIONS if not n.startswith("test.")]
    assert {"agent.ping", "agent.collect_metrics", "agent.service_status"} <= set(real)
    for n in real:
        assert not any(w in n.lower() for w in ("shell", "exec", "bash", "command", "eval", "sudo")), n
        fields = registry.OPERATIONS[n].schema.model_fields
        assert not any(f in fields for f in ("cmd", "command", "argv", "shell", "script")), n


def test_ctx_run_rejects_unlisted_pathful_and_metacharacters(tmp_path):
    allow_binary("echo")
    c = OpContext(data_dir=tmp_path)
    for argv in (["sh", "-c", "id"], ["/bin/echo", "x"], ["./echo"], ["echo;id"], ["echo && id"], ["$(id)"], ["`id`"], ["bash"], [], ["../../bin/echo"], ["rm", "-rf", "/"]):
        with pytest.raises(BinaryNotAllowed):
            c.run(argv)
    r = c.run(["echo", "a; id && $(whoami) | cat"])  # metacharacters are inert arguments: no shell
    assert r.stdout.strip() == "a; id && $(whoami) | cat"


def test_ctx_path_sandbox(tmp_path):
    c = OpContext(data_dir=tmp_path)
    assert c.path("a", "b").is_relative_to(tmp_path.resolve())
    with pytest.raises(PathEscape):
        c.path("..", "etc")
    with pytest.raises(PathEscape):
        c.path("/etc/passwd")
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(PathEscape):
        c.path("link", "passwd")


def test_unknown_operation_and_invalid_params(ctx):
    r = execute_job({"id": "1", "operation": "system.exec", "params": {"cmd": "id"}}, ctx)
    assert r.status == "failed" and "not allow-listed" in r.error
    r = execute_job({"id": "1", "operation": "agent.ping", "params": {"cmd": "id"}}, ctx)
    assert r.status == "failed" and "invalid params" in r.error
    assert execute_job({"operation": 5}, ctx).status == "failed"
    assert execute_job({"id": "1", "operation": "agent.ping", "params": {}}, ctx).status == "succeeded"
    with pytest.raises(registry.UnknownOperation):
        registry.validate("nope", {})
    with pytest.raises(registry.InvalidParams):
        registry.validate("agent.ping", {"x": 1})


def test_blocked_binary_in_handler_fails_not_executes(ctx):
    r = execute_job({"operation": "test.shell", "params": {}}, ctx)
    assert r.status == "failed" and "blocked" in r.error


# ---------------------------------------------------------------- timeouts / truncation / redaction
def test_timeout_handling(ctx):
    t0 = time.time()
    r = execute_job({"operation": "test.sleep", "params": {}}, ctx)
    assert r.status == "timed_out" and time.time() - t0 < 2.5
    # a server-supplied shorter timeout wins, a longer one cannot extend the op's own limit
    r = execute_job({"operation": "test.sleep", "params": {}, "timeout_seconds": 100}, ctx)
    assert r.status == "timed_out"
    with pytest.raises(OperationTimeout):
        run_with_timeout(lambda: time.sleep(1), 0.05)


def test_output_truncation(ctx):
    r = execute_job({"operation": "test.big", "params": {}}, ctx)
    assert r.status == "succeeded" and r.summary["truncated"] is True
    assert len(json.dumps(r.summary)) < 2500


def test_subprocess_output_truncated(tmp_path):
    allow_binary("python3")
    c = OpContext(data_dir=tmp_path, max_output=100)
    res = c.run(["python3", "-c", "print('y'*5000)"])
    assert res.truncated and len(res.stdout) == 100


def test_subprocess_timeout_kills(tmp_path):
    allow_binary("sleep")
    res = OpContext(data_dir=tmp_path).run(["sleep", "5"], timeout=1)
    assert res.timed_out and not res.ok


def test_results_redacted_and_errors_contained(ctx):
    r = execute_job({"operation": "test.secret", "params": {}}, ctx)
    s = json.dumps(r.payload())
    assert "hunter2" not in s and "abc123" not in s and r.summary["fine"] == 1
    r = execute_job({"operation": "test.boom", "params": {}}, ctx)
    assert r.status == "failed" and "kaput" in r.error
    assert redact({"a": {"Authorization": "Bearer abcdefghijkl"}, "b": "x password=zz"}) == {"a": {"Authorization": "[redacted]"}, "b": "x [redacted]"}


# ---------------------------------------------------------------- signing & client
def test_signature_binds_method_path_time_nonce_body():
    base = ("s3cret", "POST", "/api/v1/agent/heartbeat", "100", "n" * 32, b"{}")
    sig = sign(*base)
    assert len(sig) == 64 and sign(*base) == sig
    for i, alt in enumerate(["GET", "/api/v1/agent/other", "101", "m" * 32, b"{ }"], start=1):
        args = list(base)
        args[i] = alt
        assert sign(*args) != sig
    assert sign("other", *base[1:]) != sig


def test_client_headers_and_response_verification(tmp_path):
    cfg = AgentConfig(api_url="https://panel.example/api/v1", key_id="kvl_ak_1", secret="topsecret", data_dir=tmp_path)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["req"] = req
        body = json.dumps({"ok": True}).encode()
        return httpx.Response(200, content=body, headers={"X-KVL-Response-Signature": response_signature("topsecret", req.headers["x-kvl-nonce"], body)})

    c = ControlPlaneClient(cfg, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.heartbeat({"agent_version": "1"}) == {"ok": True}
    req = seen["req"]
    exp = sign("topsecret", "POST", "/api/v1/agent/heartbeat", req.headers["x-kvl-timestamp"], req.headers["x-kvl-nonce"], req.content)
    assert req.headers["x-kvl-signature"] == exp and "topsecret" not in str(req.headers) + req.content.decode()
    assert c.heartbeat({}) and seen["req"].headers["x-kvl-nonce"] != req.headers["x-kvl-nonce"]  # fresh nonce each time

    bad = ControlPlaneClient(cfg, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"jobs": [{"operation": "agent.ping"}]}))))
    with pytest.raises(UntrustedResponse):
        bad.poll(1)
    err = ControlPlaneClient(cfg, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"code": "invalid_agent_auth", "message": "no"}}))))
    with pytest.raises(ControlPlaneError) as e:
        err.heartbeat({})
    assert e.value.status == 401


def test_tls_verification_default_on(tmp_path):
    cfg = AgentConfig(api_url="https://panel.example/api/v1", key_id="k", secret="s", data_dir=tmp_path)
    assert cfg.verify_tls is True
    cfg.validate()
    with pytest.raises(ConfigError):
        AgentConfig(api_url="http://panel.example/api/v1", key_id="k", secret="s").validate()  # plain http to a remote host
    AgentConfig(api_url="http://127.0.0.1:8000/api/v1", key_id="k", secret="s").validate()
    with pytest.raises(ConfigError):
        AgentConfig(api_url="https://panel.example/api/v1", key_id="k", secret="s", verify_tls=False).validate()
    with pytest.raises(ConfigError):
        AgentConfig(api_url="https://panel.example/api/v1").validate()  # not enrolled -> fail closed


# ---------------------------------------------------------------- config
def test_config_env_and_secret_never_in_repr(tmp_path):
    cfg = load_config(env={"KVL_AGENT_API_URL": "https://p.example/api/v1", "KVL_AGENT_DATA_DIR": str(tmp_path), "KVL_AGENT_MAX_CONCURRENCY": "3", "KVL_AGENT_KEY_ID": "kvl_ak_x", "KVL_AGENT_SECRET": "supersecretvalue"})
    assert cfg.max_concurrency == 3 and cfg.enrolled
    assert "supersecretvalue" not in repr(cfg) and "supersecretvalue" not in str(cfg)
    with pytest.raises(ConfigError):
        load_config(env={"KVL_AGENT_MAX_CONCURRENCY": "lots"})


@pytest.mark.skipif(os.name != "posix", reason="posix permissions")
def test_credentials_file_permissions(tmp_path):
    cfg = AgentConfig(api_url="https://p.example/api/v1", data_dir=tmp_path)
    save_credentials(cfg, "kvl_ak_1", "sekret")
    assert oct(cfg.credentials_path.stat().st_mode & 0o777) == "0o600"
    loaded = load_config(env={"KVL_AGENT_API_URL": "https://p.example/api/v1", "KVL_AGENT_DATA_DIR": str(tmp_path)})
    assert loaded.key_id == "kvl_ak_1" and loaded.secret == "sekret"
    cfg.credentials_path.chmod(0o644)
    with pytest.raises(ConfigError):  # fail closed on a world-readable secret
        load_config(env={"KVL_AGENT_API_URL": "https://p.example/api/v1", "KVL_AGENT_DATA_DIR": str(tmp_path)})


def test_logs_are_json_and_scrubbed():
    buf = io.StringIO()
    log = setup_logging(stream=buf)
    log.info("hello Bearer abcdefghij", extra={"fields": {"secret": "zzz", "job_id": "j1", "nested": {"token": "t"}}})
    rec = json.loads(buf.getvalue())
    assert rec["level"] == "info" and rec["job_id"] == "j1" and rec["secret"] == "[redacted]" and rec["nested"]["token"] == "[redacted]"
    assert "abcdefghij" not in buf.getvalue()


# ---------------------------------------------------------------- metrics & services
def test_metrics_shape_and_missing_not_zero():
    c = MetricsCollector()
    first = c.collect()
    assert first["network"]["rx_bytes_per_s"] is None  # no previous counters: unknown, not 0
    assert 0 <= first["cpu_percent"] <= 100
    assert first["memory"]["total"] > 0 and first["uptime_seconds"] > 0 and isinstance(first["disks"], list)
    time.sleep(0.2)
    second = c.collect()
    assert second["network"]["rx_bytes_per_s"] is None or second["network"]["rx_bytes_per_s"] >= 0
    assert all(set(d) >= {"mount", "total", "used", "free", "percent"} for d in second["disks"])
    json.dumps(second)


def test_services_not_installed_is_not_configured(monkeypatch):
    monkeypatch.setattr("kvl_agent.services._installed", lambda spec: False)
    monkeypatch.setattr("kvl_agent.services._running_names", lambda: [])
    res = {s["name"]: s["state"] for s in check_services()}
    assert res == {"nginx": "not_configured", "php-fpm": "not_configured", "mariadb": "not_configured", "redis": "not_configured", "pm2": "not_configured"}
    monkeypatch.setattr("kvl_agent.services._running_names", lambda: ["nginx", "php-fpm8.3", "mariadbd"])
    monkeypatch.setattr("kvl_agent.services._installed", lambda spec: spec["bins"] == ["redis-server"])
    res = {s["name"]: s["state"] for s in check_services()}
    assert res["nginx"] == "running" and res["php-fpm"] == "running" and res["mariadb"] == "running"
    assert res["redis"] == "stopped" and res["pm2"] == "not_configured"
    assert "failed" not in res.values()


def test_monitoring_operations_registered(tmp_path):
    c = make_ctx(tmp_path)
    r = execute_job({"operation": "agent.service_status", "params": {}}, c)
    assert r.status == "succeeded" and len(r.summary["services"]) == 5
    r = execute_job({"operation": "agent.collect_metrics", "params": {}}, c)
    assert r.status == "succeeded" and "memory" in r.summary["metrics"]


# ---------------------------------------------------------------- health endpoint & service loop
def test_health_binds_loopback_only_and_serves_json():
    with pytest.raises(ValueError):
        make_server(0, lambda: {}, host="0.0.0.0")
    srv = make_server(0, lambda: {"status": "ok"})
    serve_in_thread(srv)
    port = srv.server_address[1]
    assert srv.server_address[0] == "127.0.0.1"
    assert json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz")) == {"status": "ok"}
    for method in ("POST",):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz", method=method, data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 405
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/admin")
    assert e.value.code == 404
    srv.shutdown()
    srv.server_close()


class FakeClient:
    def __init__(self, jobs=None, hb_error=None):
        self.jobs, self.reports, self.hb, self.hb_error = jobs or [], [], [], hb_error
        self.polled = 0

    def heartbeat(self, payload):
        if self.hb_error:
            raise self.hb_error
        self.hb.append(payload)
        return {"ok": True}

    def poll(self, n):
        self.polled += 1
        out, self.jobs = self.jobs[:n], self.jobs[n:]
        return out

    def report(self, jid, payload):
        self.reports.append((jid, payload))

    def close(self):
        pass


def make_agent(tmp_path, client, **kw):
    cfg = AgentConfig(api_url="http://127.0.0.1:1/api/v1", key_id="k", secret="s", data_dir=tmp_path, health_port=0, **kw)
    return Agent(cfg, client=client)


def test_agent_heartbeat_payload_has_version_and_services(tmp_path):
    c = FakeClient()
    a = make_agent(tmp_path, c)
    assert a.heartbeat_once() is True
    p = c.hb[0]
    assert p["agent_version"] and p["metrics"]["memory"] and len(p["services"]) == 5 and p["update_status"]["current"] == p["agent_version"]
    assert "secret" not in json.dumps(p)


def test_agent_concurrency_limit_and_reporting(tmp_path):
    _register_test_ops()
    jobs = [{"id": f"j{i}", "operation": "agent.ping", "params": {}} for i in range(5)]
    c = FakeClient(jobs=jobs)
    a = make_agent(tmp_path, c, max_concurrency=2)
    assert a.poll_once() == 2
    a.wait_for_jobs(5)
    assert a.poll_once() == 2
    a.wait_for_jobs(5)
    assert [r[0] for r in c.reports] == ["j0", "j1", "j2", "j3"] or len(c.reports) == 4
    assert all(r[1]["status"] == "succeeded" for r in c.reports)


def test_agent_unauthorized_stops_polling(tmp_path):
    c = FakeClient(jobs=[{"id": "j", "operation": "agent.ping", "params": {}}], hb_error=ControlPlaneError(401, "invalid_agent_auth"))
    a = make_agent(tmp_path, c)
    assert a.heartbeat_once() is False and a.authorized is False
    assert a.poll_once() == 0 and c.polled == 0 and a.status()["status"] == "degraded"


def test_agent_reports_failures_for_bad_jobs(tmp_path):
    c = FakeClient(jobs=[{"id": "a", "operation": "shell", "params": {"cmd": "id"}}, {"id": "b", "operation": "agent.ping", "params": {"x": 1}}])
    a = make_agent(tmp_path, c)
    a.poll_once()
    a.wait_for_jobs(5)
    assert {r[0]: r[1]["status"] for r in c.reports} == {"a": "failed", "b": "failed"}


def test_graceful_shutdown_waits_for_job_and_stops(tmp_path):
    _register_test_ops()
    c = FakeClient(jobs=[{"id": "slow", "operation": "agent.ping", "params": {}}])
    a = make_agent(tmp_path, c, poll_interval=1, heartbeat_interval=5)
    t = threading.Thread(target=lambda: a.run_forever(install_signals=False))
    t.start()
    time.sleep(1.5)
    a.stop.set()  # what the SIGTERM handler does
    t.join(10)
    assert not t.is_alive()
    assert any(r[0] == "slow" for r in c.reports) and a._inflight == 0
