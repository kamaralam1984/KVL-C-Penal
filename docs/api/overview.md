# KVL Panel API overview

Base path: `/api/v1` (versioned; breaking changes will ship under `/api/v2`). Interactive docs: `/api/docs` and `/api/openapi.json` (development and test only; disabled in staging/production unless enabled deliberately).

## Authentication
- **Browser**: HttpOnly cookie session plus `X-CSRF-Token` on writes (double-submit).
- **Programmatic**: `Authorization: Bearer kvl_<id>_<secret>` API key. Create keys in *Account > API keys* (or `POST /apikeys`). The secret is shown **once**; only a SHA-256 hash is stored. Keys are narrowed to the scopes you choose, which can never exceed your own permissions (`GET /developer/scopes` lists them; `grantable` marks what you may delegate). Keys cannot create other keys or webhooks. A tenant user's key is bound to their tenant; platform keys may be bound to one tenant, which removes platform reach.
- Keys have an expiry (default 90 days, max 365), per-key rate limit (default 60/min, max 600), last-used tracking, rotation (`POST /apikeys/{id}/rotate` revokes the old key) and revocation.

## Errors
Every error uses one shape:
```json
{"error": {"code": "forbidden", "message": "...", "request_id": "ab12...", "fields": [{"field": "name", "message": "..."}]}}
```
`fields` appears on `422 validation_error`. Quote `request_id` (also returned as the `X-Request-ID` header) to support. Send your own `X-Request-ID` to correlate; it is echoed and appears in structured logs.

Common codes: `unauthenticated` 401, `forbidden` / `scope_missing` 403, `not_found` 404 (also returned for other tenants' objects), `validation_error` 422, `rate_limited` 429 (with `Retry-After`), `internal_error` 500.

## Rate limits and usage
Fixed-window limits: per API key (configurable), per IP for failed key attempts (30/min), sign-in and OTP limits, ticket creation (20/h/user). `GET /developer/usage` shows your keys' request counts and limits.

## Idempotency
`POST /support/tickets` accepts `Idempotency-Key` (8-128 printable ASCII chars). Same key + same body within 24 h replays the original response with `Idempotent-Replayed: true`; same key + different body is `422 idempotency_key_reuse`; a concurrent duplicate is `409`. Keys are scoped per user and route. Other routers can adopt it with `app.services.idempotency.run_idempotent`.

## Webhooks
Subscribe with `POST /webhooks` (HTTPS only, ports 443/8443; private, loopback, link-local and metadata addresses are rejected at save time and again at delivery time, redirects are never followed). Catalog: `GET /developer/events`.

Each delivery is a JSON envelope `{id, type, created_at, tenant_id, data}` with headers `X-KVL-Event`, `X-KVL-Event-Id`, `X-KVL-Delivery`, and `X-KVL-Signature: t=<unix>,v1=<hex>[,v1=<hex>]` where `v1 = HMAC_SHA256(secret, "<t>.<raw body>")`. Verify with a constant-time compare and reject timestamps older than 5 minutes (replay window). During secret rotation both secrets sign for 24 h (two `v1` values).

```python
import hmac, hashlib, time
def verify(secret, header, body, tolerance=300):
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)  # use a list if you need both v1 values
    t = int(parts["t"]);  assert abs(time.time() - t) <= tolerance
    expected = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```
Retries: after a failure (non-2xx, timeout, 3xx) the delivery is retried after 1 min, 5 min, 30 min, 2 h; the fifth failure moves it to the dead letter list (`status=dead`), which you can re-drive with `POST /webhooks/deliveries/{id}/retry`. Deliveries are processed by `POST /webhooks/dispatch` or by calling `app.services.webhooks.deliver_due(db)` from a scheduler (every minute recommended). Delivery is at-least-once: dedupe on `X-KVL-Event-Id`.

## Observability endpoints (outside `/api/v1`)
- `GET /healthz` liveness. `GET /readyz` readiness: database and Redis, 503 when either is down, no secrets or hostnames in the body.
- `GET /metrics` Prometheus text. Requires `Authorization: Bearer <METRICS_TOKEN>` (set `METRICS_TOKEN`) or a platform session/key holding `security:read`. Series: `kvl_http_requests_total{method,route,status}`, `kvl_http_request_duration_seconds` histogram, in-flight gauge, unhandled error counter. Routes are templates, never raw ids.

## Security headers and CORS
All responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`, `Permissions-Policy`, and a restrictive `Content-Security-Policy` (API docs excepted); HSTS outside dev/test. CORS is an explicit allow-list (`CORS_ORIGINS`), unknown origins never receive `Access-Control-Allow-Origin` (browsers block the read) and preflights return 400.
