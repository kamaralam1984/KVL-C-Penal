# KVL Hosting Panel --- Detailed Claude Code Prompts (All 18 Phases)

**Product:** KVL Hosting Panel\
**Platform:** Multi-tenant Hosting SaaS + Hosting Business Management
System\
**Development:** Ubuntu 24.04 LTS, 16 GB RAM, Claude Code\
**Frontend:** Next.js App Router + TypeScript\
**Backend:** Python FastAPI\
**Automation:** n8n\
**Deployment:** Staged; production iMac is separate and must not be
modified without explicit approval.

------------------------------------------------------------------------

# A. How to use this prompt pack

1.  Keep the master `CLAUDE.md` in the repository root.
2.  Start Claude Code from the repository root.
3.  Give Claude the **Phase 0 Master Execution Prompt** once per session
    or when beginning a new phase.
4.  Then give only the prompt for the phase you want to implement.
5.  Ask Claude to inspect first, propose a plan, then implement.
6.  Review the phase report and test results before approving the next
    phase.
7.  Do not ask Claude to implement all 18 phases in one run.

**Important:** A roadmap item is not a working feature until its
backend, authorization, UI, tests, and operational behavior are
implemented and verified. Mock data must be visibly identified as demo
data.

------------------------------------------------------------------------

# B. Global product and design requirements

These requirements apply to every phase.

## Product roles

-   Platform Owner / Super Admin
-   Hosting Admin
-   Support Staff
-   Customer
-   Reseller / Agency

## Premium 2026 visual direction

Build a distinctive, professional, premium enterprise SaaS product.

-   Light theme is the default.
-   Dark theme must be equally complete and polished.
-   Use a restrained neutral base with a deliberate KVL accent palette
    and meaningful semantic colors.
-   Define design tokens for colors, typography, spacing, radii,
    borders, shadows, elevation, focus rings and motion.
-   Use consistent typography hierarchy and compact but readable
    information density.
-   Use advanced charts only when they communicate useful information.
    Include legends, units, labels, tooltips, time ranges and empty
    states.
-   Tables should support appropriate sorting, filtering, search,
    pagination, column visibility and row actions.
-   Use purposeful micro-interactions, subtle transitions, responsive
    drawers, dialogs, hover/focus states and useful action feedback.
-   Respect `prefers-reduced-motion`.
-   Support keyboard navigation, visible focus, semantic HTML, contrast
    and accessible labels.
-   Build responsive desktop, tablet and mobile experiences.
-   Use Lucide icons consistently.
-   Avoid generic AI dashboard styling, excessive gradients, neon
    colors, unnecessary glassmorphism, giant empty cards and decorative
    animation that harms usability.
-   Maintain visual consistency across Super Admin, Hosting Admin,
    Customer and Reseller areas.

## Technical architecture

-   Frontend: Next.js App Router + TypeScript.
-   Styling/components: Tailwind CSS + shadcn/ui, customized to KVL
    tokens.
-   Motion: Framer Motion, used purposefully.
-   Icons: Lucide React.
-   Charts: Recharts or another justified library.
-   Forms: React Hook Form + Zod when appropriate.
-   Backend: Python FastAPI.
-   Validation: Pydantic.
-   ORM: SQLAlchemy 2.
-   Migrations: Alembic.
-   Database: PostgreSQL for SaaS control-plane data.
-   Cache/queue: Redis.
-   Background jobs: Celery.
-   Automation: n8n.
-   Server agent: separate restricted Python service.
-   Hosting integrations: Nginx, PHP-FPM, Node.js/PM2, Python runtimes,
    Let's Encrypt, Cloudflare.
-   Testing: Pytest, HTTPX, Playwright.
-   Source control: private GitHub repository.

Do not add every library automatically. Inspect the repository and
choose the smallest suitable dependency set.

## Non-negotiable security and safety

-   Backend authorization is authoritative; frontend hiding is not
    security.
-   Deny by default.
-   Enforce tenant isolation in every backend query and operation.
-   Never expose unrestricted shell execution.
-   Never execute customer-supplied shell commands as root.
-   Server agent may execute only explicitly allow-listed operations
    with typed parameters.
-   Use least privilege, timeouts, audit logs and safe failure handling.
-   Never commit secrets, API keys, credentials, private keys or real
    `.env` files.
-   Encrypt sensitive credentials at rest and restrict their access.
-   Verify payment webhooks cryptographically.
-   Preserve existing files, databases, backups, DNS records and
    production websites.
-   Never format or repartition disks.
-   Never change production DNS or deploy to the production iMac without
    explicit owner approval.
-   Back up and validate configuration before approved infrastructure
    changes.
-   Use database migrations for schema changes.
-   Do not claim a feature is operational if it is only a UI mockup.
-   Do not claim SOC 2, ISO 27001 or other certification without formal
    audit.

## Performance requirements

Development machine has 16 GB RAM. Avoid starting every service
simultaneously. Introduce PostgreSQL, Redis, Celery, n8n and monitoring
when needed. Measure memory and CPU use. Keep frontend bundles and API
responses reasonable. Use pagination for large datasets.

