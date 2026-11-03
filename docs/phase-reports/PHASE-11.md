# KVL Hosting Panel — Phase 11 Report (Backup and Disaster Recovery)

## 1. Phase summary
Backups of website files, staged database dumps, website configuration and SaaS configuration metadata with schedules, manual requests, inventory with sha256 and verification, retention *simulation*, a guarded prune, isolated-by-default restore with an approval workflow, failure alerts, an off-site destination model and agent operations that check the disk before writing. Runbook: `docs/runbooks/backup-and-recovery.md`.

## 2. Requirements completed
- Types: `website_files`, `database` (archives a registered staged dump), `website_config`, `saas_config` (tenant/quotas/resource list JSON, no secrets). Full backups only (incremental deliberately not faked; see runbook).
- Schedules: five-field cron with validation (ranges, steps, lists; at most 4 runs per hour), next-run computation, `sweep_schedules()` for a periodic caller, failures alert.
- Manual backup request; inventory with size, timestamps, sha256, entry count, encryption flag + key fingerprint, verification status, "confirmed recovery point" (= completed and verified); job history.
- Verification: automatic sha256 verification after completion, manual and `deep` verification (reads every member / authenticates every encrypted chunk); mismatch -> `mismatch`, audit, queued email, restore blocked.
- Retention: policy objects (keep_last/daily/weekly/monthly, min_keep); `POST /backups/retention/simulate` reports would-keep/would-prune with reasons and frees bytes, **deletes nothing and queues nothing**; prune is a separate `backups:manage` call needing `confirm`, the typed word `PRUNE`, a verified recovery point in the keep set, and the agent re-verifies that point's checksum before deleting anything.
- Restore: preview with impact (target, overwrite flag, previous data kept, approval requirement, typed phrase); default isolated restore; overwrite only for single-root website backups, requires a verified backup, `confirm`, `confirm_overwrite`, the typed phrase `RESTORE <resource>` and approval by a different person with `backups:manage`; agent moves old data aside (`restores/pre-<id>`), checks the checksum first, checks restored file count against the manifest.
- Safety: destination root must exist (never created/formatted/mounted), optional disk identity marker, free-space check before writing, ENOSPC cleanup of partial files, `.partial` + rename, existing backups never deleted by setup.
- Encryption: AES-256-GCM chunked (cryptography), key generated and kept on the agent host (`backups.init_key`, 0600, never overwritten), only a fingerprint leaves it; missing key -> explicit `key_missing` failure.
- Alerts: backup failure, checksum mismatch, verification failure, restore failure, schedule failure -> audit event + queued email (once per backup).
- Off-site destination model: local / SFTP / S3, credentials encrypted and never returned, `not_configured` until set; local copy op exists, SFTP/S3 report `not_implemented`.

## 3. Routes and screens
`/admin/backups`, `/hosting/backups`, `/customer/backups` (shared `BackupsPage`): overview, inventory + detail sheet, schedules, retention (+ simulate), restores (+ approve), destinations, history, restore wizard (source, target, impact preview, typed confirmation).

## 4. Components created/updated
`frontend/src/components/backups/*` and the three thin route files.

## 5. Backend endpoints (`/api/v1/backups`)
`GET /overview`, `GET /`, `POST /` (write), `GET /{id}`, `POST /{id}/verify`, `POST /{id}/restore-preview` (write); `GET /history`; schedules `GET|POST /schedules`, `PATCH|DELETE /schedules/{id}` (write), `POST /schedules/sweep` (manage, platform); retention `GET|POST /retention-policies`, `PATCH /retention-policies/{id}` (write; platform defaults need manage), `POST /retention/simulate` (read), `POST /retention/prune` (manage); restores `GET /restores`, `GET /restores/{id}`, `POST /restores/{id}/submit` (restore), `/approve` and `/reject` (manage); destinations `GET|POST /destinations`, `POST /destinations/{id}/test`, `DELETE /destinations/{id}` (manage).
Agent operations (`agent/kvl_agent/operations/backups.py`, mirrored in `backend/app/ops/backups.py`): `backups.init_key|init_dest|disk_check|create|verify|inspect|restore|prune|offsite_copy_local`.

