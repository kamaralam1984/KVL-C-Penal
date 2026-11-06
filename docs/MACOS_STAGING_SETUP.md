# macOS staging setup (Ubuntu → GitHub → Mac)

> **Scope.** A *staging* copy of the KVL panel on the Mac, isolated from the Mac's live sites. **Not production. No customer hosting, no real domains.** Every step that changes the Mac needs the owner's explicit "haan". This guide has **not been run on a Mac yet**: commands are written from the repository and standard macOS/Homebrew behaviour and are unverified until the inspection in step 1.
>
> The Mac runs macOS Big Sur (per the handoff brief). systemd units in `ops/systemd/` do **not** apply; this guide uses launchd user agents in `ops/launchd/` instead. Final production should be a separate Linux host (brief §6).

## Isolation design
Everything lives under one directory, `KVL_HOME` (default `~/kvl-staging`), run as your normal (non-root) user via launchd **user agents**. Nothing touches the Mac's existing nginx, PHP-FPM, MariaDB, Redis, ports 80/443 or system LaunchDaemons.

| Component | Staging port (loopback only) | Why separate |
|---|---|---|
| PostgreSQL (own cluster) | 54330 | existing DBs untouched |
| Redis (own instance) | 63790 | existing Redis untouched |
| API (uvicorn) | 18000 | |
| Web (Next.js) | 13000 | |
| Agent health | 19710 | |

Layout: `$KVL_HOME/app` (git checkout), `etc/` (env files, 600), `var/` (pgdata, redis, agent state, logs, sockets), `launchd/` (rendered plists).

## 0. Move the reviewed code to the Mac
Ubuntu: push the reviewed branch and open a PR (owner approval needed to push). Mac: `git clone` or `git fetch` + `git checkout <commit>` into `$KVL_HOME/app`; record the commit hash. A push alone never changes the Mac.

## 1. Read-only inspection (changes nothing)
Copy `scripts/mac/inspect-readonly.sh` to the Mac, run `bash inspect-readonly.sh | tee ~/kvl-inspect.txt`, read it, then paste it back. It reports OS, tool versions, free ports (the five above must say "free"), nginx/MariaDB/Redis presence and which sites exist. **Decide from this output** whether staging fits; stop if a port is in use or disk is low.

## 2. Backup existing configs (copy-only)
`bash scripts/mac/backup-configs.sh --yes` (add `--with-mariadb` only if you want a DB dump and `~/.my.cnf` has credentials). Creates `~/kvl-backups/<timestamp>/` with checksums. Back up website document roots separately. Keep backups out of Git.

## 3. Prerequisites (Homebrew; may compile from source on Big Sur and take long)
Need: Python ≥ 3.11 (agent `requires-python`; the backend lock was produced on **3.14**, so verify `pip install -r requirements.lock` works on the Mac's Python before going further), Node (Next 16 needs a current LTS), PostgreSQL (any recent; example uses `postgresql@16`), Redis. Install only what is missing, and **never** `brew upgrade` existing services. Use `brew install <formula>` without `brew services start` (the plists run staging copies instead).

## 4. Directories and env files
```bash
export KVL_HOME="$HOME/kvl-staging"
mkdir -p "$KVL_HOME"/{etc,var/{pgdata,redis,sock,log,agent},launchd}
chmod 700 "$KVL_HOME/etc" "$KVL_HOME/var"
cp ops/launchd/staging.env.example "$KVL_HOME/etc/api.env"; chmod 600 "$KVL_HOME/etc/api.env"
sed "s#__KVL_HOME__#$KVL_HOME#g" ops/launchd/agent.env.example > "$KVL_HOME/etc/agent.env"; chmod 600 "$KVL_HOME/etc/agent.env"
```
Edit `api.env`: generate **new** `SECRET_KEY`, `ENCRYPTION_KEY` and DB password (commands are in the file). Secrets stay on the Mac, never in chat or Git. Razorpay/SMTP/Cloudflare/n8n values go here later, **test keys only**.

## 5. Staging PostgreSQL
```bash
PGBIN=/opt/homebrew/opt/postgresql@16/bin       # adjust to what step 1 showed
$PGBIN/initdb -D "$KVL_HOME/var/pgdata" -U postgres --auth-local=trust --auth-host=scram-sha-256
```
Start it through launchd (step 7), then create role and DB on port **54330**:
```bash
$PGBIN/psql -h 127.0.0.1 -p 54330 -U postgres -c "CREATE ROLE kvl_staging LOGIN PASSWORD '<same as api.env>'"
$PGBIN/psql -h 127.0.0.1 -p 54330 -U postgres -c "CREATE DATABASE kvl_panel_staging OWNER kvl_staging"
```

## 6. Backend and frontend
```bash
cd "$KVL_HOME/app/backend" && python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
set -a; . "$KVL_HOME/etc/api.env"; set +a
.venv/bin/alembic upgrade head        # new empty DB, so no data at risk
.venv/bin/python -m app.seed
cd ../frontend && npm ci && npm run build
cd ../agent && python3 -m venv .venv && .venv/bin/pip install .
```
**Open item:** the repo documents no production-safe way to create the first owner account (`backend/scripts/dev_seed.py` is dev-only: do **not** run it on staging). This must be added before the panel is usable on the Mac. Tracked in `PENDING_TASKS.md`.

## 7. Render and load launchd agents
```bash
bash scripts/mac/render-launchd.sh "$KVL_HOME" "$KVL_HOME/launchd" "$PGBIN" "$(command -v redis-server)"
less "$KVL_HOME"/launchd/*.plist          # review every file
cp "$KVL_HOME"/launchd/com.kvl.staging.*.plist ~/Library/LaunchAgents/
for s in postgres redis api web; do launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kvl.staging.$s.plist; done
```
Load `scheduler` and `agent` after enrolment (step 8). Logs: `$KVL_HOME/var/log/*.log`. (`launchctl bootstrap` is the modern form; on older macOS use `launchctl load -w <plist>`; confirm which works on Big Sur.)

## 8. Smoke tests, then agent enrolment
```bash
curl -s http://127.0.0.1:18000/healthz ; curl -s http://127.0.0.1:18000/readyz   # database + redis ok
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:13000/               # 200
```
Create a server enrolment token in the panel, enrol once (`python -m kvl_agent enroll --api-url http://127.0.0.1:18000/api/v1 --token kvl_enr_...` with `KVL_AGENT_DATA_DIR` set), then load `scheduler` and `agent`. Expect a heartbeat under Servers and a successful `agent.ping` job. The agent starts with **`KVL_AGENT_DRY_RUN=true`**, so nginx/SSL operations only write proposals under its own data dir. Keep it that way until the real-service tests are reviewed with the owner.

## 9. Access from the Ubuntu laptop
Prefer an SSH tunnel instead of opening ports: `ssh -L 13000:127.0.0.1:13000 -L 18000:127.0.0.1:18000 user@mac`. Do not expose staging publicly and do not point real domains at it.

## 10. Rollback / removal (staging only)
```bash
for s in agent scheduler web api redis postgres; do launchctl bootout gui/$(id -u)/com.kvl.staging.$s; done
rm ~/Library/LaunchAgents/com.kvl.staging.*.plist
# Only after checking the path: rm -rf "$KVL_HOME"
```
This removes only KVL staging. Existing Mac services were never modified (step 2 backups remain).

## Not covered yet
Reverse proxy/TLS in front of staging, real nginx/PHP-FPM/MariaDB agent operations on the Mac's own stack, Razorpay/SMTP/Cloudflare/n8n tests, and production. Each needs its own review and approval.