------------------------------------------------------------------------

# C. Phase 0 --- Master execution prompt

Use this at the beginning of a new Claude Code session or before each
phase:

``` text
You are the Principal Architect, Product Engineer, Security Engineer and Senior UI/UX Engineer for KVL Hosting Panel.

Read CLAUDE.md, README.md, the master roadmap, architecture documents, previous phase reports and the current repository before changing anything.

The product is a commercial multi-tenant hosting SaaS and hosting business management platform.

Follow all KVL product, design, security, architecture and deployment rules.

Before implementing the requested phase:
1. Inspect the current Git branch and working tree.
2. Inspect repository structure and existing implementation.
3. Read the previous phase report.
4. Identify what already exists and must be reused.
5. Identify missing prerequisites and risks.
6. Propose files, routes, components, database migrations, API endpoints and tests.
7. Explain any dependency or architecture decision.
8. Wait for approval if the work would change production infrastructure, delete data, change DNS, or perform destructive operations.

Implement only the phase explicitly requested. Do not begin later phases.

Work in small, reviewable steps. Keep the KVL premium 2026 light-first design system with complete dark mode. Add purposeful animations, responsive layouts, accessible controls, useful charts and advanced tables where relevant.

Do not fake live data or operational behavior. Label demo data clearly.

At completion:
- Run relevant lint and type checks.
- Run backend tests and frontend tests.
- Run production build where applicable.
- Review authorization, tenant isolation and secret handling.
- Update documentation.
- Write docs/phase-reports/PHASE-XX.md.
- Summarize changed files, routes, components, APIs, migrations, test results, known issues, security review, resource impact and rollback instructions.

Stop after the requested phase and wait for explicit owner approval.
```

------------------------------------------------------------------------

# D. Detailed phase-by-phase prompts

# PHASE 1 --- Product Foundation, Design System and Premium UI

``` text
Implement PHASE 1: PRODUCT FOUNDATION AND PREMIUM UI SYSTEM.

Act as a Principal Product Designer and Senior Next.js Engineer.

GOAL
Create the visual and technical foundation of KVL Hosting Panel. The result must look like a premium, modern 2026 enterprise SaaS product.

FIRST
Inspect the existing Next.js project, dependencies, routes and configuration. Preserve working code. Propose the route map and design architecture before implementation.

DESIGN SYSTEM
1. Establish KVL brand identity and visual direction.
2. Create semantic color tokens for background, surface, elevated surface, text, muted text, border, primary, secondary, success, warning, danger and information.
3. Create complete light and dark theme tokens. Light is default.
4. Define typography scale, font weights, line heights and readable data typography.
5. Define spacing, layout widths, grid, radii, shadows, borders, focus rings and motion durations.
6. Ensure charts and status colors remain understandable in both themes.
7. Avoid excessive gradients, neon colors, generic AI dashboard styling and decorative glass effects.

APPLICATION SHELL
Build:
- Responsive collapsible sidebar.
- Brand/logo area.
- Role-aware navigation configuration.
- Top bar with breadcrumbs, global search entry, notifications and profile menu.
- Mobile navigation drawer.
- Page title, description and action area.
- Theme selector.
- Keyboard focus and skip-navigation support.

ROLE AREAS AND ROUTES
Create layout shells and route placeholders for:
Super Admin:
- /admin/dashboard
- /admin/servers
- /admin/websites
- /admin/domains
- /admin/databases
- /admin/customers
- /admin/resellers
- /admin/plans
- /admin/subscriptions
- /admin/billing
- /admin/monitoring
- /admin/backups
- /admin/support
- /admin/automation
- /admin/security
- /admin/settings

Hosting Admin:
- /hosting/dashboard
- /hosting/websites
- /hosting/domains
- /hosting/databases
- /hosting/applications
- /hosting/backups
- /hosting/ssl

Customer:
- /customer/dashboard
- /customer/websites
- /customer/domains
- /customer/databases
- /customer/applications
- /customer/backups
- /customer/billing
- /customer/support
- /customer/profile

Reseller:
- /reseller/dashboard
- /reseller/clients
- /reseller/packages
- /reseller/branding
- /reseller/commissions
- /reseller/reports
- /reseller/settings

REUSABLE COMPONENTS
Create:
- Button variants and icon buttons.
- Metric/KPI cards.
- Status indicators and badges.
- Search input and filter bar.
- Data table patterns.
- Pagination.
- Tabs and segmented controls.
- Dropdowns and action menus.
- Modal/dialog and confirmation dialog.
- Sheet/drawer.
- Tooltip and popover.
- Toast/notification.
- Form field patterns.
- Loading skeletons.
- Empty states.
- Error states and retry patterns.
- Permission-denied state.
- Chart container, legend and tooltip patterns.
- Date/time range selector.

DASHBOARD MOCKUPS
Create role-specific dashboard shells with clearly labelled DEMO DATA:
- Super Admin: tenant count, active subscriptions, total websites, server health, revenue sample, recent activity.
- Hosting Admin: websites, domains, PHP/Node apps, disk usage, SSL status.
- Customer: hosting plan, websites, usage, invoices and tickets.
- Reseller: client count, packages, usage, commission sample.

CHARTS AND TABLES
Use charts only for meaningful metrics. Include accessible labels, units, legend, tooltips, time range and no-data state.
Create table patterns with search, sorting, filters, pagination, column actions and responsive behavior.

ANIMATION
Use Framer Motion for subtle page transitions, menus, dialogs, drawer transitions and feedback. Respect prefers-reduced-motion. Avoid constant or distracting animation.

BACKEND FOUNDATION
Do not invent a backend. Keep typed API service boundaries ready for FastAPI. Clearly label mock data and isolate it in demo fixtures.

QUALITY
- Responsive desktop/tablet/mobile.
- Keyboard navigation and visible focus.
- No broken links or dead buttons; if a feature is a placeholder, show a clear not-yet-available state.
- No fake live server data.
- Add component documentation or a small design-system page.
- Run ESLint, TypeScript and production build.

DELIVER
- Implemented routes and layouts.
- Design tokens and theme system.
- Component inventory.
- Screenshots or preview instructions if available.
- Test/build results.
- docs/phase-reports/PHASE-01.md.

Stop. Do not implement Phase 2.
```

