# KVL Hosting Panel — Phase 14 Report (Plans, Subscriptions, Billing, Razorpay)

## 1. Phase summary
Versioned plan catalogue, subscription lifecycle tied to VERIFIED payment, GST-ready invoices, credit notes/refunds, coupons,
revenue/MRR/ARR/churn reports, and a Razorpay integration (orders, signed idempotent webhooks, reconciliation, refunds). Test mode only; no real payments processed.

## 2. Requirements completed
- Catalogue: plans with immutable `PlanVersion` rows (DB/ORM refuses updates; "edit" = publish new version), limits, feature flags, trial days, HSN/SAC, GST rate, monthly/yearly prices, add-ons. Subscriptions pin a version; renewals bill the pinned version; quotas follow the pinned version (skipped for reseller-managed tenants).
- Lifecycle (`services/billing.py`): trial (once per tenant), checkout (activation invoice), activation only from a verified payment, renew (renewal invoice generated N days ahead), upgrade (prorated invoice, applied after payment), downgrade/interval change (scheduled to period end), add-ons, cancel at period end or immediately, resume, past_due + grace, suspension through `services.tenants.transition` (data is never deleted), reactivation on verified payment, full event history. `run_lifecycle()` is idempotent and scheduler-callable.
- Proration (documented in module docstring and tested): `credit = round_half_up(old*R/T)`, `charge = round_half_up(new*R/T)`, `net = charge - credit`.
- Invoices: concurrency-safe gap-free numbering per series+financial year (`INV/2026-27/000001`, prefix configurable), seller/buyer GST snapshot (GSTIN, state code), HSN/SAC per line, CGST+SGST (same state) or IGST, place of supply, discount spread over lines, credit notes (own series) with proportional tax, printable HTML download, billing profile per tenant.
- Coupons: percent or flat, validity window, per-plan, global and per-tenant redemption limits, reservation at checkout, release on void, redeemed on paid; 100% discount settles server-side.
- Payments: payment records per attempt, failed payment handling (dunning counter, `past_due` for renewals, failed-payments list), manual bank-payment recording by admins (audited, full amount), refunds (reservation of refundable balance, pending/processed/failed, credit note, commission reversal).
- Reports: revenue, MRR, ARR, logo/revenue churn with formulas returned by the API and shown in tooltips. Money is integer paise throughout.
- Razorpay (`services/razorpay.py`): `RazorpayClient` (httpx, basic auth), `get_gateway()` returns None without keys; order creation; HMAC-SHA256 webhook verification over the raw body with constant-time compare; `payment_webhook_events` unique `(provider,event_id)` idempotency; failed events re-run on provider retry; amount/currency checks; reconcile (server-side fetch) for admins; the checkout callback endpoint verifies the browser signature, records it and fetches provider status but NEVER marks anything paid.

## 3. Routes and screens
`/admin/plans` (plans, versions, add-ons, coupons), `/admin/subscriptions` (list, history, run lifecycle), `/admin/billing` (revenue, MRR/ARR with formula tooltips, churn, invoices with manual payment/void/refund, payments, failed payments, provider events, invoice/GST settings), `/customer/billing` (subscription detail, plan catalogue, checkout with coupon + add-ons, trial, upgrade/downgrade with proration preview, cancel/resume, invoices with download, payments, tax details; renewal/failed-payment/suspended banners). Razorpay checkout JS is loaded only when the backend reports it configured; otherwise a "Payments not configured" state is shown.

## 4. Components created
`frontend/src/components/billing/{billing-pages.tsx,checkout.tsx,billing-types.ts}`.

## 5. Backend endpoints
Catalogue: `GET /billing/catalog`, `GET /billing/config`, `GET|POST /plans`, `POST /plans/{code}/versions`, `PATCH /plans/{code}`, `POST /plans/addons`.
Subscriptions: `GET /billing/subscription[/history|/proration-preview]`, `POST /billing/subscription/{trial|change|cancel|resume|addons}`, `POST /billing/checkout`, `GET /subscriptions`, `POST /subscriptions/lifecycle/run`.
Invoices/payments: `GET /billing/invoices[/{id}|/{id}/download]`, `POST /billing/invoices/{id}/{pay|void|manual-payment}`, `GET /billing/credit-notes`, `GET /billing/payments[/{id}]`, `POST /billing/payments/{id}/{callback|reconcile|refund}`, `GET /billing/refunds`, `GET /billing/webhook-events`, `POST /payments/razorpay/webhook` (public, signature-authenticated).
Other: `POST /billing/coupons/validate`, `GET|POST /billing/coupons`, `POST /billing/coupons/{id}/deactivate`, `GET|PUT /billing/profile`, `GET|PUT /billing/settings`, `GET /billing/reports/revenue`, `GET /billing/failed-payments`.

