# API_DOCUMENTATION — actual endpoints

The API is FastAPI, so the live OpenAPI document is authoritative: **328 paths / 380 operations** (counted from `app.openapi()` on 2026-11-06).

- Interactive docs are served **only when `ENV` is dev/test**: `/api/docs` (Swagger) and `/api/openapi.json` (disabled in staging/production). Health: `/healthz`, `/readyz` (DB + Redis).
- Overview and conventions: `docs/api/overview.md`.
- Routers live in `backend/app/api/v1/routers/`: agent_api, announcements, apikeys, apps, auth, automation, backups, billing, cloudflare, databases, dev, developer, dns, domains, files, kb, monitoring, rbac, resellers, resources, security, servers, settings, ssl, staff, stats, support, tenants, webhooks, websites.
- Auth: cookie sessions (CSRF-protected) for the UI; scoped API keys for integrations (a key never exceeds its creator's permissions). Agent endpoints use per-agent credentials with nonces.
- The `dev` router is inert (404) unless `ENV` is dev/test.

Export a static copy: `cd backend && .venv/bin/python -c "import json,app.main as m;print(json.dumps(m.app.openapi()))" > openapi.json` (needs `DATABASE_URL` and `REDIS_URL` env set).
