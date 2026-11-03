# KVL Hosting Panel — Phase 03 Report: Multi-tenant SaaS foundation

## 1. Summary
Tenant lifecycle (pending → trial → active → suspended → cancelled), quotas, activity history, metadata export and strict isolation.

## 2. Requirements completed
Tenant context comes from the authenticated membership, never from a client-supplied id; another tenant's object returns 404; `consume_quota()` reserves quota atomically (row lock); suspension keeps all data and makes infrastructure writes read-only while billing/support stay open; cancellation is terminal and starts a configurable retention window; business rules (trial/grace/retention days, default quotas) are platform settings with validation and audit. Row-level security was deliberately not added: application authorization is the control and RLS would be an untested second layer.

## 3. Routes and screens
`/admin/customers` (list, filters, create, detail sheet with overview/quotas/members/activity, suspend/reactivate/cancel with confirmation, export), `/admin/settings` (business rules, roles and permissions editor, team and invitations).

## 5. Endpoints
`/tenants` (+ `/current`, `/{id}`, `/status`, `/quotas`, `/activity`, `/members`, `/export`), `/settings`, `/stats/dashboard`.

## 8. Isolation review
Tests create several tenants and prove A cannot read/update/manage B, cannot switch to an unowned membership, resellers see only their own tenants, suspended and cancelled tenants follow the documented policy, and dashboard stats are scoped per role.

## 13. Known limitations
Customer file/database export on cancellation is an owner-run job (documented), not self-service.

## 16. Owner approval
Pending.