## 6. Database models and migrations
`app/models/billing.py`: `billing_plans`, `billing_plan_versions`, `billing_addons`, `billing_profiles`, `subscriptions` (partial unique index: one live subscription per tenant), `subscription_events`, `invoice_sequences`, `invoices`, `credit_notes`, `payments`, `refunds`, `coupons`, `coupon_redemptions`, `payment_webhook_events`. **No migration written** (lead consolidates).
Additive core edits (reported): `core/config.py` gained `n8n_base_url` and `razorpay_api_base`; `core/rbac.py` customer role gained `billing:write` and `subscriptions:write` (needed for checkout/plan changes by the tenant).

## 7. Integrations
Razorpay (needs real credentials: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`; webhook URL `/api/v1/payments/razorpay/webhook`; events: payment.authorized/captured/failed, order.paid, refund.processed/failed). Without them the API reports `not_configured` (503 for webhook/refund, `payment.status = not_configured` at checkout). Emits `billing.payment_verified` and `tenant.activated` automation events.

## 8. Authorization and tenant-isolation review
Tenant users are pinned to their own tenant; another tenant's invoice/payment/subscription returns 404 for read, download, pay and callback. Invoice download uses the same visibility rule as detail and is audited; resellers see only own clients' invoices and cannot pay them. Admin-only: manual payment, refund, reconcile, void, coupons CRUD, settings, reports, webhook log. Suspended tenants keep access to billing so they can pay.

## 9. Security review
No card data stored; webhook summaries keep ids/amount only; provider secrets never returned (only the public key id); webhook rate-limited and size-capped; signature checked before parsing; checkout signature compared in constant time; HTML invoice escapes all values and is served with a restrictive CSP and attachment disposition; activation/paid state can only be reached from `mark_payment_captured` (signed webhook, admin reconcile via provider API, or admin manual record).

## 10. Tests executed
`test_billing.py` (14 tests), `test_razorpay.py` (12 tests), `test_reseller.py` (13 tests), `test_automation.py` (18 tests); combined run: 57 passed in one run against kvl_test_g4 (all green).
Billing tests: proration math and rounding, GST intra/inter-state, plan versioning keeps historic terms (+ ORM immutability), trial expiry suspends without deleting data, renewal -> past_due -> grace -> suspend -> reactivate, cancel at period end/immediate, upgrade pay-to-apply and scheduled downgrade, coupon rules, 100% discount, numbering uniqueness/gaplessness under 6 concurrent threads, tenant billing isolation, invoice download authorization and HTML escaping, MRR/ARR/churn formulas, catalogue validation.
Razorpay tests: valid/invalid/tampered/missing signature, duplicate event idempotency (single payment, single event), pending/failed/success transitions and retry, client-callback-only never marks paid (tenant stays pending), amount mismatch -> 5xx and later retry success, reconcile, refund consistency (reservation, partial/full, credit notes, failed refund), refund without gateway -> not_configured, admin-only webhook log.

## 11. Lint/type/build results
`npx tsc --noEmit`: clean. `eslint` on all new frontend files (reseller, billing, automation components and routes): no errors or warnings in them (a pre-existing error in another module's `components/dns/records.tsx` was reported by the same run and is not part of this phase). `next build` not run (shared `.next`).

## 12. Resource impact
Small relational tables; webhook handling is synchronous and short. The lifecycle/reminder/dispatch jobs must be scheduled by cron/systemd (not started automatically).

## 13. Known issues and limitations
- Nothing has been exercised against the real Razorpay API: client calls are tested with a fake gateway. Needs test-mode credentials, a reachable webhook URL and a live checkout run before any real payment (owner approval required).
- Invoices are HTML, not PDF; there is no e-invoice (IRN) integration; TCS/TDS and multi-rate per-line GST beyond a single rate per plan/add-on are not modelled.
- Reseller-to-client billing/collection is not built (platform bills the tenant directly).
- Yearly interval change is scheduled for period end (no mid-period interval proration); add-on removal is not implemented.
- Subscriptions use calendar-month arithmetic; no timezone-specific cut-offs. Dunning is counters + reminder events (n8n); no automatic retry charging (Razorpay orders are customer-initiated; no saved-card auto-debit).
- Refund tax split is proportional; partial refunds do not reverse subscription entitlements automatically (admin cancels separately).

## 14. Rollback instructions
Remove `routers/billing.py`, `services/{billing,razorpay}.py`, `models/billing.py`, drop the tables, revert the two additive core edits, restore placeholder pages.

## 15. Next phase prerequisites
Phase 15 consumes `emit_event` for `billing.payment_verified`, `tenant.activated`, invoice-due and renewal reminders (already wired).

## 16. Owner approval status
Pending owner review. Do not enable live Razorpay keys before approval.