# PHASE 2 --- Authentication, Identity and RBAC

``` text
Implement PHASE 2: AUTHENTICATION, IDENTITY AND ROLE-BASED ACCESS CONTROL.

STACK
FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, Next.js and secure session handling.

IDENTITY FEATURES
1. User registration.
2. Login and logout.
3. Email verification.
4. Email OTP flow with expiry, resend cooldown and attempt limits.
5. Password reset.
6. Password change.
7. Optional TOTP two-factor authentication.
8. Session listing and session revocation.
9. Login history and suspicious-login event records.
10. Account lockout/risk throttling with safe recovery.
11. Staff invitation and acceptance flow.
12. User profile and security settings.

SECURITY
- Use Argon2id password hashing.
- Never store plaintext passwords or OTPs.
- Store OTP verification material safely, expire it and rate-limit attempts.
- Use secure, HttpOnly, SameSite cookies where appropriate; evaluate CSRF protections.
- If using access/refresh tokens, rotate refresh tokens and detect reuse.
- Validate issuer, audience, expiry and token purpose.
- Rate-limit login, OTP and password reset endpoints.
- Avoid account enumeration in public authentication responses.
- Keep secrets in environment variables or a secret manager.
- Record security-sensitive events without logging credentials or tokens.

ROLES
- Platform Owner / Super Admin
- Hosting Admin
- Support Staff
- Customer
- Reseller

RBAC
- Define permissions as explicit resources and actions.
- Support role-permission assignments and user overrides only if justified.
- Deny by default.
- Check permissions in FastAPI dependencies/services.
- Frontend navigation may reflect permissions but is not authoritative.
- Prevent privilege escalation and self-assignment of privileged roles.
- Add audit events for role and permission changes.

DATABASE
Create migrations for users, credentials/security metadata, sessions, OTP challenges, roles, permissions, role-permission links, memberships/invitations and audit events as appropriate.
Add indexes, unique constraints and foreign keys.

FRONTEND
Build polished login, registration, OTP, reset password, 2FA setup, session manager, profile/security and staff invitation screens. Include validation, loading, errors, success and rate-limit feedback. Maintain light/dark design.

API
Document endpoints, request/response schemas and error codes in OpenAPI.

TESTS
- Password hashing and verification.
- OTP expiry, reuse and rate limits.
- Session rotation and revocation.
- CSRF/authentication protections as applicable.
- Every role/permission boundary.
- Privilege escalation attempts.
- Unauthenticated and unauthorized API requests.

DELIVER PHASE REPORT AND STOP. Do not implement tenant provisioning yet.
```

# PHASE 3 --- Multi-Tenant SaaS Engine

