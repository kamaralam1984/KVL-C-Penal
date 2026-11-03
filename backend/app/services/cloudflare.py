"""Thin Cloudflare v4 client (httpx) for scoped API tokens only. No global API keys, ever.

Required token permissions (least privilege): Zone:Read, DNS:Read for discovery/sync; DNS:Edit only if DNS changes are wanted.
Tests inject `httpx.MockTransport`; routes obtain clients through `client_factory` which tests replace.
"""
import time
from typing import Any, Callable

import httpx

from app.core.config import get_settings


class CloudflareError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


class CloudflareAuthError(CloudflareError):
    """401/403: token invalid, expired, or lacking the needed permission."""


class CloudflareRateLimited(CloudflareError):
    pass


class CloudflareClient:
    def __init__(self, token: str, *, base_url: str | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, max_retries: int = 2, max_wait: float = 5.0):
        self._token = token
        self._http = httpx.Client(base_url=(base_url or get_settings().cloudflare_api_base).rstrip("/"), transport=transport, timeout=15.0,
                                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self._sleep, self._max_retries, self._max_wait = sleep, max_retries, max_wait

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kw) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                r = self._http.request(method, path, **kw)
            except httpx.HTTPError:
                raise CloudflareError("network_error", "Could not reach Cloudflare.") from None
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
                if attempt < self._max_retries and wait <= self._max_wait:
                    attempt += 1
                    self._sleep(wait)
                    continue
                raise CloudflareRateLimited("rate_limited", "Cloudflare rate limit reached; try again shortly.", 429, wait)
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code in (401, 403):
                msg = (body.get("errors") or [{}])[0].get("message", "Token rejected or lacks permission.")
                raise CloudflareAuthError("permission_denied" if r.status_code == 403 else "invalid_token", str(msg)[:200], r.status_code)
            if r.status_code >= 400 or body.get("success") is False:
                e = (body.get("errors") or [{}])[0]
                raise CloudflareError("api_error", str(e.get("message", f"Cloudflare returned HTTP {r.status_code}"))[:200], r.status_code)
            return body

    def verify_token(self) -> dict:
        return self._request("GET", "/user/tokens/verify").get("result", {})

    def list_zones(self) -> list[dict]:
        out, page = [], 1
        while True:
            b = self._request("GET", "/zones", params={"page": page, "per_page": 50})
            out += [{"id": z["id"], "name": z["name"].lower(), "status": z.get("status")} for z in b.get("result", [])]
            if page >= (b.get("result_info") or {}).get("total_pages", 1):
                return out
            page += 1

    def list_records(self, zone_id: str) -> list[dict]:
        out, page = [], 1
        while True:
            b = self._request("GET", f"/zones/{zone_id}/dns_records", params={"page": page, "per_page": 100})
            out += b.get("result", [])
            if page >= (b.get("result_info") or {}).get("total_pages", 1):
                return out
            page += 1

    def create_record(self, zone_id: str, payload: dict) -> dict:
        return self._request("POST", f"/zones/{zone_id}/dns_records", json=payload)["result"]

    def update_record(self, zone_id: str, record_id: str, payload: dict) -> dict:
        return self._request("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", json=payload)["result"]

    def delete_record(self, zone_id: str, record_id: str) -> None:
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")


# Tests replace this: client_factory(token) -> client-like object.
client_factory: Callable[[str], CloudflareClient] = lambda token: CloudflareClient(token)
