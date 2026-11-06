# INSTALLATION — fresh developer environment (Ubuntu)

Prereqs (verified versions on the dev laptop): Python 3.14, Node 24, PostgreSQL 18, Redis, Git 2.53.

## 1. Postgres (private dev cluster)
Use a private cluster; do not touch the system Postgres or other projects' ports.
```bash
initdb -D ~/.kvl-dev/pgdata          # once
mkdir -p ~/.kvl-dev/sock
/usr/lib/postgresql/18/bin/pg_ctl -D ~/.kvl-dev/pgdata -l ~/.kvl-dev/pg.log \
  -o "-p 54329 -k $HOME/.kvl-dev/sock" start
```
The `-k` flag avoids "could not create lock file /var/run/postgresql…: Permission denied". Create role `kvl` and databases `kvl_panel` and `kvl_panel_test` (dev-only password; never reuse it).

## 2. Backend
```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
cp .env.example .env        # set DATABASE_URL (port 54329), REDIS_URL (db 5), generate SECRET_KEY / ENCRYPTION_KEY
.venv/bin/alembic upgrade head
.venv/bin/python -m app.seed
.venv/bin/uvicorn app.main:app --port 8000
```

## 3. Frontend
```bash
cd frontend && npm ci && npm run build && npm run start    # :3000
```

## 4. Agent (dev only)
`agent/run_local.py` runs a local test agent (data in `agent/.local-agent`, git-ignored).

## 5. Tests
```bash
cd backend && USE_MIGRATIONS=1 REDIS_URL=redis://127.0.0.1:1/0 \
  TEST_DATABASE_URL=postgresql+psycopg://…/kvl_panel_test .venv/bin/python -m pytest -q
cd agent && ../backend/.venv/bin/python -m pytest -q
cd frontend && npx eslint && npx tsc --noEmit && npx playwright test
```
Clear `rl:dev:*` keys in Redis db 5 before e2e runs (login rate limits). Never use broad `pkill -f`; stop processes by PID from `ss -ltnp`.
Demo logins are in `backend/.dev-credentials` (git-ignored, dev only).