## 6. Database models and migrations
`backup_records`, `backup_schedules`, `retention_policies`, `backup_destinations`, `restore_requests` (`app/models/backups.py`); shared `resource_refs`. No migration generated.

## 7. Integrations
Agent only. SFTP/S3 off-site transfer is **not implemented** (model and encrypted credentials only). Database dumps are produced by the databases module (Phase 8), not here.

## 8. Authorization and tenant-isolation review
`backups:read` (view/simulate), `backups:write` (request, verify, schedules, restore preview, tenant policies), `backups:restore` (submit restore; customers hold it), `backups:manage` (approve, prune, destinations, sweeps, platform default policies; staff only). Customers can restore only into isolated folders unless a different manager approves an overwrite. Another tenant's backup, restore, schedule or destination is 404. Resources are verified with `owns_resource()` through `require_ref()`.

## 9. Security review
No key material in DB, jobs, logs or API (only `key_id`). Off-site credentials encrypted with `encrypt_secret`. Archive extraction uses the hardened extractor; restore refuses a checksum mismatch before reading the archive's contents; archive paths must resolve inside the backup root and may not contain symlinks or traversal. Prune and overwrite are double-guarded (control plane and agent schemas).

## 10. Tests executed
`agent/tests/test_backup_ops.py` (20), `agent/tests/test_ssl_ops.py`, `backend/tests/test_backups.py`. Covered: backup completion and manifest, inline config backup, symlink skipping, traversal, checksum mismatch (agent and control plane, alert), disk unavailable, wrong disk identity, disk full (before and during write), encryption round trip, tamper and wrong key, isolated restore integrity with source preserved, manifest/restore integrity mismatch, overwrite confirmation and move-aside, prune refusal without recovery point and outside the backup area, retention simulation deleting nothing (inventory, files, jobs), slots/min_keep, owner-only prune, end-to-end prune keeping the recovery point, unauthorized restore (support, other tenant, anonymous, customer approving), requester cannot approve own restore, schedule validation/sweep/failure alerts, destination credential secrecy, tenant isolation, recovery documentation present (`docs/runbooks/backup-and-recovery.md`).

## 11. Lint/type/build results
test_backups 25 and agent test_backup_ops 20 pass. Frontend `tsc --noEmit`/eslint run per component set; `next build` not run.

## 12. Resource impact
Backups run on the production server: tar.gz + sha256 are CPU/IO heavy; schedule them off-peak. Free space is checked first. Verification re-reads the archive (deep verification reads it twice).

## 13. Known issues and limitations
- Same-host backups are not disaster protection; off-site transfer (SFTP/S3) is not implemented.
- No incremental / point-in-time recovery; RPO = backup interval. RPO/RTO figures in the runbook are assumptions to be validated by a restore drill.
- Overwrite restore supports single-root website file backups only. Database restore into a live database is not implemented (restore isolated and use the databases module).
- Encrypted-backup key loss is unrecoverable; key backup is an operator procedure (documented), not automated.
- Retention prune acts per (type, resource) group per call; no automatic prune schedule (deliberate).
- Needs periodic callers for `POST /backups/schedules/sweep` (also reconciles job results) and a running agent daemon.
- Email alerts are queued only (no SMTP transport exists in dev).
- Backup root, restore root and optional `backup_disk` setting are conventions (`backups/`, `restores/` under the agent data dir); mounting a dedicated disk at `backups/` is an operator step.

## 14. Rollback instructions
Revert the files above and drop the five tables. Backup archives on disk are never touched by removing the code; delete `backups/` and `restores/` by hand only after confirming you no longer need them.

## 15. Next phase prerequisites
Agent daemon; website/database registration (`root_rel`, `config_rel`, `dump_rel`); a second disk for off-site/local copy; periodic scheduler for sweeps; owner decision on RPO and off-site target.

## 16. Owner approval status
Pending owner review. Pruning and overwrite restores require explicit owner action and are never automatic.
