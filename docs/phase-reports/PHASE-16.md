# KVL Hosting Panel — Phase 16 Report: Support and Business Management

## 1. Phase summary
Ticketing with SLA tracking, knowledge base, targeted announcements (feeding the top-bar bell), staff management views, customer timeline, reports and CSV export. Support staff hold no infrastructure permissions.

## 2. Requirements completed
- Tickets: create (idempotent via `Idempotency-Key`), categories, departments, priority/status, assignment/reassignment, customer replies, internal notes, attachments, history, search/filters/pagination, CSV export.
- SLA: per-priority first-response and resolution targets (urgent 1h/4h, high 4h/24h, normal 8h/72h, low 24h/7d), breach flags, escalation function `app.services.support.run_sla_escalation` (also `POST /support/sla/run`), 24x7 clock by default with an optional business-hours mode (`SUPPORT_SLA_BUSINESS_HOURS=true`, Mon-Fri 09:00-18:00 UTC, no holiday calendar). `on_hold` pauses the clock by shifting due dates.
- Reports: aging, SLA compliance, first-response time, workload per staff.
- Knowledge base: categories, draft/published articles, search, related articles, helpful/not-helpful (one vote per user, changeable).
- Announcements: draft/published/archived, scheduled `publish_at`, expiry, audience by role and/or tenant, per-user read and acknowledgement, `/announcements/feed` for the bell.
- Staff: staff list via platform memberships, department membership, scoped-permissions view (with infrastructure permissions called out), activity from audit, workload.
- Customer timeline: tenant activity + tickets + audit for support staff.

## 3. Routes and screens
`/admin/support` (Inbox, Reports, Knowledge base, Announcements, Staff & workload), `/customer/support` (My tickets with new-ticket + attachments, Help articles, Announcements). Top-bar bell now reads `/announcements/feed` (demo badge removed).

## 4. Components created
`frontend/src/components/support/{common,ticket-detail,ticket-list,kb-panel,announcements-panel,staff-panel,support-console}.tsx`; `NotificationsMenu` in `shell/topbar-menus.tsx` replaced.

## 5. Backend endpoints (all under /api/v1)
- `GET /support/meta`, `GET /support/agents`, `GET|POST /support/departments`, `PATCH /support/departments/{id}`, `GET /support/departments/{id}/members`, `PUT|DELETE /support/departments/{id}/members/{user_id}`
- `POST|GET /support/tickets`, `GET /support/tickets/export.csv`, `GET|PATCH /support/tickets/{id}`, `GET /support/tickets/{id}/history`, `POST /support/tickets/{id}/messages`, `POST /support/tickets/{id}/assign`, `POST /support/tickets/{id}/attachments`, `GET /support/attachments/{id}/download`
- `POST /support/sla/run`, `GET /support/reports/{aging,sla,first-response,workload}`, `GET /support/customers/{tenant_id}/timeline`
- `GET|POST /kb/categories`, `DELETE /kb/categories/{id}`, `GET|POST /kb/articles`, `GET|PATCH|DELETE /kb/articles/{ref}`, `POST /kb/articles/{id}/feedback`
- `GET /announcements/feed`, `POST /announcements/{id}/read|ack`, `GET|POST /announcements`, `PUT|DELETE /announcements/{id}`, `GET /announcements/{id}/stats`
- `GET /staff/members`, `GET /staff/members/{id}`, `GET /staff/members/{id}/activity`

## 6. Database models
`app/models/support.py`: support_departments, support_department_members, tickets (sequence `ticket_number_seq`), ticket_messages, ticket_attachments, ticket_events, kb_categories, kb_articles, kb_feedback, announcements, announcement_reads. **No Alembic migration written** (lead consolidates; tests build the schema from metadata). The sequence must be included in the migration.

## 7. Integrations
Email via `queue_email` only (dev outbox; no SMTP). Webhook events `ticket.created|replied|status_changed|sla_breached` via the Phase 17 service. No virus scanner. Periodic SLA/escalation execution needs a scheduler that calls `run_sla_escalation`/`POST /support/sla/run`; none ships in this repo.

