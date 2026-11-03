# KVL Hosting Panel — Phase 17 Report: Security, Monitoring and Developer Platform

**No SOC 2, ISO 27001, PCI DSS or other certification is claimed.** See `docs/security/gap-list-and-remediation-plan.md`.

## 1. Phase summary
Scoped API keys, signed webhooks with SSRF-safe delivery, idempotency helper, security events with alert routing, structured redacted logging, readiness and protected metrics, header/CORS review with tests, dependency scanning, threat model and an honest gap list.

## 2. Requirements completed
- API keys (`kvl_<lookup>_<secret>`): 256-bit secret shown once, SHA-256 hash only stored with constant-time compare, scopes = permission codes limited to the creator's current permissions (effective permissions are recomputed as role ∩ scopes on every request), expiry (default 90 d, max 365), last-used, rotation, revocation, per-key rate limit via `app.core.ratelimit`, tenant-bound keys, keys cannot manage keys/webhooks, failed-key throttling per IP. `app/services/apikeys.py` implements the `authenticate(db, token, request)` contract used by `deps.get_ctx`.
- Webhooks: subscriptions, event catalog, HMAC-SHA256 `X-KVL-Signature: t=..,v1=..` with timestamp, show-once secret (Fernet at rest), rotation with 24 h dual signing, delivery log, retries 1m/5m/30m/2h then dead letter with redrive, delivery processor `deliver_due`, SSRF defences (https only, ports 443/8443, no userinfo, no IP literals including decimal/hex/octal, DNS must resolve only to global addresses, re-resolved at delivery and connection pinned to the validated IP, no redirects, no proxy env).
- Idempotency: `app/services/idempotency.py::run_idempotent` (used by `POST /support/tickets`).
- Developer catalogs: `GET /developer/scopes|events|usage`; OpenAPI tags, description, `Error` schema and standard error responses; `docs/api/overview.md`.
- Security events: failed-login surge (IP and account), refresh-token reuse, privilege-escalation attempts (recorded live from blocked 403 responses and from audit), webhook-URL-blocked, API-key rate-limited; alert routes (email or platform webhook `security.alert`) with severity/kind filters; acknowledge/resolve.
- Security console APIs: events, alert routes, audit viewer (filters + total), sessions overview/revoke, abuse settings (read-only), overview KPIs.
- Observability: JSON logs with request/correlation ids and redaction (Bearer, `kvl_` keys, JWTs, cookies, passwords, tokens; query strings never logged), `/readyz` (DB + Redis, no secrets), `/metrics` (Prometheus text, protected: `METRICS_TOKEN` bearer or platform `security:read`), in-process counters and latency histograms, error-reporting hook `register_error_reporter`.
- Headers and CORS: reviewed and tested (see section 8). Added a restrictive API `Content-Security-Policy`.
- Scanning: `scripts/security/scan.sh`; report in `docs/security/dependency-report.md`; secret-scan test.

## 3. Routes and screens
`/admin/security`: Overview KPIs, Incidents + alert routes, Audit log (filters, pagination, row detail), API keys & webhooks (all users), Sessions, Abuse controls. Reusable `components/security/{api-keys-panel,webhooks-panel,audit-table}.tsx` (panels take `{ tenantScoped?: boolean }`) for the lead to mount on the account page.

## 4. Components created
`frontend/src/components/security/{api-keys-panel,webhooks-panel,audit-table,security-console,secret-reveal}.tsx`.

## 5. Backend endpoints (under /api/v1 unless noted)
`/apikeys` (GET, POST, `/{id}/rotate`, DELETE), `/webhooks` (GET, POST, PATCH/DELETE `/{id}`, `/{id}/rotate-secret`, `/{id}/test`, `/{id}/deliveries`, `/deliveries`, `/deliveries/{id}/retry`, `/dispatch`), `/developer/{scopes,events,usage}`, `/security/{overview,events,events/{id}/acknowledge|resolve,scan,alert-routes,audit,sessions,sessions/{id}/revoke,abuse-settings}`; outside the version prefix: `/readyz`, `/metrics`.

## 6. Database models
`app/models/security.py`: api_keys, webhook_endpoints, webhook_deliveries, idempotency_records, security_events, security_alert_routes. No Alembic migration written (lead consolidates).

## 7. Integrations
Outbound HTTPS only to customer-supplied webhook URLs. Email through `queue_email` (dev outbox). Redis for rate limits and readiness. No Sentry/pager integration shipped; the error hook is the seam.

