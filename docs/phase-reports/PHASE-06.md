# KVL Hosting Panel — Phase 06 Report: Website and Domain Management

## 1. Phase summary
Tenant-owned websites and domains, DNS TXT ownership verification, and Nginx provisioning (stage -> validate -> activate -> explicit reload -> rollback) through the restricted agent. The API only enqueues jobs; finished job results are applied lazily on read (`sync_website`).

## 2. Requirements completed
- Website lifecycle: create, plan/server assignment (platform only), controlled document root `/var/www/kvl/<tenant-slug>/<site-slug>/<public_subdir>` (never customer text), runtime static/php with version checked against the server's reported catalog, suspend/reactivate/archive (archive unmaps domains, frees quota), usage summary (disk honestly `not_reported`), activity history, quota `websites`.
- Domains: strict IDNA/UTS46 validation (no wildcards, ports, underscores, trailing dots, IPs, reserved TLDs, bidi/zero-width tricks, >253/63), globally unique normalised hostname (cross-tenant conflict is a generic 409 that does not reveal the owner), subdomain of another tenant's verified domain blocked, same-tenant subdomain of a verified domain auto-verified, alias/subdomain kinds, website mapping, expiry/registrar metadata, quota `domains`, DNS TXT challenge `_kvl-verify.<host>` = `kvl-verify=<token>`.
- Pluggable resolver (`services/dnsverify.py`): dnspython when installed (it is in the venv; `backend/requirements-phase6.txt`), otherwise `UnavailableResolver` -> state `unavailable`. Tests inject a fake.
- Nginx: agent ops `nginx.stage_site`, `nginx.activate_site`, `nginx.reload`, `nginx.rollback_site`, `nginx.remove_site` (mirrored in `backend/app/ops/nginx.py`). Fixed templates (static/php/proxy), every value regex-validated on both sides, document root restricted to the prefix, proposed config written to `nginx/proposed/`, validated in an isolated wrapper config with `nginx -t -c`, backup before replacement, `nginx -t` again after install with automatic restore on failure, reload only via its own op (which re-tests first), rollback restores the chosen/latest backup and keeps the replaced config as a new backup. No nginx binary -> `status: not_configured`, never success.
- No server registered: website stays `pending_server` with a clear message; `POST /websites/{id}/provision` is the retry.

## 3. Routes and screens
`/{admin,hosting,customer}/websites` and `/domains` (shared `components/websites/websites-page.tsx`, `components/domains/domains-page.tsx`): lists, add wizards, detail drawers, provisioning progress with polling, retry, DNS instructions with copyable TXT, activity timeline.

## 4. Components created
`frontend/src/components/websites/*`, `frontend/src/components/domains/*`.

## 5. Backend endpoints
Websites (`/api/v1/websites`): GET list; POST create (`websites:write`); GET `/{id}`; PATCH `/{id}` (`websites:write`; server/plan platform only); POST `/{id}/provision` (`websites:write`, stage+validate, also retry); POST `/{id}/activate`, `/reload`, `/rollback`, `/suspend`, `/reactivate`, `/archive` (`websites:manage`); GET `/{id}/activity`, `/{id}/usage` (`websites:read`).
Domains (`/api/v1/domains`): GET list, GET `/resolver`, GET `/{id}`, GET `/{id}/activity` (`domains:read`); POST create, POST `/{id}/verify`, PATCH `/{id}`, DELETE `/{id}` (`domains:write`).

## 6. Database models and migrations
`websites`, `domains`, `resource_events` (in `app/models/hosting.py`; shared with phases 7-8). No migration generated (lead consolidates).

## 7. Integrations
dnspython (optional), Nginx via agent. No DNS records are ever created or changed.

## 8. Authorization and tenant-isolation review
Every lookup goes through `services/access.py` (`scope_stmt`/`get_visible`): another tenant's id -> 404 for every route; resellers only see their tenants' rows (read-only); platform may pass `tenant_id`. Customers hold `websites:write` (provision/stage) but not `websites:manage`: activating live config, reloading, rolling back, suspending and archiving are platform-staff operations (a deliberate "explicit authorised operation").

## 9. Security review
Hostile hostnames/paths are rejected by the API validators, again by the enqueue allow-list schemas, again by the agent's schemas, and a last character-set check runs on the rendered values. Customers cannot send Nginx text. Config hash is bound between stage and activate (a changed proposal is refused).

## 10. Tests executed
Backend `tests/test_domains.py` (51), `tests/test_websites.py` (14), `tests/test_nginx_ops.py` (12): all pass. Agent `agent/tests/test_nginx_ops.py` (32) with a fake `nginx` executable on PATH: staging, `nginx -t` failure leaves nothing, activation failure restores the previous config and does not reload, reload refuses when `nginx -t` fails, backup+rollback, not_configured, dry-run, hash mismatch.

## 11. Lint/type/build results
Frontend: `tsc --noEmit` clean for these files, `eslint` clean. `next build` was NOT run (shared `.next`; per brief).
Backend/agent: pytest (see §10).

## 12. Resource impact
No new services. Agent ops are on-demand. DNS verification issues read-only TXT lookups from the API process.

## 14. Rollback instructions
Remove the router/model/service/ops files listed above and drop the new tables (no Alembic migration was generated; the lead consolidates migrations).

## 16. Owner approval status
Pending owner review.

## 13. Known issues and limitations
- The system `nginx.conf` must `include <agent data_dir>/nginx/sites/*.conf;` (agent sandbox rule: writes only under `data_dir`). Installing that include, TLS vhosts (Phase 9), and `/var/www/kvl` directory creation are not done here. `nginx -t` on the live config is only as meaningful as that include.
- Op failures are reported as `result.ok=false` with a `status` (the job itself "succeeds" at transport level); the API treats `ok=false` as failure.
- A tenant can squat a hostname before its real owner verifies it (first claim wins, unverified); platform staff must delete the claim. No dispute workflow.
- Domain expiry is manual metadata (no WHOIS/registrar API).
- Disk usage per site is `not_reported` until monitoring supplies it.
- Test runs are slow on the shared DB (about 5 s per test fixture reset).

## 15. Next phase prerequisites
Phase 9 (SSL/DNS) should add a TLS variant of the template; the agent daemon must return op results into `agent_jobs.status/result` as the API expects.
