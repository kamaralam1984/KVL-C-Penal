# KVL Hosting Panel — Phase 12 Report: Customer portal

## 1. Summary
The customer area assembles the modules built in earlier phases behind customer-scoped APIs: dashboard (plan, usage meters, protection status, onboarding checklist), websites, domains, databases, applications, files, SSL, backups, billing and support, plus profile/security and developer keys under `/account`.

## 2. How isolation is guaranteed
There is no customer-only code path: the same endpoints serve every role and filter by the membership's tenant. Ownership is rechecked in each service call; the frontend never filters for security. Two-tenant tests exist per module (websites, domains, apps, databases, files, backups, tickets, billing, API keys).

## 3. Routes
`/customer/{dashboard,websites,domains,databases,applications,files,ssl,backups,billing,support}`, `/account`.

## 13. Known limitations
Customers hold read-only SSL/DNS permissions by default (going live is a platform-staff step). Notifications are the platform announcements feed; per-event push/email notifications depend on an SMTP transport and n8n being configured.

## 16. Owner approval
Pending.