``` text
Implement PHASE 3: MULTI-TENANT SAAS FOUNDATION.

GOAL
Create the tenant lifecycle and strict data isolation foundation.

TENANT FEATURES
- Create tenant.
- Tenant name, slug, contact and settings.
- Tenant owner.
- Tenant memberships and staff invitations.
- Tenant status: pending, trial, active, suspended, cancelled.
- Tenant plan assignment.
- Tenant quotas and usage limits.
- Tenant configuration and preferences.
- Tenant activity history.
- Tenant suspension/reactivation workflow.
- Tenant export/deactivation policy designed safely.

DATA MODEL
Design tenant-aware tables and relationships. Add tenant_id to tenant-owned resources. Define which records are platform-global and which belong to tenants. Use SQLAlchemy models and Alembic migrations.

ISOLATION
- Every tenant-owned query must be scoped.
- Derive tenant context from authenticated membership, not an arbitrary client-supplied tenant_id.
- Validate membership and role on every request.
- Prevent cross-tenant IDOR.
- Avoid caching data across tenant boundaries.
- Include tenant identity in background jobs and audit context.
- Use PostgreSQL constraints and indexes to support isolation.
- Consider PostgreSQL row-level security only if implemented and tested correctly; do not treat it as a substitute for application authorization.

SAAS LIFECYCLE
- Tenant provisioning states.
- Trial start/end timestamps.
- Plan and quota association.
- Grace period policy as configurable business rules.
- Suspension must not delete customer data.
- Reactivation restores eligible access.
- Cancellation/export/retention policy must be documented.

FRONTEND
- Super Admin tenant list, filters, tenant details and lifecycle actions.
- Tenant settings and member management.
- Clear tenant status and quota displays.
- Confirmation dialogs for suspension/cancellation.
- Customer organization settings where applicable.

TESTS
Create two or more tenants and prove:
- Tenant A cannot read Tenant B records.
- Tenant A cannot update/delete Tenant B records.
- User cannot switch to an unowned tenant by changing request parameters.
- Background jobs cannot cross tenant boundaries.
- Suspended tenant behavior follows documented policy.

DELIVER migrations, APIs, UI, isolation test report and stop.
```

# PHASE 4 --- Restricted Python Server Agent

``` text
Implement PHASE 4: SECURE PYTHON SERVER AGENT.

GOAL
Create a separate Python service that can later run on the KVL iMac hosting server and communicate with the FastAPI control plane.

AGENT CAPABILITIES
- Secure agent enrollment and server registration.
- Server identity and version.
- Heartbeat.
- CPU, memory, disk, network and uptime metrics.
- Nginx, PHP-FPM, MariaDB, Redis and PM2 status checks where available.
- Job polling or authenticated job delivery.
- Operation result reporting.
- Structured logs.
- Health endpoint.
- Graceful shutdown.
- Agent update status reporting.

SECURE OPERATION MODEL
- Define a strict allow-list of operation names.
- Each operation has a Pydantic schema and validation.
- No arbitrary shell command endpoint.
- No customer-provided command strings.
- No unrestricted root API.
- Run as a dedicated low-privilege OS account.
- Privileged actions, if ever required, must use narrowly scoped OS permissions and reviewed helper operations.
- Enforce timeouts, output limits and concurrency limits.
- Authenticate every request.
- Use TLS and server identity.
- Use request timestamps/nonces or equivalent replay protection.
- Rotate/revoke agent credentials.
- Audit every privileged operation.
- Fail closed if authorization cannot be verified.

CONTROL PLANE
- Register server.
- Issue/revoke agent credentials securely.
- Receive heartbeat and metrics.
- Queue approved jobs.
- Track job state: queued, running, succeeded, failed, timed out.
- Store result summaries without secrets.

FRONTEND
Create server registration, agent status, version, last heartbeat and operation history screens. Clearly distinguish connected, stale, disconnected and unknown states.

TESTS
- Invalid agent authentication.
- Replayed request rejection.
- Unauthorized operation rejection.
- Invalid operation schema rejection.
- Timeout handling.
- Output truncation.
- Audit creation.
- No arbitrary command execution path.

Do not connect to or modify the production iMac during this phase. Use a local test agent.
```

# PHASE 5 --- Server Monitoring and Metrics

``` text
Implement PHASE 5: SERVER MONITORING DASHBOARD.

DATA SOURCE
Use authenticated server-agent metrics. Never fabricate live data. Demo fixtures must be labelled.

METRICS
- CPU utilization and load averages.
- Memory total/used/available.
- Swap.
- Disk total/used/free by mount.
- Network throughput and interface state.
- Uptime.
- Service status.
- Agent heartbeat and version.
- Historical metric samples.
- Server availability and health status.

SERVICE HEALTH
Support status checks for Nginx, PHP-FPM, MariaDB, Redis, PM2 and configured application services. Unknown/uninstalled services must show Not configured, not failed.

DASHBOARD
- Server summary cards.
- CPU/RAM/storage/network charts.
- Time-range selector.
- Service health list.
- Recent incidents.
- Threshold configuration.
- Alert history.
- Per-server detail page.

ALERTING
- Configurable warning/critical thresholds.
- Avoid alert storms with cooldown and deduplication.
- Alert on missing heartbeat.
- Record alert lifecycle and acknowledgement.
- Use clear units and timestamps.

PERFORMANCE
- Define metric retention and sampling policies.
- Avoid excessive polling.
- Paginate incidents and history.
- Measure API/database load.

TESTS
- Missing metrics vs zero.
- Stale heartbeat.
- Threshold crossings.
- Duplicate alert suppression.
- Time-series range and timezone behavior.

Deliver monitoring API, UI, docs and stop.
```

# PHASE 6 --- Website and Domain Management

