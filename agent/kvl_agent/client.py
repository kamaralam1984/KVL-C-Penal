"""Signed HTTP client for the control plane. Signature = HMAC-SHA256(secret, method, path, ts, nonce, sha256(body))."""
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from kvl_agent.config import AgentConfig


class ControlPlaneError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


class UntrustedResponse(Exception):
    """The response signature was missing or wrong: the payload must not be acted on."""


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()])


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode(), canonical(method, path, timestamp, nonce, body).encode(), hashlib.sha256).hexdigest()


def response_signature(secret: str, nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"RESPONSE\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()


class ControlPlaneClient:
    def __init__(self, cfg: AgentConfig, http: Any = None):
        self.cfg = cfg
        self._base = cfg.api_url.rstrip("/")
        self._prefix = urlparse(self._base).path.rstrip("/")
        verify: Any = cfg.ca_bundle or cfg.verify_tls
        self.http = http or httpx.Client(timeout=cfg.request_timeout, verify=verify, follow_redirects=False)

    def _request(self, method: str, endpoint: str, payload: dict | None, *, signed: bool = True) -> dict:
        body = json.dumps(payload if payload is not None else {}, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "kvl-agent"}
        nonce = secrets.token_hex(16)
        if signed:
            ts = str(int(time.time()))
            headers.update({
                "X-KVL-Key-Id": self.cfg.key_id, "X-KVL-Timestamp": ts, "X-KVL-Nonce": nonce,
                "X-KVL-Signature": sign(self.cfg.secret, method, self._prefix + endpoint, ts, nonce, body),
            })
        r = self.http.request(method, self._base + endpoint, content=body, headers=headers)
        if r.status_code >= 400:
            err = {}
            try:
                err = r.json().get("error", {})
            except Exception:  # noqa: BLE001
                pass
            raise ControlPlaneError(r.status_code, err.get("code", "error"), str(err.get("message", ""))[:200])
        if signed:
            got = r.headers.get("X-KVL-Response-Signature", "")
            if not hmac.compare_digest(got, response_signature(self.cfg.secret, nonce, r.content)):
                raise UntrustedResponse(endpoint)
        return r.json()

    def enroll(self, token: str, hostname: str, version: str, os_info: dict) -> dict:
        return self._request("POST", "/agent/enroll", {"enrollment_token": token, "hostname": hostname, "agent_version": version, "os_info": os_info}, signed=False)

    def heartbeat(self, payload: dict) -> dict:
        return self._request("POST", "/agent/heartbeat", payload)

    def poll(self, max_jobs: int) -> list[dict]:
        return self._request("POST", "/agent/jobs/poll", {"max": max_jobs})["jobs"]

    def report(self, job_id: str, result: dict) -> dict:
        return self._request("POST", f"/agent/jobs/{job_id}/result", result)

    def close(self) -> None:
        self.http.close()
