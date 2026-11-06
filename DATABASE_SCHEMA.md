# DATABASE_SCHEMA — current schema (87 tables)

Source of truth: SQLAlchemy models in `backend/app/models/*.py`; migrations in `backend/alembic/versions/` (`0682e00cdd30` identity/tenants → `041f68a04c4d` site usage → `b8bb88d19ff8` modules 4–17). Table list taken from `Base.metadata` on 2026-11-06. Apply migrations only with `alembic upgrade head` after a backup — never at app start.

| Domain | Tables |
|---|---|
| Identity/RBAC | users, auth_sessions, login_events, otp_challenges, invitations, roles, permissions, role_permissions, memberships, api_keys |
| Tenants | tenants, tenant_quotas, tenant_activity, resource_refs, platform_settings, audit_events |
| Servers/agent | servers, server_runtimes, service_statuses, agent_credentials, agent_enrollment_tokens, agent_jobs, agent_nonces |
| Hosting | websites, domains, dns_records, dns_changes, cloudflare_connections, certificates, applications, app_deployments, app_env_vars |
| Files/databases | file_operations, customer_databases, database_users, database_backup_requests |
| Backups | backup_destinations, backup_schedules, backup_records, restore_requests, retention_policies |
| Monitoring | metric_samples, site_usage_samples, alerts, alert_thresholds, resource_events, security_events, security_alert_routes |
| Billing | billing_plans, billing_plan_versions, billing_addons, billing_profiles, subscriptions, subscription_events, invoices, invoice_sequences, credit_notes, payments, refunds, payment_webhook_events, coupons, coupon_redemptions, idempotency_records |
| Support/content | tickets, ticket_messages, ticket_events, ticket_attachments, support_departments, support_department_members, kb_articles, kb_categories, kb_feedback, announcements, announcement_reads |
| Resellers | resellers, reseller_clients, reseller_packages, reseller_domains, commission_rules, commission_entries |
| Automation/integration | automation_executions, automation_inbound, automation_outbox, automation_workflow_state, webhook_endpoints, webhook_deliveries, email_outbox |

For column-level detail, read the model files; this file intentionally lists only what was verified from code.