``` text
Implement PHASE 6: WEBSITE AND DOMAIN MANAGEMENT.

WEBSITE LIFECYCLE
- Create website record.
- Assign tenant/customer.
- Assign hosting plan and server.
- Domain and subdomain mapping.
- Document root.
- Runtime type and version.
- Website status.
- Suspend/reactivate.
- Archive workflow.
- Usage summary.
- Activity history.

DOMAIN FEATURES
- Add domain.
- Verify domain ownership.
- Add/remove alias or subdomain.
- Show DNS verification state.
- Track expiry metadata where available.
- Domain-to-website mapping.
- Prevent duplicate ownership conflicts.

NGINX PROVISIONING
- Generate configuration from validated templates.
- Restrict allowed document-root paths.
- Validate hostnames and prevent injection.
- Write proposed configuration to a temporary location.
- Back up existing config before approved replacement.
- Run Nginx configuration test.
- Reload only after successful validation and explicit authorized operation.
- Record job and result.
- Provide rollback path.

CUSTOMER UI
- Website list and details.
- Domain list.
- Add website/domain wizard.
- Status, runtime and usage panels.
- Clear provisioning progress.
- Error and retry state.

SECURITY
- All infrastructure changes go through restricted agent.
- No arbitrary Nginx text from customers.
- No overwriting existing production configs.
- No production DNS changes without explicit owner approval.
- Tenant ownership must be checked on every action.

TESTS
- Hostname validation.
- Path traversal/injection attempts.
- Duplicate domain conflicts.
- Tenant isolation.
- Config generation and validation failures.
- Rollback behavior.

Deliver and stop.
```

# PHASE 7 --- Application and Runtime Manager

``` text
Implement PHASE 7: APPLICATION AND RUNTIME MANAGER.

SUPPORTED RUNTIMES
- PHP via PHP-FPM.
- WordPress deployment templates.
- Node.js applications managed by PM2.
- Python applications with isolated virtual environments and controlled process management.

FEATURES
- Create application.
- Select runtime/version from installed supported versions.
- Link domain and document root.
- Git repository configuration.
- Deployment branch and deployment history.
- Secure environment variable management.
- Start, stop, restart and status.
- Health-check URL.
- Application logs with pagination and redaction.
- Deployment failure details.
- Rollback to previous known-good deployment where safe.
- Resource usage and quotas.

DEPLOYMENT
- Use controlled deployment directories.
- Validate Git source and branch.
- Keep secrets out of logs and build output.
- Use release directories or another atomic deployment strategy where feasible.
- Validate app health before switching traffic.
- Keep previous release for rollback.
- Never run arbitrary user shell commands as root.

FRONTEND
- Application inventory.
- Runtime selection wizard.
- Deployment history timeline.
- Environment variable editor with masked values.
- Process status and actions.
- Logs viewer.
- Health status and resource charts.

TESTS
- Runtime compatibility.
- Unauthorized process control.
- Secret redaction.
- Deployment failure and rollback.
- Tenant isolation.
- Resource quota enforcement.

Deliver and stop.
```

# PHASE 8 --- Database Management

``` text
Implement PHASE 8: CUSTOMER DATABASE MANAGEMENT.

DATABASE OPERATIONS
- Provision customer database.
- Create database user.
- Assign least-privilege grants.
- List databases owned by current tenant.
- Display database size and status.
- Rotate credentials.
- Show connection information securely.
- Backup and restore request workflow.
- Operation history.

SEPARATION
The SaaS control-plane PostgreSQL database must be separate from customer website databases. Never expose control-plane credentials to customers.

SECURITY
- Never display administrative DB credentials.
- Do not store plaintext customer passwords.
- Scope every database to tenant and customer ownership.
- Prevent access to other tenants' DBs.
- Require confirmation and verified backup before destructive operations.
- Restrict database operations through approved agent capabilities.
- Redact secrets from logs.

FRONTEND
- Database list.
- Create database wizard.
- User/permission screen.
- Usage and connection details.
- Credential rotation.
- Backup/restore history.
- Confirmation and failure states.

TESTS
- Tenant database isolation.
- Least-privilege grants.
- Credential rotation.
- Provisioning failure cleanup.
- Restore integrity.
- Destructive action safeguards.

Deliver and stop.
```

# PHASE 9 --- SSL, DNS and Cloudflare

``` text
Implement PHASE 9: SSL, DNS AND CLOUDFLARE INTEGRATION.

SSL
- Request certificate.
- Validate domain prerequisites.
- Track pending/issued/renewing/expired/failed states.
- Automatic renewal job design.
- Certificate expiry dashboard.
- Renewal failure alerts.
- Certificate metadata only; never expose private keys.

DNS
- Display records.
- Add/edit/delete only with explicit authorized workflow.
- Validate record types and values.
- Record before/after values.
- Provide rollback instructions.
- Verify changes after propagation.

CLOUDFLARE
- Use scoped API tokens with least privilege.
- Encrypt token storage.
- Support zone discovery only for authorized accounts.
- Display proxy status and relevant DNS metadata.
- Handle API errors and rate limits.
- Never request or store global account keys when scoped tokens suffice.

CRITICAL PRESERVATION
Never modify unrelated DNS records. Preserve MX, SPF, DKIM, DMARC and existing subdomains. Production DNS changes require explicit owner approval.

FRONTEND
- SSL overview and certificate detail.
- Expiry status.
- DNS record table and editor.
- Cloudflare connection status.
- Change preview and confirmation.
- Audit history.

TESTS
- Invalid domain.
- Certificate failure/renewal.
- Token permission failure.
- DNS validation.
- Preservation of unrelated records.
- Tenant ownership and audit events.

Deliver and stop.
```

