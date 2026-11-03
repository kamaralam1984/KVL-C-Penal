# Runbook: n8n automation

n8n is the **workflow engine only**. FastAPI/PostgreSQL remain the source of truth for tenants, users, billing,
permissions and hosting resources. A workflow can only *ask* the backend to do something through the signed API;
the backend authorises, validates and audits every request.

## 1. Network and authentication requirements (read before installing)

* Run n8n on the private network or loopback (for example `127.0.0.1:5678` behind the same host firewall). **Do not expose
  n8n to the public internet** without a separate security design (VPN/zero-trust proxy, SSO, IP allow-list, TLS).
* Protect the n8n editor/UI with authentication (n8n user management with strong owner password + 2FA, or SSO). Disable
  public sign-up, set `N8N_SECURE_COOKIE=true` behind TLS, and restrict the UI port with the firewall.
* Webhook endpoints (`/webhook/kvl-*`) are called only by the KVL backend. Firewall them to the backend host. They verify the HMAC signature and
  timestamp in the first workflow step and throw (no action) on any mismatch.
* The KVL backend endpoint `POST /api/v1/automation/inbound` is the only door back in. It requires an HMAC-SHA256
  signature over `"<unix timestamp>." + raw body`, a timestamp within 5 minutes, and an `X-KVL-Idempotency-Key`.
* Never put secrets in workflow JSON or Git. The exports in `automation/n8n/` reference credentials **by name** and
  read the shared secret from the n8n process environment.

## 2. Configuration

Backend (`backend/.env`, never committed):

| Variable | Meaning |
|---|---|
| `N8N_WEBHOOK_SECRET` | Shared HMAC secret (generate with `openssl rand -hex 32`). Empty = integration reports `not_configured`. |
| `N8N_BASE_URL` | Private base URL of n8n, e.g. `http://127.0.0.1:5678`. Empty = outbound disabled, events stay queued. |

n8n process environment (same secret value):

| Variable | Meaning |
|---|---|
| `KVL_N8N_WEBHOOK_SECRET` | Same value as `N8N_WEBHOOK_SECRET`. |
| `KVL_API_BASE_URL` | Backend base URL reachable from n8n (private), e.g. `http://127.0.0.1:8000`. |
| `KVL_ADMIN_ALERT_EMAIL`, `KVL_ALERT_FROM_EMAIL` | Admin alert recipient/sender used by platform-level workflows. |
| `NODE_FUNCTION_ALLOW_BUILTIN=crypto` | Lets the Code nodes use Node's `crypto` for HMAC. |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` | Required for `$env` in Code nodes. Acceptable only because n8n is private and only admins edit workflows; if you cannot accept this, store the secret as an n8n credential instead and change the two Code-node lines. |

n8n credentials to create (names must match):

* `KVL SMTP` (SMTP account used only by admin-alert workflows).

## 3. Event flow

Outbound (backend to n8n): modules call `app.services.automation.emit_event(db, event_type, payload, tenant_id=..., idempotency_key=...)`.
Payloads are validated against a per-event schema (extra fields rejected, tenant-scoped events must carry their own `tenant_id`) and
stored in `automation_outbox`. A worker/scheduler calls `dispatch_due(db)` (or an admin presses *Dispatch now*): each event is
POSTed to `/webhook/kvl-<workflow-key>` with headers `X-KVL-Signature`, `X-KVL-Timestamp`, `X-KVL-Event-Id`, `X-KVL-Event-Type`,
`X-KVL-Correlation-Id`. Failures retry with exponential backoff (30s, 60s, 120s ... capped at 1h); after 6 attempts the event is
**dead-lettered**, a failed execution is recorded and an `automation.workflow_failed` event is raised for the escalation workflow.
An admin can retry only dead-lettered events (same event id and correlation id, so n8n can deduplicate).

Inbound (n8n to backend), allow-list only:

| event_type | Effect | Safeguards |
|---|---|---|
| `workflow.execution` | Records succeeded/failed run; failures raise an escalation event once | workflow key must be in the catalog |
| `notification.send` | Queues an email to **the target tenant's own contact address** from a fixed template list | recipient is never accepted from n8n |
| `tenant.activate` | Re-evaluates activation | refused (409) unless a verified paid invoice backs a live subscription |

Anything else (marking invoices paid, suspending, creating users, changing roles) is rejected with 422.

## 4. Scheduling

n8n does not own schedules for business data. Run these from cron/systemd timers (or an n8n Schedule trigger calling a signed endpoint):

* `POST /api/v1/automation/scheduler/run` (admin session) or `services.automation.run_scheduler(db)`: invoice-due, renewal and admin-approval reminders (idempotent per threshold).
* `services.billing.run_lifecycle(db)` (also `POST /api/v1/subscriptions/lifecycle/run`): renewal invoices, past_due, grace expiry, suspension.
* `services.automation.dispatch_due(db)` every minute (also `POST /api/v1/automation/dispatch`).
* `services.automation.emit_operational_report(db, "daily" | "weekly")`.

SSL-expiry, backup, server-health and ticket events are emitted by their own modules via `emit_event()`; the event schemas exist already.

## 5. Importing the workflows

1. Create the credentials and environment variables above.
2. In n8n: *Workflows -> Import from file* for each file in `automation/n8n/` (12 files, one per catalog entry).
3. Open each workflow once, confirm the credential names resolve, then activate it. They ship inactive.
4. In the KVL admin Automation page the catalog shows each workflow's last status; send a test event by creating a tenant.

## 6. Failure handling and rollback

* Admin Automation page: failed executions, dead-lettered events with the last error, **Retry** for dead letters.
* Disable a workflow in the KVL catalog (stops new events being queued for it) without touching n8n.
* To disconnect n8n entirely: unset `N8N_BASE_URL`/`N8N_WEBHOOK_SECRET`. Events stay queued (outbound reports `not_configured`); no business
  process depends on n8n, because activation and payment verification happen in the backend.
* Rotate the secret by changing both environments and restarting; in-flight signed requests older than 5 minutes are rejected anyway.
