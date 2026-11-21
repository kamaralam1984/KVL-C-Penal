# KVL Panel — cPanel-parity feature pack (2026-11-22)

Status: **code complete and tested on branch `feature/kvl-hosting-customer-vps`. NOT deployed.**
Deploying to the live iMac needs explicit owner approval (see "Deploy steps").

## Features

| # | Feature | Where (backend / agent / UI) | Tests |
|---|---|---|---|
| 1 | SMTP outgoing mail (retry, backoff, redaction; admin status + test) | services/mail.py, scheduler `mail`, settings `/settings/mail`; Settings → Email | test_mail.py (12) |
| 2 | File editor (open / edit / save, Ctrl+S, binary and >1 MB blocked) | existing overwrite upload; files/editor-dialog.tsx | test_files.py editor e2e |
| 3 | Cron jobs (HTTPS GET to own verified domain, ≤4/hour, SSRF-safe, pauses after 10 failures and emails the customer) | models/cron.py, services/cron.py, routers/cron.py, scheduler `cron`; Cron jobs page | test_cron.py (20) |
| 4 | PHP version change + per-site php.ini values | site options `php` + PATCH runtime_version; Site tools → PHP | test_site_options.py, agent nginx tests |
| 5 | Website logs viewer (access/error, filter, paging, redacted) | op `nginx.site_logs`; `/websites/{id}/logs` | test_site_logs.py, agent |
| 6 | Redirects (exact / starts-with, 301/302/307/308) | site options; Site tools | as #4 |
| 7 | Password-protected folders ({SSHA} hashes only, content-addressed htpasswd) | site options; Site tools | as #4 |
| 8 | IP blocker (map + geo; uses CF-Connecting-IP only from the local tunnel) | site options; Site tools | as #4 + real `nginx -t` |
| 9 | Hotlink protection | site options; Site tools | as #4 |
| 10 | Visitor stats (requests/day, weekday×hour heatmap, top pages/referrers; aggregates only, no IPs) | op `nginx.site_stats`; Website → Visitors | test_site_logs.py, agent |
| 11 | Database browser (tables, structure, rows, filter, sort; READ ONLY, 5 s timeout) | op `db.browse_*`; Database → Browse | agent integration on real MariaDB + PostgreSQL; test_db_browser.py |
| 12 | WordPress toolkit (versions, plugin/theme updates from wordpress.org only, rollback, maintenance) — never executes customer PHP | ops `wp.*`, services/wporg.py; Application → WordPress | test_wordpress_ops.py, test_wordpress.py |
| 13 | Malware scan (webshell heuristics + ClamAV if installed; read-only) | op `security.scan`; Files → Security | test_security_scan.py, files e2e |
| 15 | Business email setup (Google Workspace, Zoho IN/US, Microsoft 365 records; SPF merge) | routers/email_setup.py; Email page | test_email_setup.py |
| 16 | Admin dashboard "operations centre" (uptime/health rings, request path, AI shortcut) | dashboard/ops-hero.tsx, `.glow-card` | tsc/eslint |
| 17 | Sign in with Google / Microsoft (OIDC + PKCE, JWKS-verified id_token, 2FA still required, Microsoft link-first) | services/sso.py, routers/auth_sso.py; login + Account → Security | test_sso.py (12) |
| 18 | Support AI agent (KB-grounded answer → human ticket handoff) | services/ai.py, `/support/ai/answer`; Support → Ask AI | test_ai.py |
| 19 | Admin AI assistant (read-only, RBAC-scoped tools) + MCP server (`POST /api/v1/mcp`, API-key auth) | services/ai.py, routers/ai.py; Admin → AI assistant | test_ai.py |

Not built, by decision:
- **#14 SFTP accounts:** needs SSH on and macOS users per customer. This reverses the Phase 5 hardening, so it needs an owner decision.
- **Browser terminal:** forbidden by the project rule "never expose unrestricted shell execution".

## Verification (2026-11-22)

- Agent suite: **321 passed**. This includes `nginx -t` on the real nginx binary for the static, php and proxy runtimes with every site option enabled, and DB-browser integration on throwaway MariaDB and PostgreSQL servers.
- Backend suite: **624 passed**. It runs on a throwaway PostgreSQL (:55440) and Redis (:16399), never the live :54329 / :6379.
- Alembic: single head `8d4f3b0a5e26`. upgrade → downgrade → upgrade was run on a scratch DB, and the migrated schema equals the models (0 diffs).
- Frontend: `tsc --noEmit` and `eslint src` are clean. `next build` was NOT run in place, because it would overwrite the live `.next`. Build in a copy (the release script pattern) before deploying.

## Found and fixed during this work

- The live nginx has **no realip module**, so a naive IP blocker would have broken nginx. Replaced it with map+geo.
- The stats response field `status` collided with the job status, so it was renamed to `status_codes`.
- Customers saw platform-only AI tools, because they hold `monitoring:read` for their own sites. Those tools are now hidden by scope.
- `backend/.env` (production settings) leaks into tests. Run tests with `COOKIE_SECURE=false CORS_ORIGINS=http://localhost:3000` and a throwaway DB, as above.

## Deploy steps (owner approval required)

1. Take a full backup with `sudo /opt/local/sbin/kvl-unified-backup.sh full`.
2. Install the AI dependency into a build venv first: `pip install anthropic`. It is optional; AI shows "not configured" without it.
3. Run the migrations against live PostgreSQL: `alembic upgrade head` (5a1c0e7d2b91 → 6b2d1f8e3c04 → 7c3e2a9d4f15 → 8d4f3b0a5e26). Each one only adds tables or columns.
4. Set the new backend env vars as needed. Every integration stays "not configured" until it is set:
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY`
   - `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`
   - `MICROSOFT_OAUTH_CLIENT_ID` / `_SECRET` / `_TENANT`. The redirect URI is `https://panel.kvlbusinesssolutions.com/api/v1/auth/sso/{google|microsoft}/callback`.
   - `ANTHROPIC_API_KEY`, and optionally `AI_MODEL` (default `claude-opus-5-5`, server-side refusal fallback on) and `AI_EFFORT`.
5. Build the frontend in a staging copy, swap it in, then restart the backend, frontend and agent through their LaunchDaemons.
6. Smoke test:
   - login
   - Settings → Email → Send test
   - one website: Site tools save → Validate → Activate → Reload
   - Files → Security scan
   - AI status

Rollback: restore the previous build and the backed-up `.env`, then run `alembic downgrade 041f68a04c4d` (it only drops the new tables and columns).
