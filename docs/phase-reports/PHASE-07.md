# KVL Hosting Panel — Phase 07 Report: Application and Runtime Manager

## 1. Phase summary
Applications (PHP-FPM, WordPress template, Node.js via PM2, Python with per-release venv + gunicorn via PM2) with release directories, atomic symlink switch, pre-switch health check, previous-release retention, rollback, encrypted env vars with audited reveal, job-based paginated redacted logs, usage and quotas.

## 2. Requirements completed
- Runtime catalog from the server: agent op `apps.detect_runtimes` -> table `server_runtimes` (ingested lazily from the finished job). Versions are validated against a supported list and, once the server has reported, against what is installed. Not reported -> honest `not_reported`/`not_configured` state.
- Git config: https or ssh URLs on an allow-list (github.com, gitlab.com, bitbucket.org, codeberg.org); no credentials, ports, file://, local paths, option-like values; branch name grammar (no `..`, `@{`, leading `-`).
- Deploy (`apps.deploy`): git clone (fixed argv, `--`), `.git` removed, npm `--ignore-scripts` / venv + `pip --python` / composer, env into a 0600 `.env` + PM2 ecosystem file, canary process on its own port health-checked BEFORE the symlink switch, atomic `os.replace` switch, `pm2 startOrReload`, automatic revert if the process manager fails, failed releases are discarded and the previous release stays live, pruning keeps current + previous. PHP/WordPress health = index/`wp-settings.php` present. WordPress template: official mirror default repo, generated `wp-config.php` from validated `WP_DB_*` env values + random salts.
- Rollback (`apps.rollback`) only to a release with a succeeded deployment on record; re-switches atomically.
- Process control (`apps.process` start/stop/restart/status) for Node/Python only; PHP/WordPress -> 409.
- Env vars: Fernet-encrypted at rest, masked in every API response, reveal only via `POST /apps/{id}/env/{name}/reveal` (`apps:manage`, audited without the value), reserved names (PATH, LD_*, NODE_OPTIONS, PYTHONPATH, GIT_*, KVL_* ...) refused.
- Logs: `POST /apps/{id}/logs` enqueues `apps.logs`, `GET /apps/{id}/logs/{job_id}` returns redacted lines (agent redacts, API redacts again incl. the app's own env values). Redaction function: `backend/app/services/redact.py` (+ agent copy `kvl_agent/redact.py`), unit-tested.
- Usage (`apps.usage`) and quota `apps`; ports allocated per server (20000+, app + canary).
- No server: app is `provisioning_blocked`; Deploy returns 409 `no_server` and works once a server exists.

## 3. Routes and screens
`/hosting/applications`, `/customer/applications` (no `admin/applications` route exists): inventory, runtime wizard, drawer with Overview/Deployments/Environment/Process/Logs.

## 4. Components created
`frontend/src/components/applications/*`.

## 5. Backend endpoints (`/api/v1/apps`)
GET list, GET/POST/PATCH/DELETE `?confirm=<slug>`, GET `/runtimes`, POST `/runtimes/refresh` (platform, `apps:manage`), POST `/{id}/deploy`, GET `/{id}/deployments`, POST `/{id}/rollback`, POST `/{id}/process`, POST `/{id}/usage/refresh`, GET `/{id}/usage`, POST `/{id}/logs`, GET `/{id}/logs/{job_id}`, GET/PUT `/{id}/env`, DELETE `/{id}/env/{name}`, POST `/{id}/env/{name}/reveal`, GET `/{id}/activity`.
Permissions: read=`apps:read`; create/update/deploy/env edit=`apps:write`; rollback/process/reveal/delete/runtime refresh=`apps:manage`.
**RBAC edit (additive):** the `customer` role gained `apps:manage` in `backend/app/core/rbac.py` (needed so customers can control their own processes and reveal their own env values). The seed must be re-run on existing databases to grant it.

## 6. Database models and migrations
`server_runtimes`, `applications`, `app_deployments`, `app_env_vars` (+ shared `resource_events`) in `app/models/hosting.py`. No migration generated.

## 7. Integrations
git, npm, node, pm2, composer, python3.x, php-fpm via agent only. Nothing installed or run in the API process.

## 8. Authorization and tenant-isolation review
All routes use `get_visible` (other tenant -> 404, including env, logs jobs, deployments). Support and reseller roles get 403 on process control, reveal and deploy. A log job id from another app/tenant returns 404.

## 9. Security review
Secrets: env values travel to the agent in the job params (needed to write `.env`), and are scrubbed from `agent_jobs.params` the next time the API syncs the finished job (`scrub_job_params`); there is a window while the job is queued/running and until the first read after completion. Results/errors never contain env values (agent and API redact). Customer code runs only as the app process under the agent's unprivileged user; npm install scripts are disabled; no shell is used anywhere.

## 10. Tests executed
Backend `tests/test_apps.py` (17): validation (runtime/version/entrypoint/repo/branch/health), catalog ingestion, no-server retry, deploy success + health-failure keeps previous release + rollback rules, not_configured, unauthorized process control (other tenant, support, reseller, anonymous, invalid action), php has no process, env encryption/mask/reveal audit/reserved names, isolation across all routes, logs pagination + redaction + job scoping, job result redaction, quota, usage, port uniqueness, website link. Agent `tests/test_apps_ops.py` (50) with a scripted fake context: URL/branch/env validation, node/python/php/wordpress deploys, canary health failure, build failure, process-manager failure revert, rollback, prune, not_configured, runtime mismatch, process actions, log pagination/redaction, detect/usage/remove, dry-run, redaction function.
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
- Real git/npm/pm2/php-fpm were never executed (tests use a scripted context). First real deployment needs those installed on the server.
- SSH repositories need a deploy key configured on the server; none is managed by the panel (private HTTPS repos with tokens are rejected on purpose).
- PHP/WordPress releases live in the agent sandbox (`<data_dir>/apps/<app>/current`); wiring that path into the site's `/var/www/kvl/...` document root is not automated (the sandbox rule forbids writing there). Node/Python apps are reached by Nginx proxy (`runtime_type=proxy`) after linking a website and re-provisioning.
- Node: only the server's active Node major is usable (no nvm switching). PM2 runs under the agent user.
- `apps.remove` leaves nothing behind in the sandbox but `DELETE` does not delete the linked website/domain.
- Resource charts: only a point-in-time usage snapshot (disk, memory, CPU, restarts, uptime), no time series.
- PM2 log files are the source for logs; PHP app logs are not exposed here.

## 15. Next phase prerequisites
The agent daemon must supply `ctx.run` env for pm2 (HOME) as the ops do not rely on the daemon's environment; backups engine may pick up `apps/<app>/shared`.
