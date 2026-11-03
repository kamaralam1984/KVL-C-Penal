# KVL server agent

A small, unprivileged Python service that runs on a hosting server and talks **outbound only** to the KVL control plane.
It has no inbound command port and no shell: the only things it can do are the operations registered in
`kvl_agent/operations/` (see `registry.py`).

## What it does
- Enrols once with a single-use token, receives `key_id` + `secret` (stored `0600` in the data dir; never logged).
- Sends a signed heartbeat every 30 s with version, uptime, CPU, load, memory, swap, disk per mount, network throughput
  and interface state, plus nginx / php-fpm / mariadb / redis / pm2 status. Not-installed services are
  `not_configured`; a measurement that cannot be taken is omitted (null), never reported as 0.
- Polls for queued jobs, validates each against its own allow-list and Pydantic schema, runs it with a timeout and
  output limit, and reports a redacted, size-limited summary.
- Exposes `GET http://127.0.0.1:9710/healthz` (loopback only, read-only, no secrets).
- Handles SIGTERM: stops polling, waits for running jobs (up to `shutdown_grace`), exits.

## Security model
| Property | Mechanism |
|---|---|
| Request authentication | HMAC-SHA256 over `METHOD \n path \n timestamp \n nonce \n sha256(body)` with the credential secret |
| Replay protection | control plane rejects timestamps outside +-300 s and any reused nonce |
| Server authentication | HTTPS with certificate verification on by default; job lists additionally carry an HMAC response signature that the agent verifies (unverifiable responses are dropped: fail closed) |
| No arbitrary commands | `registry.validate` allow-list; `ctx.run` only accepts bare, pre-declared binary names, no shell, no metacharacter interpretation |
| Revocation | rotating/revoking/disabling in the panel makes the next request fail with 401/403; the agent then stops polling |
| Least privilege | run as `kvl-agent` (no login shell, no sudo); filesystem writes confined to `data_dir` via `ctx.path` |

Plain `http://` is accepted only for loopback or with `allow_insecure_http` (development). TLS verification cannot be disabled
unless `allow_insecure_http` is also set.

## Configuration
Environment variables (`KVL_AGENT_*`) and/or a JSON file named by `KVL_AGENT_CONFIG` (must be mode `0600`):

| Key | Default | Notes |
|---|---|---|
| `api_url` | none | e.g. `https://panel.example.com/api/v1` (include the `/api/v1` prefix; it is part of the signed path) |
| `data_dir` | `/var/lib/kvl-agent` | credentials and sandbox |
| `key_id` / `secret` / `secret_file` | from `credentials.json` | normally written by `enroll` |
| `heartbeat_interval` / `poll_interval` | 30 / 5 s | |
| `max_concurrency` | 2 | 1..8 |
| `max_output` | 16384 | bytes kept from op output |
| `health_port` | 9710 | binds 127.0.0.1 |
| `verify_tls`, `ca_bundle`, `allow_insecure_http` | true, none, false | |

## Commands
```
python -m kvl_agent enroll --api-url https://panel.example.com/api/v1 --token kvl_enr_...
python -m kvl_agent run
python -m kvl_agent check      # print local metrics and service status, contact nothing
```
Local development against the dev API: `python run_local.py --api-url http://127.0.0.1:8000/api/v1 --token <token>`
(state in `agent/.local-agent`, health port 9710). Do not point it at a real server.

## Tests
`cd agent && ../backend/.venv/bin/python -m pytest tests` (the control-plane integration test lives in `backend/tests/test_servers_agent.py`).

## Not implemented (by design, for now)
Self-update (the agent only *reports* its version and `update_status: not_configured`), privileged helpers, TLS client certificates.
Deployment (systemd unit, user, directories): see `docs/agent-deployment.md`. Nothing here installs anything.