## 8. Authorization and tenant-isolation review
- Permissions: `tickets:read|write` (customers, resellers, staff), `tickets:manage` + platform scope = "staff view" (assignment, internal notes, SLA, reports, departments, timeline), `kb:write` authors, `kb:manage` delete, `announcements:write` (platform) manage, `announcements:manage` delete, `staff:read` staff list. No role grants changed.
- Tenant users see only their tenant; resellers only tickets of their clients; other tenants' objects return 404 (tickets, history, messages, attachments, uploads).
- Internal notes: serializers take a `staff` flag and assert; customer search/CSV/history/attachments are filtered; emails and webhook payloads never include bodies; platform users without `tickets:manage` (e.g. hosting admin) also get the public view. Tested on every customer-facing read.
- Attachments: 5 MB cap (configurable), allow-list png/jpg/gif/webp/pdf/txt/log/csv, extension + declared type + magic bytes must agree, no multi-extension tricks (`.php.png`), random 48-hex storage names under `SUPPORT_UPLOAD_DIR/<tenant>/<ticket>/`, resolved-path containment check, forced `attachment` download with `nosniff` and CSP sandbox, 20 per ticket.
- Support role verified to hold no infra permissions (test) and to be denied roles/staff/security/apikeys/webhooks/audit endpoints.

## 9. Security review
CSV export neutralises formula injection. KB bodies and announcements render as plain text in the UI (never HTML). Ticket creation is rate limited (20/h/user). All sensitive writes audited.

## 10. Tests executed
`backend/tests/test_support.py` (32 cases) and `backend/tests/test_kb_announcements.py` (5) pass: tenant ticket isolation on every endpoint, internal-note invisibility on every customer-facing read (detail, history, list, search probe, CSV, attachment download, emails, hosting-admin view), staff-only endpoints denied to customers, support role holds no infrastructure permissions, assignment/history/workload, 12 attachment rejection cases (spoofed extension, content-type mismatch, `.php.png`, html/svg, NUL/markup in text, empty, no extension), oversize (413), path-traversal filenames, other-tenant upload/download, SLA due times per priority, breach flags + escalation (idempotent), resolution breach, on-hold pause, business-hours clock, status flow, search/filters/pagination/aging, CSV authorisation + formula safety, departments + customer timeline, suspended tenants can still open tickets, KB draft/published visibility for every role, KB search/related/feedback, announcement audience targeting (role, tenant, scheduled, expired, draft), read/ack per user, management permissions.
Full run together with existing `test_auth.py` and `test_rbac_tenants.py`: **175 passed, 0 failed** (Postgres `kvl_test_g5`; run with `REDIS_URL` pointing at an unreachable port so the in-process rate limiter is used, because parallel builders share one Redis and each test fixture flushes `rl:test:*`).

## 11. Lint/type/build results
- `npx eslint` on all new/changed frontend files (support, security, shell/topbar-menus, the three route files): clean (exit 0).
- `npx tsc --noEmit` (whole project): **no errors in any Phase 16/17 file**; the run reports pre-existing errors in other builders' files (automation, backups, billing, files pages), which were not touched.

## 12. Resource impact
No background processes added. Attachments land on local disk; capacity monitoring is not implemented.

## 13. Known issues and limitations
- No malware scanning; text-type validation is heuristic; uploads are read into memory (capped).
- SLA clock: `pending` (awaiting customer) does not pause, only `on_hold` does; no holiday calendar; breach detection happens on the periodic run (the API also computes live state).
- Ticket creation by staff requires a tenant id typed into the dialog (no tenant picker yet).
- Announcement audience editing uses comma-separated ids in the UI.
- Staff account creation/invitation reuses the Phase 2 invitation endpoints; no new UI for it here.
- Departments do not route/auto-assign tickets.
- No Alembic migration (see section 6).

## 14. Rollback instructions
Remove the new routers/models/services/components listed above and revert `topbar-menus.tsx`; drop the tables in section 6 and sequence `ticket_number_seq`; remove the Phase 16 settings from `config.py`.

## 15. Next phase prerequisites
Scheduler for SLA escalation; migration consolidation; SMTP for ticket emails.

## 16. Owner approval status
Pending owner review.
