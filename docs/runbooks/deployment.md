# Deployment runbook (staged)

> **Production safety.** Nothing in this repository deploys to the production iMac automatically. Production DNS, customer files and databases are never changed without explicit owner approval. This runbook describes *staging first*, then an owner-approved production rollout.

## 0. Preconditions
- Release candidate tagged (`vX.Y.Z`) and the commit recorded in `docs/releases.md`.
- `scripts/release.sh --check` passes (tests, lint, types, build).
- Fresh backup of the control-plane database verified (`pg_dump` + restore test on staging).
- Owner approval recorded (production only).

## 1. Staging
1. Provision staging host/user (never the production iMac). Use `ops/systemd/*.service` and `ops/nginx/panel.conf.example` as templates; copy `backend/.env.example` → `.env` and set **new** secrets (`SECRET_KEY`, `ENCRYPTION_KEY`, DB URL, `COOKIE_SECURE=true`, `ENV=staging`). Use non-production Razorpay keys.
2. `python -m venv .venv && pip install -r requirements.lock`.
3. **Back up** the database, then `alembic upgrade head` (migrations are a controlled step, never at app start).
4. `python -m app.seed` (idempotent roles/permissions/settings).
5. Build the UI: `npm ci && npm run build`; start with `npm run start` behind nginx.
6. Health checks: `GET /healthz` → 200, `GET /readyz` → 200 (DB + Redis), login works, agent heartbeat appears under Servers, a no-op job (`agent.ping`) succeeds.
7. Run the staging test suite: `pytest` (with `USE_MIGRATIONS=1`) and `npx playwright test`.

## 2. Production (owner-approved only)
1. Announce a maintenance window; take backups (DB + config). Confirm the previous release directory is intact.
2. Deploy into a new release directory; keep the previous one for rollback.
3. `alembic upgrade head` (after the backup). Keep migrations backward-compatible for one release.
4. Switch the `current` symlink, restart services one at a time, verify health checks and agent heartbeat.
5. Monitor error rate, job failures and heartbeat for 30 minutes before closing the window.

## 3. Rollback
- App: point `current` back to the previous release directory and restart services.
- Database: prefer forward-fix. If a migration must be undone and it is reversible, `alembic downgrade -1` **after** confirming no data loss; otherwise restore the pre-deploy backup into a new database and switch `DATABASE_URL`.
- Agent: previous agent package version is kept; `systemctl restart kvl-agent`.

## 4. Release record
Record version, commit, migration revision, date, approver, verification results.
