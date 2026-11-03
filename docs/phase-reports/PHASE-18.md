# KVL Hosting Panel — Phase 18 Report: QA, staging readiness and launch preparation

> **Nothing has been deployed.** The production iMac, production DNS, customer files and databases were not touched. Production launch requires explicit owner approval (`docs/launch-checklist.md`).

## 1. Test summary (all run on this development machine)
| Suite | Result |
|---|---|
| Backend (pytest, against the Alembic-migrated schema, `USE_MIGRATIONS=1`) | **538 passed** |
| Agent (pytest) | **233 passed** |
| Frontend `tsc --noEmit` / ESLint / `next build` (60 routes) | all clean |
| Playwright e2e (system Chrome): auth, 2FA setup, role gating, phone layout, tap-target size, WCAG 2 A/AA axe scan on login/register/home/admin dashboard in light **and** dark | **11 passed** |
| `alembic upgrade head` on an empty DB, then `alembic check` | no schema drift |

Defects found and fixed during this phase (kept here as evidence the gates have teeth): Alembic `fileConfig` silently disabled application loggers when migrations ran in-process; tests shared mutated role grants across modules; one webhook-tamper test passed/failed by chance (1 in 16); an unrelated "no `password` in metrics" assertion was too naive; the account menu crashed on open (Radix `asChild` slot with two children); animated icons were keyboard-focusable while `aria-hidden`; several light-theme text colours fell below 4.5:1 contrast.

## 2. Security review
See `docs/security/threat-model.md`, `gap-list-and-remediation-plan.md`, `dependency-report.md`. `pip-audit`: 0 known vulnerabilities (86 packages). `npm audit`: 16 moderate, 0 high/critical, all in the lint toolchain (one `minimatch` advisory); not exploitable in the shipped app. **No SOC 2 / ISO 27001 / PCI claim is made.**

## 3. Performance / resource report
Indicative only, on a shared 16 GB laptop (load average 5–18 from other work), API with 2 uvicorn workers, 20 concurrent clients (`scripts/loadtest.py`):

| Endpoint | req/s | p50 | p95 | errors |
|---|---|---|---|---|
| `/healthz` | 208 | 70 ms | 261 ms | 0 |
| `/api/v1/auth/me` | 121 | 130 ms | 357 ms | 0 |
| `/api/v1/tenants?limit=25` | 95 | 164 ms | 460 ms | 0 |
| `/api/v1/stats/dashboard` | 66 | 272 ms | 506 ms | 0 |

Resident memory observed: Next.js server ≈ 138 MB, each uvicorn worker ≈ 28 MB, each Postgres backend ≈ 25 MB. These numbers are not a capacity plan; re-measure on the target hardware before launch.

## 4. Known limitations (honest list)
- **Not exercised against real services:** Razorpay (test mode needs keys + a reachable webhook URL), Cloudflare, ACME/certbot, n8n, SMTP, a live agent on the iMac, real nginx/PM2/MariaDB. Each reports `not_configured` instead of pretending. Ops handlers were tested with fake binaries and sandboxes.
- **Scheduler** (`app.services.scheduler`, `ops/systemd/kvl-scheduler.*`) is provided but not installed anywhere.
- **No email transport:** alerts, OTPs and invitations are queued in `email_outbox`.
- File transfer through the job queue is capped (10 MB up / 2 MiB down); only full backups; SFTP/S3 off-site copy is modelled but not implemented; database backups need a staged dump from the databases module.
- Reseller→client invoicing, e-invoice (IRN), TCS/TDS, PDF invoices, mid-period interval changes, WebAuthn, malware scanning of attachments, incident delivery without alert routes are not implemented.
- Production `nginx.conf` must include the agent's `sites/*.conf` directory; PHP/WordPress webroot wiring and git deploy keys are manual steps (documented in Phase 6–7 reports).
- Visual regression, cross-browser (Firefox/Safari) and real-device checks were not run; only Chrome desktop and Chrome emulating an iPhone 13.
- The repository is not under Git; there is no commit hash to record.

## 5. Deliverables
Runbooks: `docs/runbooks/{deployment,monitoring-incident,backup-and-recovery,n8n}.md`; `docs/launch-checklist.md`; `docs/architecture.md`; `docs/agent-deployment.md`; `docs/releases.md`; `scripts/release.sh --check` (gate script, never deploys); systemd and nginx templates in `ops/`.

## 6. Rollback
Nothing was deployed. Local rollback = delete the dev databases (`~/.kvl-dev`), `backend/.env`, `backend/.dev-credentials`.

## 7. Owner approval
Pending. Next steps for the owner: (1) review this pack; (2) provide staging credentials; (3) approve a staging deployment; (4) after the staging run, approve the production rollout window.