# PHASE 10 --- Secure File Manager

``` text
Implement PHASE 10: TENANT-SCOPED FILE MANAGER.

FEATURES
- Browse files and folders within assigned website root.
- Upload/download.
- Create folder.
- Rename and move.
- Archive and extract supported formats.
- File metadata and size.
- Search and pagination.
- Storage quota.
- Permission display.
- Safe recovery/trash workflow if supported.
- Audit trail.

SECURITY
- Strictly resolve paths within tenant's assigned root.
- Prevent path traversal.
- Prevent symlink escape.
- Prevent cross-tenant and system-file access.
- Restrict file types and upload sizes as policy requires.
- Avoid unsafe archive extraction paths.
- Never expose unrestricted host filesystem.
- Require confirmation for destructive operations.
- Use least-privilege agent operations.

FRONTEND
- Breadcrumb navigation.
- File/folder table and grid view if useful.
- Upload progress.
- File details panel.
- Context actions.
- Quota meter.
- Empty, error and permission states.

TESTS
- `../` traversal.
- Encoded traversal.
- Symlink escape.
- Malicious archive paths.
- Cross-tenant access.
- Oversized upload.
- Unauthorized deletion.

Deliver and stop.
```

# PHASE 11 --- Backup and Disaster Recovery

``` text
Implement PHASE 11: BACKUP AND DISASTER RECOVERY.

BACKUP TYPES
- Website files.
- Customer databases.
- Website configuration.
- SaaS configuration metadata where appropriate.
- Full and incremental strategies only where technically justified.

FEATURES
- Backup schedules.
- Manual backup request.
- Retention policies.
- Backup inventory.
- Size and timestamp.
- Checksums.
- Verification status.
- Restore preview.
- Restore to isolated location where possible.
- Approved restore workflow.
- Backup failure alerts.
- Off-site backup destination support.
- Backup job history.

SAFETY
- Verify target disk identity and available space.
- Never format or repartition disks.
- Never delete existing backups as part of setup.
- Do not prune backups until retention policy and recovery points are confirmed.
- Verify checksum and restore integrity.
- Require explicit confirmation for overwrite/restore.
- Preserve source data during restore testing.
- Encrypt backups where appropriate and protect encryption keys.

FRONTEND
- Backup overview.
- Schedule editor.
- Retention settings.
- Backup detail.
- Restore wizard with source, target and impact preview.
- Verification and failure state.

TESTS
- Backup completion.
- Checksum mismatch.
- Disk unavailable/full.
- Restore integrity.
- Retention simulation without actual deletion.
- Unauthorized restore.
- Recovery documentation review.

Document RPO/RTO assumptions and recovery runbook. Deliver and stop.
```

# PHASE 12 --- Customer Portal

``` text
Implement PHASE 12: CUSTOMER PORTAL.

CUSTOMER HOME
- Hosting plan and status.
- Website count.
- Domain count.
- Storage and bandwidth usage.
- SSL health.
- Recent activity.
- Invoices and payment summary.
- Open support tickets.

CUSTOMER MODULES
- Websites and application status.
- Domains and DNS status (only permitted functions).
- Databases and credentials workflow.
- SSL status.
- File manager.
- Backup history and restore request.
- Usage and quota.
- Plans and subscription.
- Invoices and payments.
- Support tickets.
- Notifications.
- Profile, password, 2FA and sessions.

UX
- Clear onboarding.
- Helpful empty states.
- Search and filters.
- Mobile-friendly account navigation.
- Contextual help and knowledge base links.
- Accessible charts and tables.

SECURITY
- Customer can only see and operate their own resources.
- Recheck ownership on every API call.
- Never rely on frontend filtering for isolation.
- Sensitive credentials must be masked and revealed only through authorized secure flow.

TESTS
Test every customer route and API with two separate tenants and users.

Deliver and stop.
```

# PHASE 13 --- Reseller and Agency Portal

