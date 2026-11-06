# KVL Control Panel

cPanel-style hosting control panel for KVL Business Solutions: Next.js frontend, FastAPI backend, PostgreSQL, Redis and a least-privilege host agent.

> **Status:** built and tested on Ubuntu with fakes. **Not deployed anywhere. No live customer hosting** until the gate in `PRODUCTION_READINESS.md` passes and the owner approves.

## Layout
| Path | Purpose |
|---|---|
| `frontend/` | Next.js admin and customer dashboards |
| `backend/` | FastAPI API, Alembic migrations, services |
| `agent/` | Host agent (fixed allow-listed operations) |
| `ops/` | systemd units, nginx example |
| `automation/n8n/` | n8n workflow exports |
| `scripts/` | release, load test, security scan |
| `docs/` | architecture, runbooks, security, phase reports |

## Documents
`INSTALLATION.md` · `SERVER_CONFIGURATION.md` · `DATABASE_SCHEMA.md` · `API_DOCUMENTATION.md` · `DEPLOYMENT.md` · `BACKUP_RESTORE.md` · `PRODUCTION_READINESS.md` · `PROJECT_AUDIT.md` · `COMPLETED_TASKS.md` · `PENDING_TASKS.md` · `SECURITY_AUDIT.md` · `DEVELOPMENT_ROADMAP.md`

## Workflow
Ubuntu = development, GitHub = source of truth, Mac = reviewed deployment target. Use feature branches and pull requests; never commit secrets; a push does **not** update the Mac.
