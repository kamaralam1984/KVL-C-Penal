# Engineering brief for parallel phase builders

Repo layout: `frontend/` (Next 16 App Router, TS, Tailwind v4), `backend/` (FastAPI, SQLAlchemy 2, Postgres), `agent/` (restricted server agent), `docs/`.
Read `docs/phase-reports/PHASE-01.md`, `KVL_Hosting_Panel_Detailed_Phase_Prompts_01-18.md` (your phases) and the existing code before writing. **Reuse; do not duplicate.**

## Backend conventions (`backend/`, use `backend/.venv`)
- One router per module: `app/api/v1/routers/<name>.py` exposing `router` (auto-mounted under `/api/v1`). Models in `app/models/<name>.py` (auto-imported). Services in `app/services/<name>.py`. Agent op schemas in `app/ops/<name>.py` (`register_op`).
- Authorization: `Depends(require("resource:action"))` from `app/core/deps.py` (permission catalog is `app/core/rbac.py`; all catalog entries already exist; append a grant to a role in ROLE_DEFS with a minimal Edit if you truly need one, and say so in your report). Deny by default.
- Tenancy: every tenant-owned table has `tenant_id`. Derive it with `resolve_tenant/require_tenant(ctx, requested)`; never trust a client tenant_id for tenant users. Another tenant's object must return **404**. Platform staff (`ctx.is_platform`) may pass `tenant_id`. Resellers see only tenants with `reseller_id == ctx.reseller_id`.
- Use `audit()` for every security/business-relevant write, `consume_quota()` for quota'd resources, `queue_email()` for mail (no SMTP in dev), `enqueue_job()` for ANY infrastructure change (never run commands in the API process).
- Errors: `raise api_error(status, "code", "message")`. Validate input with Pydantic (`Field` limits, patterns). No raw SQL strings with user input.
- Secrets: encrypt at rest with `encrypt_secret`, never log/return them (show-once patterns), never commit.
- No fake data, no faking integrations. If credentials/software are absent, return an explicit `not_configured` state and make the UI say so. Real external calls (Cloudflare, Razorpay, ACME) go through a thin client class that tests replace with a fake.
- Tests: `tests/test_<module>.py`, pytest, fixtures in `tests/conftest.py` (`client`, `make_client`, `make_user`, `admin`, `login`, `db`). Prove authorization, tenant isolation (two tenants), validation failures, and the negative paths in your phase prompt. Run with your own DB: `TEST_DATABASE_URL=postgresql+psycopg://kvl:kvl_dev_only_pw@127.0.0.1:54329/kvl_test_gN pytest tests/test_<module>.py`. Do NOT run alembic autogenerate/upgrade; the lead consolidates migrations. Do not edit `tests/conftest.py` beyond adding fixtures.
- Don't edit files owned by another module. Shared core (`app/core/*`, `app/main.py`, `app/models/identity.py`, `tenant.py`, `infra.py`, `services/jobs.py`) is read-only except tiny additive edits you report.

## Agent conventions (`agent/`)
Register operations in `agent/kvl_agent/operations/<group>.py` with `@operation(name, Schema, timeout=, destructive=)` (see `registry.py`, `context.py`, `testing.py`, `operations/core.py`). Params are strict Pydantic (`extra="forbid"`, regex-validated hostnames/paths). Use `ctx.path()` for every file path, `ctx.run([...])` (no shell) for allow-listed binaries (`allow_binary("nginx")`). Mirror each op's params schema in `backend/app/ops/<group>.py`. Operations must be dry-run safe and never touch real system paths in tests (sandbox `data_dir`). No customer-supplied command strings. Never modify production config/DNS/disks.

## Frontend conventions (`frontend/`)
- Next.js here is v16 with breaking changes: if unsure about an API, read `frontend/node_modules/next/dist/docs/`.
- Replace the placeholder `page.tsx` files for your slugs (they render `<PlaceholderPage/>`). Build one shared client component per module under `src/components/<module>/` and have the admin/hosting/customer route files render it (backend scoping returns role-appropriate data). Keep route files thin.
- Use: `useApi`, `useMutation` (`src/hooks/use-api.ts`), `api/get/post/put/patch/del` (`src/lib/http.ts`), `ResourceTable`, `FormDialog`, `StatusPill`, `RequirePerm`, `Can`, `DetailList`, `LiveBadge`, `PageHeader`, `Card`, `KpiCard`, `Sheet`, `ConfirmDialog`, `Tabs`, `TrendChart/ChartCard`, `EmptyState/ErrorState/NotYetAvailable`, `useToast`, `useAuth`.
- Design: tokens only (no raw hex), light+dark, gold/silver `AccentLine`, `AnimatedIcon` for module icons/headers, compact data typography (`tnum`). **Mobile-first, app-like**: the shell shows a bottom tab bar on phones; pages must work at 360px width with no horizontal page scroll (tables scroll inside their card; use the `hideOnMobile` column option), 44px touch targets, drawers/sheets instead of wide modals. Reduced motion respected automatically.
- Show real loading, empty, error and permission states. Mark anything not backed by a live API clearly. Confirm destructive actions with `ConfirmDialog`.
- Do NOT run `next build` or `next dev` (shared `.next` dir). Verify with `cd frontend && npx tsc --noEmit` and `npx eslint <your files>`. Don't edit shared components; add new files instead (tiny additive edits OK).

## Definition of done
Backend + tests green, agent ops + tests green (if any), frontend screens typed and linted, a short report at `docs/phase-reports/PHASE-XX.md` per phase (use the template in the prompt pack §E; be honest about limitations and what needs real credentials/hardware), final reply summarising files, endpoints, permissions needed, test results and any gap.
