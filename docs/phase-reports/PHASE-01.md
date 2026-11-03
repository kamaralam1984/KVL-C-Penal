# KVL Hosting Panel — Phase 01 Report

## 1. Phase summary
Product foundation: KVL design tokens (light default + complete dark), responsive application shell,
role-aware navigation, reusable component library, four role dashboards with clearly labelled demo data,
and route placeholders for every planned module. No backend, authentication or live data exists yet.

## 2. Requirements completed
- Design tokens: surfaces, text, borders, brand, semantic colours, chart palette, radii, shadows, focus ring, motion, layout (`frontend/src/app/globals.css`).
- Light default, full dark theme, system option (next-themes, class strategy).
- Shell: collapsible sidebar (persisted), mobile drawer, top bar, breadcrumbs, Ctrl/Cmd+K search (navigation only), notifications (demo), profile menu, theme selector, skip link, page header.
- 39 role routes from the spec, all reachable from navigation. Modules not yet built render an honest "not available yet" state naming the build phase.
- Components: button/icon button, KPI card, badges/status/demo badge, search + filter bar, data table, pagination, tabs/segmented, menus, dialog + confirm, sheet/drawer, tooltip, popover, toast, form fields, skeletons, empty/error/permission-denied/not-yet-available states, chart frame/legend/tooltip, date-range selector.
- Framer Motion: page transition, toasts; `MotionConfig reducedMotion="user"` plus a global CSS reduced-motion rule.
- Typed API seam (`src/lib/api`) returning `{ data, source: "demo" | "live" }`; fixtures isolated in `src/lib/demo`.
- Design-system page at `/design-system`.

## 3. Routes and screens
`/` (role chooser), `/design-system`, and per role: `/admin/*` (16), `/hosting/*` (7), `/customer/*` (9), `/reseller/*` (7).
Bespoke screens: the four `/…/dashboard` pages. Everything else is a placeholder route.

## 4. Components created
`src/components/ui/*`, `src/components/charts/*`, `src/components/shell/*`, `src/components/dashboard/*`, `src/components/design-system/demo.tsx`.

## 5. Backend endpoints
None (by design).

## 6. Database models and migrations
None.

## 7. Integrations
None. `NEXT_PUBLIC_API_BASE_URL` is read by `src/lib/api/client.ts` but nothing calls it yet.

## 8. Authorization and tenant-isolation review
Not applicable: there is no authentication or data. All areas are openly previewable; the landing page and profile menu say so.
Role navigation is configuration only and is explicitly **not** a security control (backend enforcement arrives in Phase 2/3).

## 9. Security review
No secrets, no external calls, no user input persisted. Dependencies: next, react, framer-motion, lucide-react, recharts, radix-ui, next-themes, clsx, tailwind-merge.
`npm audit` reports moderate advisories inherited from the scaffold toolchain; review and pin in Phase 17/18.

## 10. Tests executed
- Manual browser checks via Playwright against the dev server (12/12 passed): Ctrl+K navigation, placeholder state, theme switch, sidebar persistence, skip link as first tab stop, table search, pagination, confirm dialog open/Escape, mobile drawer, drawer navigation, no horizontal overflow at 390px.
- No console errors on 9 captured pages (light, dark, mobile).
- Automated unit/e2e suites are not written yet (Phase 18 scope); these checks were ad hoc.

## 11. Lint/type/build results
`tsc --noEmit` clean · `eslint` clean · `next build` succeeded, 48 static pages generated.

## 12. Resource impact
Dev server only (Next.js). No databases, queues or background services started.

## 13. Known issues and limitations
- Global search covers navigation items only.
- Notifications and all dashboard figures are demo fixtures.
- Chart range selector slices demo data (7d / 14d); real ranges need the metrics API.
- Table state (search, sort, page) is client-side only; server pagination comes with the APIs.
- No automated accessibility audit has been run yet; keyboard, focus and ARIA basics were checked manually.
- Colour contrast was chosen by hand and spot-checked visually, not measured with a tool.

## 14. Rollback instructions
The project is not under Git yet. To roll back, delete `frontend/` (nothing outside it was changed other than `docs/`).

## 15. Next phase prerequisites
Phase 2 needs: backend project scaffold (FastAPI), PostgreSQL available for development, decision on session strategy (cookie vs token),
and an email delivery option for verification/OTP in development.

## 16. Owner approval status
Pending owner review.
