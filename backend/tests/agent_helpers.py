"""Shared helpers for agent/monitoring tests: a protocol-level signed client independent of the agent package."""
import json
import secrets
import time

from fastapi.testclient import TestClient

from app.services.agents import sign_request

API = "/api/v1"


def register(admin_client: TestClient, name: str = "imac-1") -> tuple[str, str]:
    r = admin_client.post(f"{API}/servers", json={"name": name, "hostname": "imac.local"})
    assert r.status_code == 201, r.text
    return r.json()["server"]["id"], r.json()["enrollment"]["token"]


def enroll(anon: TestClient, token: str):
    return anon.post(f"{API}/agent/enroll", json={"enrollment_token": token, "hostname": "imac.local", "agent_version": "1.0.0"})


def enrolled(admin_client: TestClient, anon: TestClient, name: str = "imac-1") -> tuple[str, str, str]:
    sid, token = register(admin_client, name)
    r = enroll(anon, token)
    assert r.status_code == 200, r.text
    return sid, r.json()["key_id"], r.json()["secret"]


def signed(anon: TestClient, path: str, payload: dict, key_id: str, secret: str, *, ts: int | None = None, nonce: str | None = None, sign_body: bytes | None = None, method: str = "POST"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    ts_s = str(int(time.time()) if ts is None else ts)
    nonce = nonce or secrets.token_hex(16)
    sig = sign_request(secret, method, f"{API}/agent{path}", ts_s, nonce, body if sign_body is None else sign_body)
    headers = {"Content-Type": "application/json", "X-KVL-Key-Id": key_id, "X-KVL-Timestamp": ts_s, "X-KVL-Nonce": nonce, "X-KVL-Signature": sig}
    return anon.request(method, f"{API}/agent{path}", content=body, headers=headers)


METRICS = {
    "cpu_percent": 12.5,
    "load": {"load1": 0.5, "load5": 0.4, "load15": 0.3},
    "memory": {"total": 8_000_000_000, "used": 2_000_000_000, "available": 6_000_000_000},
    "swap": {"total": 0, "used": 0, "available": 0},
    "disks": [{"mount": "/", "fstype": "ext4", "total": 100, "used": 40, "free": 60, "percent": 40.0}],
    "network": {"rx_bytes_per_s": 1000.0, "tx_bytes_per_s": 500.0, "interfaces": [{"name": "eth0", "is_up": True}]},
    "uptime_seconds": 1234,
}
