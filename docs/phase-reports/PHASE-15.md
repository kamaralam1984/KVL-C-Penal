# KVL Hosting Panel — Phase 15 Report (n8n Automation)

## 1. Phase summary
n8n integration with FastAPI/PostgreSQL as the source of truth: signed inbound webhook, outbound event outbox with retry/backoff and
dead letters, 12-workflow catalogue with execution records, importable workflow JSON, scheduler-callable reminder emitters, runbook, admin screen.

## 2. Requirements completed
- Outbound (`services/automation.py`): generic `emit_event(db, type, payload, tenant_id=..., idempotency_key=...)` usable by every module; per-event Pydantic schemas (extra fields rejected), tenant-scoped events must carry matching `tenant_id`, platform events carry none; disabled workflows queue nothing; `dispatch_due()` signs each request (HMAC over `timestamp.body`), headers carry event id, type and correlation id; exponential backoff 30s,60s,120s... capped 1h; 6 attempts then `dead` + failed execution + `automation.workflow_failed` escalation event (no escalation loops); admin retry of dead letters only; per-run batch cap and endpoint rate limits.
- Inbound `POST /automation/inbound`: HMAC + 5-minute timestamp window (constant-time compare), mandatory `X-KVL-Idempotency-Key` stored in `automation_inbound` (replay returns `duplicate`, same key with a different body is 409), body size cap, rate limit, strict allow-list: `workflow.execution`, `notification.send` (fixed templates, recipient resolved from the tenant, never supplied by n8n), `tenant.activate` (refused with 409 unless a verified paid invoice backs a live subscription). Everything else (mark paid, suspend, create user...) is 422.
- Scheduler-callable: `emit_invoice_due_reminders`, `emit_renewal_reminders`, `emit_admin_approval_reminders`, `emit_operational_report`, `run_scheduler`; idempotent per invoice/subscription and threshold.
- Workflow catalogue with the 12 workflows, execution records, enable/disable, failure escalation.
- `automation/n8n/*.json`: 12 importable workflows (webhook trigger -> signature verification -> signed call to KVL or admin email -> signed success/failure report). No secrets: credentials referenced by name (`KVL SMTP`), shared secret read from n8n env.
- `docs/runbooks/n8n.md`: network restriction, auth requirements, never expose publicly, configuration, scheduling, import steps, rollback.

## 3. Routes and screens
`/admin/automation`: integration status (`not_configured` when no secret/base URL), workflow catalogue with last status, executions, event outbox, dead-letter failures with detail and safe retry, "Dispatch now" and "Emit reminders" actions; admin-only.

## 4. Components created
`frontend/src/components/automation/automation-page.tsx`.

## 5. Backend endpoints
`POST /automation/inbound` (signature-authenticated), `GET /automation/status`, `GET /automation/workflows`, `POST /automation/workflows/{key}/enabled`, `GET /automation/outbox[/{id}]`, `POST /automation/outbox/{id}/retry`, `GET /automation/executions`, `POST /automation/dispatch`, `POST /automation/scheduler/run`. Admin endpoints need `automation:read` / `automation:manage` (Super Admin only by default).

## 6. Database models and migrations
`app/models/automation.py`: `automation_outbox`, `automation_inbound`, `automation_workflow_state`, `automation_executions`. No migration written (lead consolidates). Additive config: `n8n_base_url`.

## 7. Integrations
n8n (needs a real instance: `N8N_WEBHOOK_SECRET`, `N8N_BASE_URL` on the backend; `KVL_N8N_WEBHOOK_SECRET`, `KVL_API_BASE_URL`, `KVL_ADMIN_ALERT_EMAIL`, `KVL_ALERT_FROM_EMAIL` and an SMTP credential in n8n). Emitters wired so far: `tenant.created` (reseller client creation), `billing.payment_verified`, `tenant.activated`, reminders. Event schemas exist for SSL expiry, backup result, server health and ticket update; those modules must call `emit_event()`.

## 8. Authorization and tenant-isolation review
n8n cannot bypass authorization: it can only call the allow-listed inbound events, each validated and tenant-scoped; activation is re-verified against paid invoices; recipients are derived from the tenant row. Outbound payloads for a tenant contain only that tenant's identifiers and recipient (tested with two tenants, including the delivered wire payloads). Admin endpoints are Super Admin only.

## 9. Security review
HMAC-SHA256 with constant-time comparison, replay window, idempotency table, schema validation, size caps, rate limiting, audit events on notifications/activation/retry/toggle, secrets never in responses, logs, payloads or workflow exports (a test scans the exports). Residual risk: Code nodes need `$env` access in n8n (documented trade-off, private network only).

## 10. Tests executed
`backend/tests/test_automation.py`: 18 tests. Covers invalid/stale/tampered/missing signatures, not-configured, replay and key-reuse, schema/allow-list, notification scoping, activation only after verified payment, emit validation, disabled workflows, dispatch signing, backoff schedule, dead letter + escalation (no loop), n8n-reported failure escalation once, reminder idempotency, no cross-tenant leakage, admin-only, retry via API, workflow export sanity. Combined run: see PHASE-14.

## 11. Lint/type/build results
See PHASE-14 section 11.

## 12. Resource impact
n8n itself is not started by this repo. Outbox rows are small; dispatch is batch-limited.

## 13. Known issues and limitations
- Not exercised against a real n8n: the workflow JSON follows n8n node schemas but has not been imported into a live instance; node typeVersions may need adjusting to the installed n8n version.
- No built-in scheduler process: cron/systemd (or an n8n schedule calling signed endpoints) must call `dispatch_due`, `run_scheduler` and `run_lifecycle`.
- SSL, backup, server-health and ticket events are defined but not emitted until those modules call `emit_event()`; welcome/verification emitters in the auth flow are not wired (auth is a core module).
- Execution status of a delivered event is `running` until n8n reports back; there is no timeout sweep marking silent runs failed.
- `emit_event` shares the caller's transaction; callers must commit.

## 14. Rollback instructions
Unset `N8N_*` settings (events stay queued, nothing depends on n8n), then remove `routers/automation.py`, `services/automation.py`, `models/automation.py`, the four tables, `automation/n8n/`, and restore the placeholder page.

## 15. Next phase prerequisites
Other modules call `emit_event()`; the lead adds the scheduler entry and the consolidated Alembic migration.

## 16. Owner approval status
Pending owner review.
