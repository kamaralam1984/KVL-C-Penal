# KVL Hosting Panel — Phase 04 Report (Restricted Python Server Agent)

## 1. Phase summary
A separate, unprivileged agent (`agent/kvl_agent`) and the control-plane side that enrolls, authenticates, monitors and dispatches
allow-listed jobs to it, plus the Servers UI. Everything was exercised against a local agent and the dev API only; **no real server or iMac was contacted and nothing was installed on the host.**

## 2. Requirements completed
- Agent: config loader (env + JSON file, secrets never logged/repr'd, credential files must be mode 0600), HMAC-signed client, one-time-token enrollment, heartbeat loop, job poll/claim/execute/report, strict allow-list via `registry.validate`, thread-based timeout wrapper, output truncation, secret redaction, concurrency limit, structured JSON logs, loopback health endpoint, SIGTERM graceful shutdown, metrics (psutil), service checks, update-status reporting (version only), operations `agent.collect_metrics` and `agent.service_status`, `run_local.py`, `agent/README.md`.
- Control plane: server registration (+ single-use enrollment token), list/detail, disable/enable, credential issue/rotate/revoke (secret encrypted at rest, shown once), job list/detail/cancel and manual enqueue of allow-listed ops via `enqueue_job`, agent API (`/agent/enroll|heartbeat|jobs/poll|jobs/{id}/result`), agent auth dependency (signature, +-300 s window, nonce replay table, revoked/disabled rejection, fail closed), job state machine with timeout sweeper, audit events for every operation, redacted and size-limited result summaries.
- Frontend: server registration with one-time token/command reveal, agent status/version/last heartbeat with distinct connected / stale / disconnected / unknown / disabled states, credential rotate/revoke/disable (confirm dialogs), operation history with status/operation filters and a detail sheet, cancel, run-operation form.

## 3. Routes and screens
`/admin/servers` (list + register) and `/admin/servers/[id]` (tabs: Agent, Monitoring, Operations, Credentials). Thin route files render `components/servers/*`.

## 4. Components
`frontend/src/components/servers/{servers-page,server-detail,credentials-panel,jobs-panel,availability-badge,enrollment-reveal,types}`; reused `ResourceTable`, `FormDialog`, `Sheet`, `ConfirmDialog`, `StatusPill`.

## 5. Backend endpoints (all under `/api/v1`)
| Method + path | Permission |
|---|---|
| `POST /servers` register (returns token once) | servers:write |
| `GET /servers`, `GET /servers/{id}`, `GET /servers/{id}/credentials` | servers:read |
| `POST /servers/{id}/enrollment-token` | servers:write |
| `POST /servers/{id}/disable`, `/enable`, `/credentials/rotate`, `/credentials/revoke` | servers:manage |
| `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/operations` | servers:read |
| `POST /servers/{id}/jobs`, `POST /jobs/{id}/cancel` | servers:write (destructive ops also need servers:manage) |
| `POST /agent/enroll` | enrollment token (IP rate-limited 10/10 min) |
| `POST /agent/heartbeat`, `/agent/jobs/poll`, `/agent/jobs/{id}/result` | HMAC agent signature |

All server endpoints additionally require a platform-scope account (`ctx.is_platform`): tenant and reseller users get 403 even where their role holds a similarly named permission. No change to `rbac.py` was needed (hosting_admin holds servers:read/write; super_admin holds manage).

## 6. Database models (new files; migration not generated, per brief)
`app/models/agents.py`: `agent_enrollment_tokens` (sha256 only), `agent_credentials` (Fernet-encrypted secret), `agent_nonces` (unique key_id+nonce). Existing `servers` / `agent_jobs` used unchanged (update-status is stored under `servers.os_info["update_status"]`).

## 7. Integrations
None external. The agent talks only to the control plane.

## 8. Authorization and tenant-isolation review
- Agent identity is the credential: every agent call recomputes the HMAC; unknown key, wrong secret, tampered body, revoked or disabled all return the same generic 401 (disabled after a valid signature: 403). A nonce is consumed only after the signature verifies. An agent can only see/complete its own server's jobs (others return 404).
- Staff: read/write/manage split as above; tenant users and customers get 403 (tested), unauthenticated 401.
- Response signing: poll/heartbeat responses carry `X-KVL-Response-Signature`; the agent drops job lists whose signature does not verify, so a network attacker or misrouted host cannot make it run work.

## 9. Security review
- No arbitrary command path: control-plane `OPERATIONS` and agent `registry` are allow-lists with `extra="forbid"` schemas; tests assert no op or param named shell/exec/cmd/etc. `ctx.run` accepts only bare, pre-declared binary names, runs with `shell=False`, rejects paths and metacharacter-bearing names, and treats metacharacters in arguments as inert text (tested).
- Secrets: encrypted at rest, shown once, excluded from audit detail (tested), results/errors redacted by key name and common `password=`/Bearer patterns on both agent and server, hard 16 KB result cap.
- TLS verification is on by default; plain http only for loopback or with an explicit insecure flag; disabling verification requires that flag too.
- Auth failures are logged by the access log; they are not individually audited (would let an attacker write unbounded audit rows). Enrollment is IP rate-limited.
- Python threads cannot be killed: a handler that ignores its timeout is abandoned (reported timed_out) and keeps running until it returns. Long-running work must use `ctx.run`, whose subprocesses are killed on timeout.

## 10. Tests executed
- `backend/tests/test_servers_agent.py` (24 tests): tenant 403, permission levels, registration/token single use/expiry, encrypted secret, invalid auth, tampered body, cross-path signature, replay, expired/future timestamp, revoke/rotate, disabled server, heartbeat validation, job lifecycle + audit, unknown op/invalid params, no shell op, cross-server isolation, redaction/size limit, cancel, full transition matrix, timeout sweeper, SKIP LOCKED concurrent claim, list filters/pagination, and a **real agent against the real API** (enroll, heartbeat, run three jobs under the concurrency limit, tampered response is not acted on, revoked credential stops polling).
- `agent/tests/test_agent.py` (23 tests): registry has no shell op, `ctx.run` rejection matrix, sandbox escape, unknown/invalid ops, timeout, truncation (summary and subprocess), subprocess kill, redaction, signature binding, response verification, TLS/config validation, credential file permissions, JSON log scrubbing, metrics missing-not-zero, services not_configured, health endpoint loopback/read-only, concurrency, unauthorized stops polling, graceful shutdown.
- Result counts are in the final section of `PHASE-05.md` (combined run).

## 11. Lint/type/build results
Backend: tests only (no linter configured in this phase). Frontend: eslint clean and no tsc errors in the new files; see PHASE-05.md section 13a for the combined counts (backend 47 passed, agent 131 passed).

## 12. Resource impact
Agent: one process, ~2 threads at rest plus one per running job (max 2 by default); a psutil collection per 30 s heartbeat (sub-second CPU), one HTTPS POST per 30 s heartbeat and one per 5 s poll (small JSON). Control plane: one INSERT (nonce) + one UPDATE per agent request, nonces purged by the maintenance task. No new daemons were started on this machine by this phase.

## 13. Known issues and limitations
- No self-update; version is reported only. No TLS client certificates. No privileged helper operations (none needed yet).
- Only `agent.ping`, `agent.collect_metrics`, `agent.service_status` exist in this phase's registry (other phases register more).
- A job cancelled while running is only marked cancelled; the agent is not interrupted (its result is rejected with 409).
- Queued jobs for a server that never connects stay queued (no queue TTL).
- Periodic maintenance must be scheduled by the operator (`python -m app.services.maintenance`, or `POST /monitoring/sweep`); no scheduler is wired into the API process.
- Alembic migrations for the new tables are not generated here (lead consolidates).
- Browser behaviour of the new screens was type/lint-checked only; not exercised in a running browser in this phase.

## 14. Rollback instructions
Remove `agent/kvl_agent/{config,client,executor,health,jsonlog,metrics,service,services,__main__}.py`, `agent/kvl_agent/operations/monitoring.py`, `agent/run_local.py`, backend `app/{models/agents,models/monitoring,services/agents,services/monitoring,services/maintenance}.py`, routers `servers|agent_api|monitoring.py`, the frontend `components/{servers,monitoring}` folders, restore the placeholder `admin/servers/page.tsx` and `admin/monitoring/page.tsx`, delete `admin/servers/[id]`, and drop the new tables (if migrated). No host changes to revert.

## 15. Next phase prerequisites
A reachable HTTPS API URL for the agent, a real enrollment on the iMac (owner action), and the systemd/user setup in `docs/agent-deployment.md`.

## 16. Owner approval status
Pending owner review.
