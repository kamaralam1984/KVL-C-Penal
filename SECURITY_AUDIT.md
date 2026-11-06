# SECURITY_AUDIT — risks, severity, remediation, retest evidence

Self-assessment by the builders. **No independent audit; no certification claimed.** The detailed control table is `docs/security/gap-list-and-remediation-plan.md`; threat model: `docs/security/threat-model.md`; dependency scan: `docs/security/dependency-report.md`.

## Brief §5 non-negotiable rules vs. repository
| Rule | Status | Evidence |
|---|---|---|
| No arbitrary shell from browser/API | Implemented by design: agent exposes fixed allow-listed ops with schema validation | agent `tests/test_*_ops.py` |
| Least-privilege host automation | Partial — systemd unit templates run an unprivileged agent; **not verified on a real host** | `ops/systemd/kvl-agent.service` |
| Tenant ownership on every operation | Implemented; foreign objects return 404 | `test_rbac_tenants`, `test_files`, `test_databases` |
| Path traversal / symlink / ZIP-slip / injection | Implemented in file ops and agent | `test_files`, agent `test_files_ops` |
| Nginx config validated before reload, rollback | Implemented with fake binaries only; **not tested against real nginx** | agent `test_nginx_ops` |
| Secrets not logged or committed | Log redaction implemented; `.gitignore` excludes `.env*` except `.env.example` | `test_observability`; `git ls-files | grep env` |
| Argon2id hashing, sessions, CSRF, rate limits, audit | Implemented | `test_auth`, `test_security_headers` |
| Privileged ops require authorization | Implemented via RBAC permissions | `test_rbac_tenants` |

## Open risks
| # | Risk | Severity | Remediation |
|---|---|---|---|
| 1 | Real-host behaviour (nginx, FPM, MariaDB, permissions) never exercised | High | Staging test after owner approval |
| 2 | Staff MFA not enforced | High | Enforce in settings/policy |
| 3 | No CI gate or scheduled dependency/SAST scans | High | Add CI running `scripts/security/scan.sh` |
| 4 | Key rotation (`SECRET_KEY`, `ENCRYPTION_KEY`) undocumented | High | Write procedure |
| 5 | Backup restore not demonstrated on a real target | High | Restore drill in staging |
| 6 | Alert delivery (mail/pager) untested against real services | High | Staging test |
| 7 | Audit log lacks tamper evidence | Medium | Hash chain / external sink |
| 8 | Dev-only credentials exist in local DB (`backend/.dev-credentials`, git-ignored) | Medium | Remove seed accounts before any non-dev deploy |

Retest evidence for each item will be added as it is remediated; none is retested yet.
