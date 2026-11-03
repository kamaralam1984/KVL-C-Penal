# KVL Hosting Panel — Phase 10 Report (Tenant-scoped File Manager)

## 1. Phase summary
A file manager whose every action is an allow-listed agent operation confined to the website root that the control plane looked up for the tenant.
The API never touches the filesystem and never accepts a root from the client. Frontend for `hosting/files` and `customer/files`.

## 2. Requirements completed
- Browse, stat (with permission display), download (size-capped, single-use), chunked upload (size + type policy, quota reservation), mkdir, rename/move, delete-to-trash, trash list, restore, purge, zip/tar.gz archive, safe extract, search with pagination, quota usage, audit trail.
- Path safety (agent `kvl_agent/safe_paths.py`): rejects `..` in raw and repeatedly URL-decoded form (`%2e%2e`, `%252e%252e`, `..%2f`), NUL bytes, backslashes, control chars, absolute and drive paths; walks every component with `lstat` and refuses any symlink on the way (file or directory); final path re-checked against the root; files opened with `O_NOFOLLOW`; the reserved `.kvl-trash` folder is not addressable. The control plane repeats the same checks before queueing (422, no job).
- Archives: member names validated before anything is written (absolute, `..`, backslash, NUL, drive letters); symlink, hardlink, device and fifo members rejected; encrypted zips refused; limits for total size, per-file size, file count and compression ratio (default 1 GiB / 512 MiB / 10 000 / 100:1); declared sizes are not trusted (streaming counters); nothing is written when validation fails and partial output is rolled back; existing files are not overwritten unless asked; extraction never writes through an existing symlink.
- Destructive operations: delete requires `confirm: true` (agent schema is `Literal[True]`) and is recoverable (trash); purge requires confirm and either one trash id or `empty`.
- Quota: `disk_mb` is reserved at upload start via `consume_quota` (409 `quota_exceeded`), released if an upload job fails, charged after archive/extract (`bytes_added`) and released on purge.

## 3. Routes and screens
`/hosting/files`, `/customer/files`: site selector, breadcrumbs, list/grid, search with paging, upload progress, details sheet with permissions, context actions, trash tab, activity tab, quota meter, empty/error/permission/not-configured states.

## 4. Components created/updated
`frontend/src/components/files/{api.ts,files-page.tsx,entry-views.tsx,details-sheet.tsx,quota-meter.tsx}` and route files `app/hosting/files/page.tsx`, `app/customer/files/page.tsx`.

## 5. Backend endpoints (`/api/v1/files`)
`GET /sites`, `GET /quota`, `GET /history`, `GET /jobs/{id}`, `GET /jobs/{id}/download`; `POST /{site}/` `list|stat|read|search|trash/list` (files:read), `upload|mkdir|rename|restore|archive|extract` (files:write), `delete|purge` (files:delete). Actions return 202 with an operation view; the UI polls `/jobs/{id}`.
Agent operations (`agent/kvl_agent/operations/files.py`, mirrored in `backend/app/ops/files.py`): `files.list|stat|read|usage|write|mkdir|rename|delete|trash_list|restore|purge|archive|extract|search`.

## 6. Database models and migrations
`file_operations` (`app/models/files.py`) plus the shared `resource_refs` registry (Phase 9). No migration generated.

## 7. Integrations
Agent daemon (job queue). No external services.

## 8. Authorization and tenant-isolation review
`files:read|write|delete` (customer and hosting_admin/super_admin hold them; support/reseller do not). Site ownership via `owns_resource(db, tenant_id, "website", ref)`; a site or operation id belonging to another tenant is 404 for reads, writes and downloads; the root is looked up from the registry (a `root` in the request body is ignored). Platform staff must pass `tenant_id` and the site must belong to it. Suspended tenants are read-only through the existing `require()` rules.

## 9. Security review
Defence in depth (control plane and agent both validate). TOCTOU between check and use is narrowed with `O_NOFOLLOW` and a re-resolve before the final move but not eliminated; the sandbox user should own only site roots (agent hardening, Phase 4). Upload content type is not sniffed; the type policy is an extension deny-list (`.exe .dll .scr .msi .com .bat .cmd .so .dylib .jar .apk`). Scripts such as `.php` are allowed because sites need them; malware scanning is out of scope. Download uses `attachment`, `nosniff`, a sanitized file name, and the content is removed from the job row after delivery. Job views never include file content.

## 10. Tests executed
`agent/tests/test_files_ops.py` (48), `backend/tests/test_files.py` (24); all pass. Covered: `../`, encoded/double-encoded, NUL, backslash, absolute, root escape, file and directory symlink escapes (created in the sandbox) for read/list/mkdir/write/search/archive, symlink delete removes only the link, zip-slip, absolute members, zip and tar symlink/hardlink members, write-through-existing-symlink, zip bomb (ratio, size, count), lying headers, no-overwrite extraction, oversized upload, blocked type, quota exceed/release, cross-tenant 404 on every verb, unauthorized and unconfirmed deletion, anonymous 401, trash/restore/purge with audit, single-use download, platform scoping, missing server/location.

## 11. Lint/type/build results
Backend and agent tests green. Frontend: `tsc --noEmit` clean for these files at last check; eslint required one file-level `set-state-in-effect` allowance in `files-page.tsx` (data-fetching effects). `next build` not run.

## 12. Resource impact
Searches scan at most 50 000 entries; list is paginated (<= 500); archive/extract bounded by size limits; uploads are chunked (512 KiB) through the job queue, so throughput is modest by design.

## 13. Known issues and limitations
- Upload size is capped at 10 MB because content travels base64 inside job parameters; bigger files need a direct agent transfer channel (not built). Downloads are capped at 2 MiB for the same reason.
- Every action is asynchronous: with no agent connected jobs stay `queued` and the UI says the agent has not responded.
- Quota is approximate (MB granularity, overwrite counts twice, direct edits outside the panel are not seen); `files.usage` exists for reconciliation but nothing schedules it.
- Trash is per site under `.kvl-trash` and counts against disk usage until purged; no auto-expiry.
- Edit-in-browser, permissions editing (chmod) and ownership changes are not implemented.
- Ownership relies on the interim `resource_refs` registry until Phase 6 websites call `register_resolver("website", ...)` and supply `root_rel`.

## 14. Rollback instructions
Revert the files above and drop `file_operations`. Site data is never modified by removal of the panel code; trash folders (`<site>/.kvl-trash`) can be deleted by hand.

## 15. Next phase prerequisites
Agent daemon running jobs; website registration with `root_rel` (e.g. `sites/<tenant>/<site>/public` under the agent data dir); `disk_mb` quota rows from tenant provisioning.

## 16. Owner approval status
Pending owner review.
