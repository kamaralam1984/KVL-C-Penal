# SERVER_CONFIGURATION — Ubuntu vs macOS differences (no secrets)

Do not assume Ubuntu commands work unchanged on macOS (Big Sur). The Mac has not been inspected; the right-hand column is to be **filled in from read-only inspection** after owner approval.

| Topic | Ubuntu dev laptop (verified) | Mac server |
|---|---|---|
| Service manager | systemd (`ops/systemd/*`) | launchd/PM2 — unverified; systemd units do not apply |
| Postgres | 18, private cluster port 54329, socket `~/.kvl-dev/sock` | unknown |
| Redis | 127.0.0.1:6379, panel uses db 5 | existing Redis must be protected; use ACL/separate db |
| Web | `next start` :3000, API :8000 | existing nginx/PHP-FPM/MariaDB host live sites — preserve |
| Paths | `/usr/lib/postgresql/18/bin`, `/var/run/postgresql` (not writable by user) | Homebrew prefixes differ |
| Agent | unprivileged user, fakes in tests | real nginx reload/permissions unverified |

Rules: keep OS-specific behaviour behind adapters or documented scripts; inspect exact command, affected paths, service impact and rollback before running anything on the Mac; Mac is not automatically the production host — a separate Linux production plan is required.

Env settings: see `backend/.env.example` (`ENV`, `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, `COOKIE_SECURE`, `CORS_ORIGINS`). Real values live only in each host's `.env`, never in Git.
