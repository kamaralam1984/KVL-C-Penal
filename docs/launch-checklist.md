# SaaS launch checklist

Tick every item with evidence (link to test run, screenshot or runbook). **Production launch needs explicit owner approval.**

## Product & UX
- [ ] All role areas reviewed on desktop, tablet and phone (bottom tab bar, drawers, no horizontal scroll)
- [ ] Light and dark themes reviewed; reduced-motion respected
- [ ] Empty/error/permission states exist for every module; no placeholder routes remain

## Security
- [ ] `SECRET_KEY`, `ENCRYPTION_KEY`, DB and Redis credentials generated per environment, stored outside Git
- [ ] `COOKIE_SECURE=true`, HTTPS + HSTS at the edge, CORS origins pinned
- [ ] Tenant-isolation and RBAC suites green; dependency scan reviewed (`docs/security/dependency-report.md`)
- [ ] Gap list reviewed (`docs/security/gap-list-and-remediation-plan.md`); no compliance certification is claimed
- [ ] Admin accounts have 2FA enabled; dev seed accounts removed

## Infrastructure
- [ ] Staging mirrors production topology; staging smoke + e2e pass
- [ ] Backups run, restore drill performed, RPO/RTO met (`docs/runbooks/backup-and-recovery.md`)
- [ ] Agent installed as unprivileged user; heartbeat visible; `agent.ping` job succeeds
- [ ] Monitoring alerts routed; on-call contact defined
- [ ] n8n reachable only from the control plane; workflow credentials are not in Git

## Billing
- [ ] Razorpay in **test mode** end-to-end (order → webhook → activation); webhook secret set; idempotency verified
- [ ] Owner approves switching to live keys; GST/invoice fields reviewed by an accountant

## Release
- [ ] Release tagged, migrations rehearsed on a copy of production data, rollback rehearsed
- [ ] Owner sign-off recorded