``` text
Implement PHASE 13: RESELLER AND AGENCY PORTAL.

RESELLER LIFECYCLE
- Application/registration.
- Super Admin approval.
- Reseller profile and business details.
- Status and limits.
- Reseller staff/members if supported.

CLIENT MANAGEMENT
- Create and manage clients.
- Assign packages.
- Track hosting resources.
- Client status and renewals.
- Client-level support context.
- Export permitted reports.

PACKAGE AND PRICING
- Create reseller packages from platform-approved capabilities.
- Set reseller retail price and margin.
- Track quotas and usage.
- Prevent packages exceeding platform allocation.

WHITE LABEL
- Logo and brand colors.
- Custom portal branding.
- Custom domain readiness and verification workflow.
- Email branding configuration where supported.
- Clearly identify platform-owned infrastructure and legal disclosures where needed.

COMMISSIONS
- Commission rules.
- Ledger entries.
- Pending/approved/paid status.
- Reconciliation and export.
- No fake commission balances.

SECURITY
- Reseller sees only their own clients.
- Reseller cannot access other resellers' data.
- Reseller cannot access privileged server operations.
- All client ownership is enforced in backend services.

TESTS
- Cross-reseller access.
- Package quota enforcement.
- Commission ledger consistency.
- White-label tenant boundary.
- Unauthorized server operation rejection.

Deliver and stop.
```

# PHASE 14 --- Plans, Subscriptions, Billing and Payments

``` text
Implement PHASE 14: SAAS PLANS, SUBSCRIPTIONS AND BILLING.

PRODUCT CATALOG
- Hosting plans.
- Resource limits and feature entitlements.
- Add-ons.
- Monthly/yearly billing intervals.
- Trial policy.
- Upgrade/downgrade policy.
- Plan versioning so existing subscriptions retain correct historical terms.

SUBSCRIPTIONS
- Start trial.
- Activate subscription.
- Renew.
- Upgrade/downgrade with defined proration rules.
- Cancel at period end or immediate cancellation per policy.
- Grace period.
- Suspend/reactivate based on verified billing state.
- Subscription history.

BILLING
- Invoices.
- Credit notes/refund records.
- Coupons.
- Tax/GST-ready fields and invoice numbering configuration.
- Payment history.
- Failed payment handling.
- Revenue and MRR/ARR-style reports with clearly defined formulas.

RAZORPAY
- Backend order creation.
- Signed webhook verification.
- Idempotent event processing.
- Payment reconciliation.
- Refund workflow.
- Webhook retry handling.
- Store provider references safely.
- Never mark paid from client redirect alone.

FRONTEND
- Plan catalog.
- Subscription detail.
- Checkout flow.
- Invoice list/detail.
- Payment history.
- Coupon entry.
- Admin billing overview.
- Failed payment and renewal states.

SECURITY/TESTS
- Webhook signature tests.
- Duplicate webhook idempotency.
- Payment pending/failed/success.
- Refund consistency.
- Tenant billing isolation.
- Authorization on invoice downloads.

Do not process real payments until test mode and owner approval. Deliver and stop.
```

# PHASE 15 --- n8n Automation

``` text
Implement PHASE 15: N8N AUTOMATION INTEGRATION.

ARCHITECTURE
n8n is the workflow engine only. FastAPI/PostgreSQL remain the source of truth for tenants, users, billing, permissions and hosting resources.

WORKFLOWS
1. New tenant onboarding.
2. Customer welcome and verification notifications.
3. Hosting activation only after verified payment.
4. Invoice due reminders.
5. Subscription renewal reminders.
6. SSL expiry alerts.
7. Backup success/failure alerts.
8. Server heartbeat/health alerts.
9. Support ticket updates.
10. Daily/weekly operational reports.
11. Admin approval reminders.
12. Failed workflow escalation.

INTEGRATION
- Authenticated webhook endpoints.
- Signed requests or shared-secret/HMAC validation.
- Idempotency keys.
- Retry and backoff.
- Dead-letter/error handling.
- Workflow execution correlation IDs.
- Audit and execution status.
- Rate limits.
- Credential isolation.

SECURITY
- Protect n8n UI with authentication and network restrictions.
- Do not expose n8n publicly without a security design.
- Do not place secrets in workflow exports or Git.
- Restrict workflow permissions.
- Validate inbound event schemas.
- Do not allow a workflow to bypass backend authorization.

FRONTEND
- Workflow catalog/status.
- Integration connection status.
- Recent executions.
- Failure detail and retry where safe.
- Admin-only configuration.

TESTS
- Invalid webhook signature.
- Duplicate event.
- Retry behavior.
- Workflow failure.
- Payment activation only after verified payment.
- No cross-tenant notification leakage.

Deliver and stop.
```

# PHASE 16 --- Support and Business Management

``` text
Implement PHASE 16: SUPPORT AND BUSINESS MANAGEMENT.

TICKETS
- Ticket creation.
- Categories and departments.
- Priority and status.
- Assignment and reassignment.
- Customer replies.
- Internal notes.
- Attachments with safe file validation.
- Ticket history.
- SLA timers and escalation.
- Search, filters and reports.

KNOWLEDGE BASE
- Categories.
- Articles.
- Draft/published states.
- Search.
- Related articles.
- Helpful/not-helpful feedback.

ANNOUNCEMENTS
- Platform notices.
- Scheduled publication.
- Audience targeting by authorized tenant/role.
- Read/acknowledgement state where appropriate.

STAFF MANAGEMENT
- Staff accounts.
- Department membership.
- Scoped permissions.
- Activity history.
- Workload views.

BUSINESS OPERATIONS
- Customer timeline.
- Support performance reports.
- Ticket aging and SLA report.
- Export authorized records.

SECURITY
- Support staff do not receive infrastructure privileges by default.
- Internal notes must never appear to customers.
- Tenant data and attachments must be isolated.
- Audit sensitive actions.

TESTS
- Internal note visibility.
- Attachment access.
- SLA timing.
- Staff permissions.
- Tenant ticket isolation.

Deliver and stop.
```

