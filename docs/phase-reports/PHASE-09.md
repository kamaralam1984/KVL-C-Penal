# KVL Hosting Panel — Phase 09 Report (SSL, DNS and Cloudflare)

## 1. Phase summary
Certificate lifecycle (metadata only, ACME through an agent `certbot` operation, staging by default), a DNS record mirror with an explicit
preview -> confirm -> apply -> verify -> rollback workflow that provably touches exactly one record, and a Cloudflare integration restricted to
scoped API tokens (encrypted, never returned). Frontend: `hosting/ssl` (certificates, DNS, Cloudflare, audit) and read-only `customer/ssl`.

## 2. Requirements completed
- SSL: per-domain certificate model (pending / issued / renewing / expired / failed); request flow with prerequisite checks (domain syntax, ownership, webroot, server, ACME contact email, DNS resolves to this server); staging default, production only with `production=true` **and** `ssl:manage`; renewal sweep `sweep_renewals()` (idempotent, back-off 6h x 2^n, gives up after 5 failures with one alert, marks lapsed certs `expired`); expiry dashboard data (7/14/30-day buckets, severity); failure alerts (audit event + queued email, once per failure); metadata only.
- DNS: types A, AAAA, CNAME, TXT, MX, NS, CAA, SRV with per-type value rules; change-request workflow (previewed -> applied / failed / violation / rejected) with before/after diff, warnings, rollback instructions and a "prepare rollback" action that creates a new *preview*; propagation verification with a pluggable resolver (proxied records are verified through the provider API instead); full audit (before/after in the event).
- Preservation guarantee: a change targets one record id. `apply_change` re-reads the zone, refuses a stale preview (`stale_preview`), performs exactly one provider mutation, re-reads the zone and compares every other record; any difference marks the change `violation`, audits `dns.integrity_violation` and queues an alert. MX/SPF/DKIM/DMARC need an explicit `protected_ack` even as the target; a second SPF/DMARC and apex NS edits are rejected.
- Production gate: applying a change to a live provider zone needs `dns:write`, `dns:manage` (owner level), `confirm=true` and `confirm_production=true`.
- Cloudflare: httpx client with Bearer scoped tokens; global API keys rejected (37-hex shape and format check); token encrypted with `encrypt_secret`, only a short sha256 fingerprint is exposed; zone discovery limited to what the token returns; proxy status shown; 429/`Retry-After` handling (bounded retries then `429` to the caller); 401/403 mapped to `invalid_token` / `permission_denied`; connection re-check marks status.

## 3. Routes and screens
`/hosting/ssl` (tabs Certificates, DNS, Cloudflare, Audit), `/customer/ssl` (read-only certificate status and DNS records for own domains).

## 4. Components created/updated
`frontend/src/components/ssl/{types.ts,certificates.tsx,ssl-dns-page.tsx}`, `frontend/src/components/dns/{records.tsx,cloudflare-card.tsx}`, route files `app/hosting/ssl/page.tsx` (placeholder replaced), `app/customer/ssl/page.tsx` (new).

## 5. Backend endpoints (`/api/v1`)
- SSL: `GET /ssl/overview`, `GET /ssl/domains`, `GET /ssl/certificates`, `GET /ssl/certificates/{id}`, `GET /ssl/prerequisites?domain=`, `POST /ssl/certificates` (ssl:write; production needs ssl:manage), `POST /ssl/certificates/{id}/renew`, `PATCH /ssl/certificates/{id}` (auto_renew), `POST /ssl/sweep` (ssl:manage, platform only).
- DNS: `GET /dns/{domain}/records`, `POST /dns/{domain}/sync`, `POST /dns/{domain}/changes` (preview only), `GET /dns/{domain}/changes`, `GET /dns/changes/{id}`, `POST /dns/changes/{id}/confirm`, `/verify`, `/rollback`, `/reject`, `GET /dns/audit`.
- Cloudflare: `GET|PUT|DELETE /cloudflare/connection`, `POST /cloudflare/connection/check`, `GET /cloudflare/zones`.
- Resources (shared interim registry, also used by phases 10/11): `GET|POST /resources`.
- Agent operations: `ssl.request_certificate`, `ssl.renew`, `ssl.inspect` (`agent/kvl_agent/operations/ssl.py`, mirrored in `backend/app/ops/ssl.py`).

