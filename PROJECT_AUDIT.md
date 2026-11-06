# PROJECT_AUDIT — actual repository and service audit

Audit date: 2026-11-06 · Branch: `feature/pdf-handoff-docs-audit` · Base commit: `eb039a8` (only commit on `main`)
Remote: `origin https://github.com/kamaralam1984/KVL-C-Penal.git` (no other remotes; only `main` exists on origin).

## Environments (per the handoff brief)
| Environment | State |
|---|---|
| Ubuntu laptop | Development workstation. Private Postgres 18 cluster at `~/.kvl-dev/pgdata` (port 54329), Redis on 6379 (shared with other projects; panel uses db 5 only). |
| GitHub | Single `main` branch. This audit lives on `feature/pdf-handoff-docs-audit`. |
| Mac/iMac server | **Not touched. Not inspected. Nothing deployed.** Needs owner approval (see `PENDING_TASKS.md`). |

## Stack found (not replaced)
| Layer | Found |
|---|---|
| Frontend | `frontend/` Next.js 16.4.3, React 19.3, Tailwind, next-themes (light/dark), Playwright e2e (`a11y`, `auth`, `mobile` specs) |
| API | `backend/` FastAPI; 328 paths / 380 operations in OpenAPI; 31 router modules in `backend/app/api/v1/routers` |
| Data | PostgreSQL via SQLAlchemy + Alembic (3 migrations); **87 tables** |
| Queue/cache | Redis (rate limits, jobs) |
| Host agent | `agent/` (`kvl_agent`), fixed allow-listed operations: nginx, ssl, files, databases, backups, apps, site usage |
| Ops | `ops/systemd/*` (api, web, agent, scheduler + timer), `ops/nginx/panel.conf.example` |
| Automation | `automation/n8n/*.json` (12 workflows) |
| Scripts | `scripts/release.sh`, `scripts/loadtest.py`, `scripts/security/scan.sh` |
| Docs | `docs/` (architecture, runbooks, security, 18 phase reports) |

No `Dockerfile` or `docker-compose.yml` exists. There is no second duplicate application (brief §2 check).

## Brief module coverage (code exists — see `COMPLETED_TASKS.md` for evidence level)
Admin/customers/RBAC/sessions (`auth`, `rbac`, `tenants`, `staff`), hosting accounts, websites/domains, nginx automation (`app/ops/nginx.py`, agent `nginx`), file manager (`filemgr`, `files`), apps/PHP/WordPress (`apps`), databases, SSL/DNS/Cloudflare, backups, monitoring/resources, plans/billing/Razorpay, support, resellers, webhooks, API keys, automation.

## Verification run on 2026-11-06 (Ubuntu only)
| Check | Command | Result |
|---|---|---|
| Backend tests | `USE_MIGRATIONS=1 REDIS_URL=redis://127.0.0.1:1/0 TEST_DATABASE_URL=… pytest -q` (in `backend/`) | **549 passed** (on branch `feature/macos-staging-setup`, includes 5 new bootstrap-owner tests; 544 before) |
| Agent tests | `pytest -q` (in `agent/`, using backend venv) | **239 passed** in 30.9 s |
| Frontend lint | `npx eslint` | exit 0, no output |
| Frontend types | `npx tsc --noEmit` | no errors |
| Frontend build | `npm run build` | passed (all routes built) |
| Playwright e2e | not re-run in this session | unverified today (11 were green on 2026-11-02) |

## Gaps found
1. Required top-level docs from the brief were missing (README was a one-line stub) — added in this branch.
2. No `Dockerfile`/compose for isolated Linux service simulation (brief §6 suggests containers "where practical").
3. `backend/.env.example` is the only env example; no `frontend` or `agent` example.
4. Zero real-service testing (real nginx, Let's Encrypt, Razorpay test mode, SMTP, Cloudflare, n8n) and zero deployment — see `PENDING_TASKS.md`.
5. Local dev note: `pg_ctl` fails if the default socket dir `/var/run/postgresql` is not writable; start with `-k ~/.kvl-dev/sock` (see `INSTALLATION.md`).
