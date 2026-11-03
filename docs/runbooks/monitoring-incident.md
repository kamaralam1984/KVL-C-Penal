# Monitoring and incident guide

## Signals
| Signal | Where | Healthy |
|---|---|---|
| API liveness / readiness | `/healthz`, `/readyz` | 200 |
| Agent heartbeat | Admin → Servers | `connected` (last heartbeat < 2× interval) |
| Alerts | Admin → Monitoring | none `critical` open |
| Job failures | Admin → Servers → Operations | < 2% failed / 1 h |
| Payment webhooks | Admin → Billing | all `processed` |
| Auth anomalies | Admin → Security | no refresh-token reuse / lockout surge |
| Backups | Admin → Backups | last verified < RPO |

## Severity
- **SEV1** data loss risk, auth bypass, tenant data exposure, all sites down → page owner immediately, freeze deploys.
- **SEV2** one server/service down, payments failing, backups failing > 24 h.
- **SEV3** degraded feature, single-tenant issue.

## First response
1. Acknowledge the alert (Monitoring → Alerts) so duplicates are suppressed.
2. Check `/readyz`, DB and Redis, then the agent heartbeat.
3. Review Security → Audit for the incident window; revoke suspect sessions/API keys.
4. Mitigate (disable a server, suspend a tenant, rotate a credential). Prefer reversible actions.
5. Communicate via an announcement if tenants are affected.
6. Write a post-incident note: timeline, impact, root cause, follow-ups.

## Common runbooks
- **Agent stale:** check the host service (`systemctl status kvl-agent`), network/TLS, credential revoked? Rotate credentials if in doubt.
- **Nginx config rejected:** the job result contains the `nginx -t` output; the previous config is untouched. Fix the template input and retry.
- **Certificate renewal failing:** check domain DNS and port 80 reachability; staging ACME first.
- **Webhook flood / forgery attempts:** signatures are verified before any processing; review rate-limit counters, rotate the webhook secret.
