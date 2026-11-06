# DEVELOPMENT_ROADMAP — ordered phases, dependencies, acceptance criteria

Mirrors brief §7. Status as of 2026-11-06.

| Phase | Acceptance evidence (brief) | Depends on | Status |
|---|---|---|---|
| 0 Audit | Inventory + reports | — | Done (`PROJECT_AUDIT.md` and siblings) |
| 1 Foundation | Runs locally, env examples, migrations, logging, health, auth/RBAC tests | 0 | Done on Ubuntu; missing frontend/agent `.env.example` |
| 2 Accounts | Admin/customer flows, tenant isolation tests | 1 | Done on Ubuntu |
| 3 Websites/domains | Verification, docroot, nginx template, config test, rollback | 2 | Code + fake-binary tests; **real nginx pending** |
| 4 Files | Tenant-safe ops, ZIP safety, quota tests | 2 | Done on Ubuntu |
| 5 PHP/WordPress | PHP-FPM pools, WP install on isolated test site | 3 | Code + fakes; **real install pending** |
| 6 Databases | MariaDB lifecycle, least-privilege, export/restore | 2 | Code + fakes; **real MariaDB pending** |
| 7 SSL/DNS | Cert lifecycle, scoped Cloudflare | 3 | Code + mocks; **real ACME/Cloudflare pending** |
| 8 Backup/monitoring | Scheduled backups, successful restore drill, metrics, alerts | 4,6 | Code; **restore drill pending** |
| 9 Plans/billing | Server-side limits, invoices, payment, expiry | 2 | Code + mocked Razorpay; **test-mode pending** |
| 10 Release readiness | Security review, Linux rehearsal, load tests, rollback, approval | all | **Not started** — `PRODUCTION_READINESS.md` |

Next batches, in order (each: feature branch, tests, PR, commit hash reported):
1. Re-verify frontend build + e2e; CI workflow; env examples.
2. Owner-approved read-only Mac inspection → backups → staging install.
3. Real-service integration tests (nginx, SSL, backup/restore, Razorpay test, SMTP, Cloudflare, n8n).
4. Security remediation items; load tests; Linux production rehearsal.
5. Owner sign-off → production.
