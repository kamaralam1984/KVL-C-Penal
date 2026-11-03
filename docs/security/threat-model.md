# KVL Panel threat model (STRIDE-style, working document)

Scope: control-plane API (FastAPI/PostgreSQL/Redis), web UI (Next.js), server agent (separate component), outbound webhooks. Not a formal assessment. Reviewed by the builders only; no independent review has happened.

## Assets
Customer tenant data and website files; credentials (password hashes, TOTP secrets, API key hashes, webhook signing secrets, encryption keys); audit trail; ticket content (incl. internal notes and attachments); server agent trust channel; billing records.

## Trust boundaries
Internet -> reverse proxy -> API; browser <-> API (cookie session + CSRF) ; script <-> API (bearer key); API -> PostgreSQL/Redis; API -> external HTTPS webhook receivers (untrusted); API -> agent jobs (allow-listed operations only); tenant A <-> tenant B (logical isolation in one database).

## Threats and controls
| # | Threat | Control in the codebase | Test | Residual risk |
|---|--------|------------------------|------|---------------|
| 1 | Tenant A reads tenant B tickets/attachments/keys/webhooks (IDOR) | Every query scoped by tenant; foreign objects return 404 | test_support, test_apikeys, test_webhook_subscriptions | One shared DB: a missing `WHERE` in a future module breaks isolation. No row-level security yet. |
| 2 | Customer sees internal notes | Serializers take `staff` flag and assert; search/CSV/history/attachments filtered; webhooks/emails never include bodies | test_internal_notes_never_reach_customers | New endpoints must reuse the serializers. |
| 3 | Malicious upload (webshell, stored XSS, traversal, zip bomb) | Size cap, extension allow-list, magic-byte check, declared type must match, random storage names under a sandbox dir, path re-check, forced download with CSP sandbox, no archives/SVG/HTML | attachment tests | No antivirus scanning. Text types are checked by heuristics only. Whole upload is read into memory (capped) after the framework parses it. |
| 4 | API key theft or misuse | 256-bit secret, hash only, show once, expiry, scopes within owner permissions, effective perms recomputed per request, rate limit, revocation, rotation, keys cannot mint keys | test_apikeys | A stolen live key works until revoked/expired; no IP allow-listing. |
| 5 | Webhook SSRF into internal network or cloud metadata | https only, port allow-list, no userinfo, no IP literals (decimal/hex/octal rejected), DNS resolved and every address must be global, re-resolved at delivery and connect to the validated IP, no redirects, no proxy env | test_webhook_subscriptions (50+ cases) | The real transport is only unit-tested via source inspection; needs a staged end-to-end test with a TLS receiver. IPv6 literal hosts are blocked outright. |
| 6 | Forged or replayed webhook calls to receivers | HMAC-SHA256 over timestamp + body, 5 minute tolerance helper, rotation overlap | signing tests | Receivers must implement verification; documented. |
| 7 | Credential stuffing / brute force | Per-email and per-IP rate limits, lockout, OTP limits, failed-login surge incident detection with alert routing | test_auth, test_observability | Detection is poll-based (scan), not real time, and needs a scheduler. |
| 8 | Refresh-token theft | Rotation + family revocation on reuse (Phase 2), surfaced as a security event | test_observability | |
| 9 | Privilege escalation via role/permission APIs | Deny-by-default RBAC, owner-only changes, blocked attempts recorded as security events | test_privilege_escalation_attempt_recorded_from_live_request | Permission catalog edits by the owner are trusted. |
| 10 | Secret leakage via logs/metrics/readiness | Redaction filter on all log output, query strings never logged, `/readyz` and `/metrics` carry no secrets, `/metrics` protected | test_observability | Redaction is pattern-based; novel secret formats in free text could slip through. |
| 11 | CSRF / CORS abuse | Double-submit CSRF for cookie sessions, SameSite=Lax, explicit CORS allow-list | test_security_headers | |
| 12 | Clickjacking / MIME sniffing | X-Frame-Options, CSP frame-ancestors, nosniff | test_security_headers | Frontend (Next.js) headers are configured separately and not covered here. |
| 13 | Supply-chain vulnerability | `scripts/security/scan.sh` (pip-audit, npm audit), report in docs/security/dependency-report.md | manual | No automated CI gate or SBOM yet. |
| 14 | Repudiation of sensitive actions | Append-only audit events with actor, tenant, IP, request id | audit assertions | Audit table is not tamper-evident (no hash chain / external sink). |
| 15 | Denial of service | Rate limits, request size caps on attachments, pagination caps | rate-limit tests | No global request body cap at the proxy level in this repo; no WAF. |
| 16 | Support staff gaining infrastructure access | Support role has no infra permissions; asserted by test; staff scoped permissions view | test_support_role_has_no_infrastructure_privileges | The owner can still grant more via role editing (audited). |

## Out of scope / not assessed
Server agent hardening, TLS termination and host OS hardening, backups at rest, payment provider integration security, physical security, organisational controls.