## 6. Database models and migrations
`certificates`, `cloudflare_connections`, `dns_records`, `dns_changes` (`app/models/ssl.py`), `resource_refs` (`app/models/ownership.py`). No Alembic revision was generated (lead consolidates); tests build the schema with `create_all`.

## 7. Integrations
Cloudflare API v4 (real client, tests use fake transport/fake client). ACME via `certbot` on the agent host (not installed here: an honest `certbot_not_installed` failure surfaces in the UI). DNS lookups via dnspython (pluggable `resolver_factory`).

## 8. Authorization and tenant-isolation review
Permissions: `ssl:read` view; `ssl:write` request/renew/auto-renew; `ssl:manage` production issuance + sweep; `dns:read` records/audit/connection status; `dns:write` previews, sync, verify, rollback preparation; `dns:manage` (super_admin only) connect Cloudflare and apply production changes. Customers hold only `ssl:read` and `dns:read`. Domain ownership goes through `owns_resource()` (`app/services/ownership.py`); anything not owned (including another tenant's domain, certificate, change or connection) is 404. Platform staff must name `tenant_id`. No change to `rbac.py`.

## 9. Security review
No private key is read, stored, logged or returned (agent reads `cert.pem` only; failure text is scrubbed of PEM blocks). Cloudflare token: validated before storing, encrypted, never in responses, audit detail or exceptions. Production ACME and production DNS both need explicit flags. All DNS writes are audited with before/after. certbot runs without a shell with sandboxed config/work/log dirs; staging certificates are marked untrusted in the UI.

## 10. Tests executed
`backend/tests/test_ssl.py`, `test_dns.py`, `test_cloudflare.py`, `agent/tests/test_ssl_ops.py` (backend: test_ssl 20, test_dns 64, test_cloudflare 14 = 98 passed; agent: test_ssl_ops 6 passed). Covered: invalid domain, unowned domain, prerequisites, issue/failure/renewal/renewal-failure, sweep (due-only, idempotent, back-off, give-up, expiry), dashboard buckets, no private key exposure, DNS validation per type (valid and invalid tables), name normalisation, preview-does-nothing, production confirmation and owner gate, only-target-modified (create/update/delete) with all other records byte-identical, mail-record acknowledgement, SPF/DMARC duplicates, CNAME conflicts, integrity-violation detection, stale preview, provider errors (permission, rate limit), verification (pluggable resolver, proxied), rollback preparation, tenant isolation, token permission failure, global-key rejection, token encryption, zone discovery limits.

## 11. Lint/type/build results
Backend: 98 tests green for this phase. Frontend: `tsc --noEmit` clean for these files; `eslint src/components/dns` clean (ssl components checked by `tsc`). `next build` deliberately not run (shared `.next`).

## 12. Resource impact
No new services. Cloudflare calls happen only on connect/sync/confirm/verify. Sweep is O(certificates).

## 13. Known issues and limitations
- Real certificate issuance and real Cloudflare traffic are untested here (no certbot, no credentials). The agent daemon must report job results for the UI to move out of `pending`.
- Domain ownership relies on the interim `resource_refs` registry until Phase 6 domains call `register_resolver("domain", ...)`; the domain row must also carry `root_rel` (ACME webroot), `server_id` and optionally `attrs.server_ips`.
- Wildcard certificates (DNS-01) are not supported (HTTP-01 only).
- Verification of TXT/SRV via public resolvers is best-effort; propagation states are `pending` until observed.
- Renewal needs a periodic caller of `POST /ssl/sweep` or `sweep_renewals()` (not scheduled by this phase).
- A violation is detected after the fact; the provider offers no transactions, so the system alerts and offers rollback preparation rather than preventing it.
- Zone records are mirrored for the target domain only; pagination/size of very large zones is bounded by the Cloudflare client loop.

## 14. Rollback instructions
Revert the files listed above; drop tables `certificates`, `cloudflare_connections`, `dns_records`, `dns_changes`, `resource_refs`. No existing table was altered. Issued certificates on the server are untouched by removing the panel code.

## 15. Next phase prerequisites
Agent daemon executing queued jobs; a periodic scheduler; Phase 6 domains wiring (`register_resolver`, webroot/server ids); certbot installed on the server; a scoped Cloudflare token (Zone:Read, DNS:Read, DNS:Edit).

## 16. Owner approval status
Pending owner review. Production ACME and live DNS changes are disabled until explicitly requested.
