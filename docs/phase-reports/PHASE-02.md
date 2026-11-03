# KVL Hosting Panel — Phase 02 Report: Authentication, Identity and RBAC

## 1. Summary
Register/login/logout, email verification, email-OTP sign-in, password reset/change, optional TOTP 2FA, session list/revoke, login history with suspicious-login flag, lockout, staff invitations, and a deny-by-default RBAC with an audit trail.

## 2. Requirements completed
Argon2id hashing (RFC 9106 parameters; cheap parameters only when `ENV=test`); OTPs stored as keyed HMAC, 10-minute expiry, 30-second resend cooldown, 5 attempts, single use; HttpOnly+SameSite=Lax cookies; short-lived access JWT (iss/aud/exp/purpose checked) + opaque rotating refresh token with reuse detection that revokes the whole family; double-submit CSRF token; per-IP and per-account rate limits (Redis, in-memory fallback); uniform responses against account enumeration (including constant-time dummy hash); TOTP secrets encrypted at rest and shown once; OTP login refused for 2FA accounts so it cannot bypass the second factor.

## 3. Routes and screens
`/login`, `/register`, `/verify-email`, `/forgot-password`, `/reset-password`, `/accept-invite`, `/account` (profile, password, 2FA with QR, sessions, sign-in activity, developer keys).

## 5. Endpoints
`/api/v1/auth/*` (register, verify-email, otp/request, login, otp/login, refresh, logout, me, switch-membership, password/*, 2fa/*, sessions, login-history, invitations/accept), `/rbac/*`, `/users`, `/staff/invitations`, `/audit`.

## 6. Models / migrations
users, roles, permissions, role_permissions, memberships, invitations, auth_sessions, otp_challenges, login_events, audit_events, email_outbox. Migrations: `0682e00cdd30`, `b8bb88d19ff8`.

## 8. Authorization review
Permissions are `resource:action` and enforced in FastAPI dependencies. Privileged roles can only be granted by the platform owner; self-assignment, removing your own role and removing the last owner are refused; role-permission edits are owner-only, validated against the catalog, scope-checked and audited.

## 10. Tests
`tests/test_auth.py` (23), `tests/test_rbac_tenants.py` (includes every role boundary, privilege-escalation attempts, invitations). Playwright: register→verify→sign-in→sign-out, wrong password, anonymous redirect, role area gating, 2FA setup.

## 13. Known limitations
No SMTP transport is configured: codes and invitations go to the `email_outbox` table; in dev the UI shows a "Show code" helper that is hidden in production. WebAuthn/passkeys are not implemented.

## 16. Owner approval
Pending.