# PHASE 17 --- Security, Monitoring and Developer API

``` text
Implement PHASE 17: SECURITY, MONITORING AND DEVELOPER PLATFORM.

SECURITY CONTROLS
- Scoped API keys.
- API key creation, display-once, rotation and revocation.
- Webhook subscriptions and signing secrets.
- Rate limits and abuse controls.
- Audit trails.
- Session revocation.
- Security event records.
- Secret encryption and access controls.
- Dependency vulnerability scanning.
- Secure headers and CORS review.
- Backup/restore operational monitoring.
- Incident alerting.

API PLATFORM
- Versioned API routes.
- OpenAPI documentation.
- API scopes.
- Usage limits.
- Request IDs.
- Error format.
- Webhook event catalog.
- Idempotency support where required.

OBSERVABILITY
- Structured logs.
- Correlation IDs.
- Error reporting.
- Health/readiness checks.
- Metrics endpoints protected appropriately.
- Alert routing and acknowledgement.
- Sensitive data redaction.

SECURITY TESTING
- Authentication and authorization review.
- Tenant isolation.
- IDOR.
- Rate limiting.
- Secret leakage.
- Webhook forgery/replay.
- SSRF and unsafe URL handling where relevant.
- File/path traversal.
- Dependency review.

Do not claim SOC 2, ISO 27001, PCI DSS or other compliance certification without formal independent assessment. Deliver a gap list and remediation plan.

Deliver and stop.
```

# PHASE 18 --- QA, Staging, Deployment and SaaS Launch

``` text
Implement PHASE 18: PRODUCTION READINESS, STAGED DEPLOYMENT AND LAUNCH.

TEST PLAN
- Frontend unit/component tests.
- Backend unit tests.
- API integration tests.
- End-to-end Playwright tests.
- Authentication and RBAC tests.
- Tenant isolation tests.
- Server agent security tests.
- Website provisioning tests in isolated test environment.
- Runtime deployment/rollback tests.
- Database backup/restore drills.
- SSL staging tests.
- Payment provider test-mode webhooks.
- n8n workflow failure/retry tests.
- Load and resource tests.
- Accessibility checks.
- Browser/device responsive checks.

STAGING
- Separate staging environment.
- Test tenant and test users.
- Test hostname.
- Non-production payment keys.
- Test database and isolated storage.
- Monitoring and logs.
- Backup before any migration.
- Deployment checklist and rollback procedure.

RELEASE ENGINEERING
- Pin and review dependencies.
- Build reproducibly.
- Run migrations as controlled deployment steps.
- Health-check after deployment.
- Verify background jobs and agent heartbeat.
- Confirm backup recovery path.
- Record release version and commit.
- Keep previous release available for rollback where feasible.

PRODUCTION SAFETY
- Do not alter production DNS, domains, customer files or databases without explicit owner approval.
- Do not deploy directly to the production iMac as part of an automated unapproved action.
- Prepare a deployment plan and wait for owner approval.
- Do not remove existing services or data.

FINAL DELIVERABLES
- Test summary.
- Security review.
- Performance/resource report.
- Known limitations.
- Deployment runbook.
- Backup and restore runbook.
- Rollback plan.
- Monitoring/incident guide.
- SaaS launch checklist.
- Final architecture diagram and documentation.

Stop and request approval before production launch.
```

------------------------------------------------------------------------

# E. Phase completion report template

Claude must create one report per phase at
`docs/phase-reports/PHASE-XX.md`.

``` markdown
# KVL Hosting Panel — Phase XX Report

## 1. Phase summary
## 2. Requirements completed
## 3. Routes and screens
## 4. Components created/updated
## 5. Backend endpoints
## 6. Database models and migrations
## 7. Integrations
## 8. Authorization and tenant-isolation review
## 9. Security review
## 10. Tests executed
## 11. Lint/type/build results
## 12. Resource impact
## 13. Known issues and limitations
## 14. Rollback instructions
## 15. Next phase prerequisites
## 16. Owner approval status
```

# F. Recommended branch and release strategy

-   `main`: approved stable code.
-   `develop`: integration branch.
-   `feature/phase-01-ui-foundation`: phase-specific work.
-   `feature/phase-02-auth-rbac`: next phase.
-   Use pull requests or reviewed merges.
-   Tag approved releases.
-   Never commit `.env`, secrets, database dumps or customer data.

# G. Final project rule

KVL Hosting Panel must be visually premium, but it must also be secure,
genuinely functional, testable and recoverable.

**Build one phase at a time. Complete its tests and documentation. Get
owner approval. Then move to the next phase.**
