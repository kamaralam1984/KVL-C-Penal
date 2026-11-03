# KVL Hosting Panel — Architecture

```
                    Browser (desktop / phone, installable PWA)
                                   │  HTTPS
                    ┌──────────────▼───────────────┐
                    │  Next.js (App Router, TS)     │  UI only. Same-origin proxy: /api/v1/* → FastAPI
                    └──────────────┬───────────────┘
                                   │  cookies (HttpOnly) + CSRF header
                    ┌──────────────▼───────────────┐        ┌──────────────┐
                    │  FastAPI control plane        │◄──────►│  PostgreSQL  │  tenants, users, billing, jobs…
                    │  RBAC · tenant isolation      │        └──────────────┘
                    │  audit · rate limits          │◄──────►┌──────────────┐
                    └───┬──────────────┬───────────┘        │  Redis       │  rate limits, nonces, queues
          signed webhooks│              │ signed job protocol └──────────────┘
                ┌────────▼───┐   ┌──────▼──────────────┐
                │   n8n      │   │  KVL server agent    │  runs ON the hosting server, unprivileged,
                │ workflows  │   │  allow-listed ops    │  polls jobs, never opens a shell
                └────────────┘   └──────┬──────────────┘
                                        │ nginx · php-fpm · pm2 · mariadb · certbot · backups
                                  Hosting server (KVL iMac)
```

## Principles
- **Backend is authoritative.** The UI hides what you cannot do; the API enforces it (`require("resource:action")`, deny by default).
- **Tenant isolation in every query.** Tenant context comes from the authenticated membership, never from a client-supplied id. Another tenant's object returns 404.
- **No shell, ever.** Infrastructure changes are *jobs* with allow-listed operation names and strict Pydantic params. The agent re-validates and executes without a shell inside a sandbox directory, with timeouts and output caps.
- **Source of truth:** PostgreSQL via the control plane. n8n is only a workflow engine and acts through the signed API.
- **Honest integrations.** Razorpay, Cloudflare, ACME, SMTP and n8n report `not_configured` until credentials exist. Nothing is simulated as live.

## Components
| Area | Location |
|---|---|
| Web UI | `frontend/` |
| API | `backend/app` (`api/v1/routers`, `models`, `services`, `ops`) |
| Server agent | `agent/kvl_agent` |
| Migrations | `backend/alembic` |
| Runbooks | `docs/runbooks/` |
| Phase reports | `docs/phase-reports/` |
| Ops templates | `ops/` (systemd units, nginx vhost) |

## Data classification
Control-plane DB (platform + tenant metadata) is separate from customer website databases (provisioned on the hosting server by the agent). Control-plane credentials are never exposed to customers.
