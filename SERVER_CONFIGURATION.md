# SERVER_CONFIGURATION — Ubuntu vs macOS differences (no secrets)

Do not assume Ubuntu commands work unchanged on macOS. The Mac was inspected read-only on 2026-11-06 (macOS 13.7.8 Ventura, Intel iMac Pro, 4 cores, 8 GB RAM, MacPorts, no Homebrew).

| Topic | Ubuntu dev laptop (verified) | Mac server |
|---|---|---|
| Service manager | systemd (`ops/systemd/*`) | launchd (system daemons for nginx/PHP/MariaDB/Postgres) and two PM2 daemons; systemd units do not apply, use `ops/launchd/*` |
| Postgres | 18, private cluster port 54329, socket `~/.kvl-dev/sock` | existing PostgreSQL 16 cluster (MacPorts) running; staging uses its binaries for a new cluster on 54330 |
| Redis | 127.0.0.1:6379, panel uses db 5 | Redis 7.4 on 6379 already in use; staging runs its own on 63790 |
| Web | `next start` :3000, API :8000 | :3000 and :8000 are **already used** by unknown processes; staging uses :13000 and :18000. nginx 1.30, PHP 8.3 FPM, MariaDB 10.11 host live sites (cloudflared tunnel) — preserve |
| Paths | `/usr/lib/postgresql/18/bin`, `/var/run/postgresql` (not writable by user) | MacPorts under `/opt/local` (`etc/nginx`, `etc/php83`, `lib/postgresql16/bin`, `www`); Node via nvm (default 18, too old for Next 16) |
| Agent | unprivileged user, fakes in tests | real nginx reload/permissions unverified |

Rules: keep OS-specific behaviour behind adapters or documented scripts; inspect exact command, affected paths, service impact and rollback before running anything on the Mac; Mac is not automatically the production host — a separate Linux production plan is required.

Env settings: see `backend/.env.example` (`ENV`, `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ENCRYPTION_KEY`, `COOKIE_SECURE`, `CORS_ORIGINS`). Real values live only in each host's `.env`, never in Git.
