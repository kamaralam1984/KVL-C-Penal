# KVL Hosting Panel — Phase 13 Report (Reseller and Agency Portal)

## 1. Phase summary
Reseller lifecycle (apply, Super Admin approval, limits and platform allocation), reseller portal (clients, packages, white-label,
commissions, reports), backend-enforced reseller isolation, and a derived-only commission ledger.

## 2. Requirements completed
- Lifecycle: signed-in verified user applies (`pending_approval`); Super Admin approves (creates a `reseller` Membership with `reseller_id`, so the existing reseller RBAC scope works), rejects, suspends, reactivates; admin can add members (existing users).
- Allocation: per-reseller `max_clients` and per-resource `allocation` (websites, domains, databases, apps, disk_mb, bandwidth_gb) plus optional platform `unit_costs`. Packages cannot exceed or include resources outside the allocation; committed client quotas (sum of client `TenantQuota` limits) can never exceed it (row-locked check on create-client / assign-package). Admin cannot lower allocation below what is committed.
- Clients: create client = `provision_tenant(reseller_id=...)` + package quotas + owner invitation email (queued) + `tenant.created` automation event; assign package; renewal date; usage; support-context endpoint; clients CSV (formula-injection safe).
- Packages: retail price, wholesale cost derived from unit costs, margin (paise and bps); in-use packages are deactivated instead of deleted.
- White-label: portal name, https logo, hex colours, support email, email sender/footer config, platform disclosure text, custom domain with DNS TXT verification state machine (`pending -> verified | failed`, 72h window, re-check demotes) using a pluggable resolver (dnspython default, fake in tests). Public `GET /branding/by-host` serves only verified domains and only presentation fields; `GET /branding/effective` returns a tenant user's own reseller's branding only.
- Commissions: rules (platform default or per reseller/plan, rate in bps, minimum), append-only ledger entries created when an invoice is verified paid (idempotent per invoice), `pending -> approved -> paid`, negative reversal entries on refunds, reconciliation endpoint, CSV export. Balances are computed from entries on every request; paid entries are immutable (ORM guard) and undeletable.

## 3. Routes and screens
`/reseller/{dashboard,clients,packages,branding,commissions,reports,settings}`, `/admin/resellers` (approve with allocation, reject, suspend, limits, commission rule, ledger approve/payout). All live data; loading, empty, error and permission states.

## 4. Components created
`frontend/src/components/reseller/{reseller-pages.tsx,shared.tsx}`; route files are thin. The reseller dashboard no longer uses demo fixtures.

## 5. Backend endpoints
Public/authenticated: `POST /reseller/apply`, `GET /reseller/application`, `GET /branding/by-host` (public, rate-limited), `GET /branding/effective`.
Reseller (own data only): `GET|PATCH /reseller/me`, `GET /reseller/allocation`, `GET|POST /reseller/packages`, `PATCH|DELETE /reseller/packages/{id}`, `GET|POST /reseller/clients`, `GET|PATCH /reseller/clients/{id}`, `POST /reseller/clients/{id}/package`, `GET /reseller/clients/{id}/support-context`, `GET|PUT /reseller/branding`, `POST /reseller/domains`, `POST /reseller/domains/{id}/verify`, `DELETE /reseller/domains/{id}`, `GET /reseller/commissions`, `.../reconciliation`, `.../export.csv`, `GET /reseller/reports/summary`, `GET /reseller/reports/clients.csv`.
Admin: `GET /resellers`, `GET /resellers/{id}`, `POST /resellers/{id}/{approve|reject|suspend|reactivate}`, `PUT /resellers/{id}/limits`, `POST /resellers/{id}/members`, `GET /resellers/{id}/commissions[/reconciliation]`, `POST /resellers/{id}/commissions/{approve|pay}`, `GET|POST /resellers/commission-rules`, `PATCH /resellers/commission-rules/{id}`.

## 6. Database models and migrations
`app/models/reseller.py`: `resellers`, `reseller_packages`, `reseller_clients`, `reseller_domains`, `commission_rules`, `commission_entries`. **No Alembic migration written** (lead consolidates); tests build the schema with `create_all`.

## 7. Integrations
None external. DNS TXT lookup through dnspython (real network only when an operator clicks "Check now"). Emits `tenant.created` to the automation outbox.

## 8. Authorization and tenant-isolation review
Reseller identity comes only from the membership (`ctx.reseller_id`); every query filters by it and other resellers' client/package/domain ids return 404. Admin routes require `resellers:*`/`commissions:*` and are platform-only. Resellers hold no server/infra permissions and cannot call tenant status/quota/create endpoints (403). Reseller must be `approved` for portal access. Tenant users never see another reseller's branding.

## 9. Security review
Input validated with Pydantic (hex colours, https-only logo URL, domain regex, GSTIN pattern); CSV cells starting `= + - @` are prefixed; public branding endpoint rate-limited and limited to verified domains; audit events on every write; invitations use existing hashed-token mechanism.

## 10. Tests executed
`backend/tests/test_reseller.py`: 13 tests (see PHASE-14 for full-suite run). Covers application/approval, cross-reseller 404s, package/client allocation enforcement, margin, admin allocation floor, unauthorized server/admin operations, ledger consistency + immutability + reversal, DNS verification state machine, white-label tenant boundary, CSV safety.

## 11. Lint/type/build results
`tsc --noEmit` and `eslint` on the new files: see PHASE-14 section 11 (same run). `next build` not run (shared `.next`).

## 12. Resource impact
Negligible: a few small tables; DNS check on demand only.

## 13. Known issues and limitations
- Reseller clients are billed by the platform's own plans/invoices; the reseller's retail price is a catalogue/margin figure, there is no reseller-to-client invoicing or payment collection yet.
- Commission is computed on the platform invoice's taxable value; payouts are recorded manually (no money movement).
- Client owner onboarding relies on the queued invitation email (no SMTP in dev) accepted via the existing invitation flow.
- Custom-domain verification proves DNS ownership only; vhost/TLS for the domain and applying branding on the login pages by host is front-end/deployment follow-up.
- "Client-level support context" shows status/quota/activity/invoice count; ticket data lives in the support module.
- Reseller staff (members) can be added only by an admin by email of an existing user; there is no self-service reseller team invite.

## 14. Rollback instructions
Drop the six tables above, remove `routers/resellers.py`, `services/reseller.py`, `models/reseller.py`, the reseller frontend components and restore the placeholder pages.

## 15. Next phase prerequisites
Phase 14 billing hooks `settle_invoice` -> `record_sale_commission` / `finalize_refund` -> `reverse_commission` (already wired).

## 16. Owner approval status
Pending owner review.