## 8. Authorization and tenant-isolation review
- Permissions: `apikeys:read|write` (customer, super admin), `apikeys:manage` (platform lists/revokes all keys), `webhooks:read|write|manage`, `security:read|manage`, `audit:read`. No role grants changed. Scopes `apikeys:*`, `roles:*`, `staff:*` are not delegable.
- Tenant users only see/operate their own keys, endpoints, deliveries; foreign ids return 404. A tenant cannot subscribe to `security.alert`; tenant events never reach another tenant's endpoints; platform endpoints receive all tenant events.
- Config review: security headers `nosniff`, `DENY`, strict referrer, `no-store`, Permissions-Policy, CSP `default-src 'none'; frame-ancestors 'none'` (docs excepted), HSTS outside dev; CORS allow-list (wildcard never used); unknown origins never receive Access-Control-Allow-Origin and preflight returns 400 (Starlette still emits `Access-Control-Allow-Credentials: true` without an origin, which browsers ignore). Cookies: HttpOnly, SameSite=Lax, refresh path-scoped. Findings: CORS `allow_headers` was already tight; no change needed. The Next.js frontend's own response headers are outside this review.

## 9. Security review
See `docs/security/threat-model.md` (16 threats with controls, tests and residual risk) and the gap list. Attachment path-traversal review: filenames are display-only, storage names are server random, path containment checked, uploads isolated per tenant/ticket (tests with `..`, backslash and absolute names).

## 10. Tests executed
`test_apikeys.py` (12), `test_webhook_subscriptions.py` (~66 incl. ~48 SSRF URL cases), `test_observability.py` (12), `test_security_headers.py` (8) pass: show-once + hash-only storage, scopes <= creator, scope shrink after permission loss, revoked/expired/unknown/disabled-owner rejected, rotation, per-key rate limit, guess throttling, other-tenant and tenant-bound-platform-key isolation, keys cannot manage keys/webhooks; SSRF (127.0.0.1, 10/8, 192.168/16, 172.16/12, 169.254.169.254, ::1, fe80::, ::ffff:127.0.0.1, localhost, .local/.internal, userinfo and `#@` tricks, decimal/hex/octal/short IP encodings, non-https schemes, ports, CR/LF, backslash, DNS names resolving to private/mixed/mapped/ULA/CGNAT via a fake resolver), DNS rebinding at delivery, redirects never followed, signature + replay window + tamper + rotation overlap, retry/backoff/dead-letter/redrive, tenant isolation of events, idempotency replay/mismatch/per-user/failed-not-cached; log redaction (unit + live requests), request/correlation ids, error reporter, readyz (ok/redis down/db down, no secrets), metrics protection (anon/customer/support/hosting-admin denied, token, no raw ids), OpenAPI polish, failed-login surge detection (dedupe, masking, severity), refresh-reuse and privilege-escalation events, alert routing + ack/resolve, audit viewer, sessions revoke, abuse settings; secure headers, HSTS gating, CORS unknown origins, CSRF, cookie flags, secret scan, `.env` hygiene, scanning tooling present.
Combined run: 175 passed (see Phase 16 report). `pip-audit`: 0 known vulnerabilities in 86 packages. `npm audit`: 16 moderate (dev toolchain, `minimatch`), see `docs/security/dependency-report.md`.

## 11. Lint/type/build results
- `npx eslint` on all new/changed frontend files (support, security, shell/topbar-menus, the three route files): clean (exit 0).
- `npx tsc --noEmit` (whole project): **no errors in any Phase 16/17 file**; the run reports pre-existing errors in other builders' files (automation, backups, billing, files pages), which were not touched.

## 12. Resource impact
In-process metrics only (bounded label cardinality via route templates). Idempotency rows older than 24 h are purged opportunistically on write. Delivery processing is on demand.

## 13. Known issues and limitations
- Webhook delivery and incident scan need a scheduler to run unattended; none ships here (`POST /webhooks/dispatch`, `POST /security/scan` and `POST /support/sla/run` are provided).
- The real webhook HTTP transport (IP pinning with SNI) is only verified by source inspection and fakes; it has not been exercised against a live TLS receiver. IPv6 and IP-literal URLs are rejected outright.
- Metrics are per-process; multi-worker deployments need an aggregating exporter. Log redaction is pattern-based. uvicorn's own access/error loggers keep their native format unless configured.
- Rate-limit storage falls back to in-process memory if Redis is down (pre-existing behaviour), so limits are per-process in that case.
- `/readyz` treats Redis as required, although the rate limiter degrades without it.
- Alert delivery is queued mail/webhook only; no real SMTP or pager was tested. Without routes configured nobody is notified.
- Audit table is not tamper-evident. No migration written. Backup/restore operational monitoring is whatever Phase 12 provides; no new checks were added here.
- npm audit: see dependency report for what actually completed.

## 14. Rollback instructions
Remove the Phase 17 files, the `install_observability(app)` line and import in `app/main.py`, and the Phase 16/17 settings in `config.py`; drop the tables in section 6. Existing `deps.get_ctx` then rejects `kvl_` keys again (ImportError path).

## 15. Next phase prerequisites
Scheduler (SLA, webhook dispatch, incident scan), migration consolidation, real SMTP/alert routes, a CI job for `scripts/security/scan.sh`, staged webhook transport test.

## 16. Owner approval status
Pending owner review.
