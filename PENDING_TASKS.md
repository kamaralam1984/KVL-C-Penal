# PENDING_TASKS — missing, partial, blocked, untested

## Blocked on owner approval (nothing below may start without an explicit "haan")
Production rule: no live customer hosting, no production domain migration, no change to the Mac server until the gate in `PRODUCTION_READINESS.md` passes.

1. **Read-only inspection of the Mac server** — OS version, nginx, PHP-FPM, Redis, MariaDB, disk, ports, existing sites. Open question for owner: do they paste command output (safer) or may Claude use SSH? Is the Mac the staging machine or a separate one?
2. **Back up Mac configs and DBs** before any install.
3. **Staging install** on a separate user/port/domain (Postgres, Redis, API, web, agent, scheduler) per `docs/runbooks/deployment.md`. Brief §6: Mac is not automatically the final production host; a Linux production plan is needed.
4. **Real-service tests**: agent with real nginx, Let's Encrypt, backup + **restore drill**, Razorpay test mode, SMTP, Cloudflare scoped token, n8n.
5. Secrets (Razorpay test keys, Cloudflare token, SMTP, n8n) go in the server `.env` only — never chat or Git. Owner handles SSH keys.

## Not blocked, not done
- Re-run Next.js `npm run build` and Playwright e2e and record results.
- Capture an exact backend test count (`pytest -q | tail -1`) in CI output.
- Add Dockerfiles / compose for Linux service simulation (brief §6).
- Add `.env.example` for frontend and agent.
- CI workflow running tests, lint, types, build and `scripts/security/scan.sh` (gap list item).
- Items from `docs/security/gap-list-and-remediation-plan.md` (mandatory staff MFA, RLS pilot, audit tamper evidence, key rotation, incident runbook, etc.).
- Load/capacity tests with documented limits (`scripts/loadtest.py` exists; no recorded results).
- Merge `feature/pdf-handoff-docs-audit` via reviewed pull request (not pushed yet).

## Final production gate (brief §11)
All unchecked. See `PRODUCTION_READINESS.md`.
