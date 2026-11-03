# Security gap list and remediation plan

**No certification is claimed.** KVL Panel has not been assessed by any independent party and is **not** SOC 2, ISO/IEC 27001, PCI DSS, HIPAA or GDPR certified or attested. The table maps engineering controls to the kind of control families those frameworks ask about so the gaps are visible. It is a self-assessment by the builders, not an audit result. Do not use it in marketing or customer questionnaires as evidence of compliance.

Legend: **Implemented** = exists in code with tests. **Partial** = exists with known holes. **Gap** = not done.

| Control family | State | Evidence / what exists | Gap and remediation (owner, priority) |
|---|---|---|---|
| Access control: authentication | Partial | Argon2id, TOTP, OTP, lockout, rate limits, session rotation/revocation | Enforce MFA for all platform staff; password breach check; SSO. (Product, High) |
| Access control: authorisation | Implemented | Deny-by-default RBAC, tenant scoping, 404 on foreign objects, scoped API keys | Row-level security in PostgreSQL as a second layer; automated IDOR test generator across all routers. (Backend, High) |
| Least privilege | Partial | Support role has no infra permissions (tested); keys never exceed creator | Periodic access review workflow; just-in-time elevation for infra roles. (Ops, Medium) |
| Audit logging | Partial | Append-only `audit_events` for sensitive writes, viewer with filters | Tamper evidence (hash chain or WORM sink); retention policy; export to SIEM. (Backend/Ops, High) |
| Logging and monitoring | Partial | JSON logs with request ids and redaction, Prometheus metrics, readiness, security event detection and alert routing | No alert delivery tested against real mail/pager; detection is a polled scan needing a scheduler; no log shipping/retention. (Ops, High) |
| Incident response | Gap | Incident records with acknowledge/resolve exist | No written runbook, on-call rota, severity definitions, post-incident review process, breach-notification procedure. (Ops/Legal, High) |
| Vulnerability management | Partial | `scan.sh`, dependency report | No CI gate, no scheduled scans, no SAST/container scanning, no patch SLA. (Eng, High) |
| Secure development | Partial | Input validation, parameterised queries, tests for negative paths | No mandatory code review policy, no threat-model review cadence, no dependency pinning policy. (Eng, Medium) |
| Cryptography and key management | Partial | Fernet for secrets at rest, HMAC signing, hashed API keys, TLS expected at proxy | Key rotation procedure for `ENCRYPTION_KEY`/`SECRET_KEY`; KMS/HSM; documented TLS config. (Ops, High) |
| Data protection and privacy | Gap | Tenant data deletion lifecycle exists (Phase 3 retention) | No data classification, DPA/privacy notice, subject-access/deletion tooling, data-residency statement. (Legal/Product, High) |
| Backup and recovery | Partial | Backup features (Phase 12) | Restore tests are not scheduled or evidenced; no RPO/RTO targets. (Ops, High) |
| Availability and capacity | Gap | Health/readiness endpoints | No SLOs, load tests, redundancy design. (Ops, Medium) |
| Network and infrastructure security | Gap | App-level headers/CORS only | Hardening, firewalling, WAF, segmentation, host patching are deployment concerns not covered here. (Ops, High) |
| Vendor management | Gap | | No supplier inventory/assessment (payment, DNS, email). (Ops/Legal, Medium) |
| Change management | Gap | Migrations consolidated by lead | No formal change approval, staging promotion gate or rollback evidence. (Eng, Medium) |
| Personnel / training / policies | Gap | | No security policy set, onboarding/offboarding checklists, awareness training, background checks. (Management, High) |
| Business continuity | Gap | | No BCP/DR plan or exercises. (Management, Medium) |
| Physical security | Gap | n/a to code | Depends on hosting provider; collect their attestations. (Ops, Low) |

## Prioritised remediation plan
1. **0-30 days**: scheduler for `run_sla_escalation`, `deliver_due`, `scan_for_incidents`; real alert delivery test; mandatory MFA for staff; set `METRICS_TOKEN`/`ENCRYPTION_KEY`; CI job running `scripts/security/scan.sh`; incident runbook v1.
2. **30-90 days**: PostgreSQL row-level security pilot on tickets/keys/webhooks; audit tamper evidence and log shipping; restore-test schedule with evidence; key rotation procedure; staged end-to-end test of the webhook transport against a TLS receiver; antivirus scanning for attachments.
3. **90-180 days**: policy set, access reviews, vendor inventory, DR plan, load tests and SLOs; then consider a readiness assessment by an independent auditor.

Certification (SOC 2 Type I/II, ISO 27001) requires an independent auditor, an operating ISMS and months of evidence. Nothing here substitutes for that.
