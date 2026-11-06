# COMPLETED_TASKS — verified work only

"Verified" = automated tests pass on Ubuntu with fakes/local services. **No item here has been tested against real nginx, Let's Encrypt, Razorpay, SMTP, Cloudflare, n8n or the Mac server.** Per the brief, that is not "done" — see `PENDING_TASKS.md`.

| Phase (brief §7) | Built (see `docs/phase-reports/`) | Evidence |
|---|---|---|
| 1 Foundation | App, config example, migrations, logging, health/ready, auth, RBAC | backend `test_auth`, `test_rbac_tenants`, `test_observability`, `test_security_headers` |
| 2 Accounts | Admin/customer flows, tenant model, lifecycle, isolation | `test_rbac_tenants` |
| 3 Websites/domains | Domains, verification, document roots, nginx template + test + rollback logic | `test_websites`, `test_domains`, `test_nginx_ops`; agent `test_nginx_ops` (fake binaries) |
| 4 Files | Tenant-scoped ops, ZIP safety, quotas | `test_files`; agent `test_files_ops` |
| 5 PHP/WordPress | App deployment ops | `test_apps`; agent `test_apps_ops` |
| 6 Databases | Lifecycle, grants, export/restore requests | `test_databases`; agent `test_databases_ops` |
| 7 SSL/DNS | Certificates, DNS records, Cloudflare connection | `test_ssl`, `test_dns`, `test_cloudflare`; agent `test_ssl_ops` |
| 8 Backup/monitoring | Schedules, retention, restore requests, metrics, alerts | `test_backups`, `test_monitoring`, `test_site_usage` |
| 9 Plans/billing | Plans, invoices, coupons, Razorpay (mocked), idempotency | `test_billing`, `test_razorpay` |
| Owner bootstrap | `python -m app.bootstrap_owner`: one-time first super_admin for non-dev envs (hidden password, policy, audit, refuses a second owner) | `backend/tests/test_bootstrap_owner.py` (5 pass); CLI run on a fresh migrated scratch DB |
| Extras | Support, KB/announcements, resellers, API keys, webhooks, n8n automation | corresponding `test_*.py` |

Test run on 2026-11-06: backend suite 549 passed, agent 239 passed, frontend eslint and `tsc --noEmit` clean. Details and caveats: `PROJECT_AUDIT.md`.

Next.js production build passed on 2026-11-06 (`npm run build`). Playwright e2e not re-run today.
