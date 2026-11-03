# Backup and recovery runbook (Phase 11)

Audience: the platform owner / on-call operator of the KVL private hosting server.
Scope: website files, staged database dumps, website configuration and SaaS configuration metadata.

## 1. What is protected and how

| Backup type | Source (registered per resource) | Notes |
|---|---|---|
| `website_files` | website `root_rel` | tar.gz of the site root. Symlinks are skipped (counted in the manifest), never followed. |
| `website_config` | website `config_rel` | vhost / runtime configuration files only. |
| `database` | database `dump_rel` | The agent archives a **staged dump**; producing the dump (pg_dump / mysqldump) belongs to the databases module. Until a dump path is registered the API answers `409 not_configured`. |
| `saas_config` | built by the control plane | tenant, quotas and resource list as JSON. Contains no credentials, tokens or keys by construction. |

Each backup writes `<name>-<UTC time>-<id>.tar.gz[.enc]` and a `.manifest.json` (sha256, size, entries, layout, key id) under
`<backup root>/<tenant id>/`. Archives are written as `*.partial` and renamed, so a half-written file never looks like a backup.

Full vs incremental: **only full backups are implemented.** Incremental strategies need a chunk store or filesystem snapshots (ZFS/btrfs/LVM)
to be correct; they are intentionally not faked. Retention keeps older fulls instead.

## 2. RPO / RTO assumptions (single private server)

| | Assumption | Basis |
|---|---|---|
| RPO (data loss window) | **24 hours** with the recommended nightly schedule (`30 2 * * *`); at most 6 hours if the owner schedules 4x/day (the schedule validator allows at most 4 runs per hour, nightly is the default recommendation). Database RPO equals the dump schedule. | Last completed *and verified* backup. |
| RTO website | **1-2 hours** for a typical site: isolated restore (minutes), inspection, then approved overwrite. | Size-dependent: assume ~100-200 MB/s local disk. |
| RTO whole server | **4-8 hours**, dominated by reinstalling the OS/stack and re-enrolling the agent; not automated. | Requires an off-site copy (see 6). |
| Off-site | **Not configured by default.** A same-disk backup does **not** protect against disk or host loss. | Local-path off-site copy works through the agent when a second mounted disk is registered; SFTP/S3 transfer is not implemented in this build. |

These figures are planning assumptions, not measurements. Re-measure with a real restore drill (section 5) and update this table.

## 3. Safety rules built into the system

* The agent never formats, partitions or mounts disks. The backup root must already exist; if it is missing the job fails with `disk_unavailable`.
* Optional disk identity: set platform setting `backup_disk` = `{"disk_id": "<id>"}` and run `backups.init_dest` once to write the marker file
  `.kvl-backup-disk`. Backups then refuse to write to any other disk (`disk_identity_mismatch`). The marker is never overwritten.
* Free space is checked **before** writing (`disk_full`); an ENOSPC mid-write removes the partial file.
* Setup never deletes existing backups. The only deleting operation is `backups.prune`.
* Retention is **simulated** by default (`POST /backups/retention/simulate` returns what *would* be pruned, `deleted: 0`).
  Actual pruning (`POST /backups/retention/prune`) needs `backups:manage`, `confirm=true` and the typed word `PRUNE`; the agent additionally refuses
  unless a **verified recovery point** that is not in the delete list still matches its sha256.
* Every completed backup is automatically verified (sha256 of the stored file). A mismatch marks it `mismatch`, raises an audit event and a queued
  email, and blocks restore. `deep` verification also reads every archive member (and authenticates every encrypted chunk).
* Restore defaults to an **isolated** folder (`restores/<id>/`); live data is not touched.
* Overwrite restore needs: a *verified* backup, `confirm`, `confirm_overwrite`, the typed phrase `RESTORE <resource>`, and approval by a **different**
  person holding `backups:manage`. The agent moves the existing data to `restores/pre-<id>/` instead of deleting it.
* Extraction uses the safe extractor (no absolute paths, `..`, links or devices; size/ratio/file-count limits).

## 4. Encryption and key handling

* Encryption is optional per backup/schedule (`encrypt=true`): AES-256-GCM, 1 MiB chunks, each chunk authenticated (truncation, reordering and tampering are detected).
* The key is **generated on the agent host** by `backups.init_key` into `<agent data dir>/keys/backup.key`, mode `0600`, and is never overwritten.
  The control plane, the database, job parameters, logs and API responses only ever see the key **fingerprint** (`key_id`).
* **If the key is lost, encrypted backups are unrecoverable.** Procedure: copy `keys/backup.key` to an offline location (password manager secure note and a printed copy
  in a safe) immediately after `init_key`. Never store it on the same disk as the backups only. Rotate by generating a new key on a new host path and keeping the old key for as long as
  old encrypted backups exist; the manifest records which `key_id` encrypted each file.
* A backup requested with `encrypt=true` before a key exists fails with `key_missing` (clear message, no silent fallback to plaintext).
* Off-site credentials (S3/SFTP) are encrypted at rest with the panel `ENCRYPTION_KEY` and never returned by the API.

## 5. Routine checks and drills

Weekly (5 minutes): Admin > Backups > Overview. Expect: last success per type < 36 h old, `failed (7d)` = 0, `unverified` = 0, `verification problems` = 0.

Monthly restore drill (required to trust the RTO):
1. Pick a website backup > Restore > target **Isolated** > review the impact preview > type the confirmation phrase.
2. When completed, open `restores/<id>/data/` on the server and spot-check files (open `index.html`, compare file counts with the inventory `entries`).
3. Record the elapsed time. Remove old `restores/<id>` folders by hand when finished (they are never auto-deleted).

Quarterly: restore a database dump into a scratch database (not the live one) and run a sanity query.

Alerts: failures create an audit event and a queued email to the tenant contact (no SMTP is configured in dev, so mail sits in the outbox until a transport exists). Make sure the platform operator also watches `audit` events `backup.failed`, `backup.checksum_mismatch`, `backup.verify_failed`, `restore.failed`, `backup.schedule_failed`.

Periodic tasks (cron / systemd timers on the control plane): call `POST /api/v1/backups/schedules/sweep` every 5 minutes (starts due schedules and reconciles finished jobs) and `POST /api/v1/ssl/sweep` every 6 hours. Both are idempotent.

## 6. Recovery runbook

### A. A website was damaged or deleted (most common)
1. Admin/Customer > Backups > Inventory > choose the newest backup marked **confirmed recovery point**.
2. Restore > **Isolated** first. Check the result in `restores/<id>/data`.
3. If the content is right, restore again with target **Overwrite live site** (website file backups only): tick the overwrite box, type `RESTORE <site>`, submit. A second person with backup management permission approves.
4. Verify the site in a browser. The previous data is in `restores/pre-<id>/`; keep it until you are sure, then delete manually.

### B. Corrupt or missing backup
* `mismatch` / `failed` verification: do **not** use it. Use the next verified backup. Run a fresh backup immediately.
* If every backup is damaged: check the off-site copy; if none exists, this is the failure the off-site destination exists to prevent.

### C. Backup disk full or unavailable
* Symptoms: jobs failing with `disk_full` / `disk_unavailable` / `disk_identity_mismatch`.
* Check the mount (`df -h`, `lsblk -f`, marker file). Do **not** reformat. Free space by running a retention **simulation**, reviewing it, and only then an approved prune (requires a verified recovery point).
* Failed runs leave no partial files and existing backups are untouched.

### D. Whole server lost
1. Provision a replacement server, install the stack, install the agent and enrol it (Phase 4).
2. Mount the off-site disk or fetch the off-site copy (the archive plus its `.manifest.json`).
3. Register resources again (websites, domains, databases) so ownership and paths exist.
4. Copy archives to `<backup root>/<tenant id>/`, restore **isolated** per site, verify, then place into the site roots.
5. For encrypted archives restore `keys/backup.key` from the offline copy first (section 4).
6. Restore databases from dumps with the databases module, then re-issue certificates (SSL) and check DNS.

### E. Restore a single file
Restore isolated, copy the file out of `restores/<id>/data/`, leave live data alone.

## 7. What is NOT covered (be honest with customers)
* No incremental/PITR (point-in-time recovery). RPO is the backup interval.
* No automated off-site transfer to SFTP/S3 (model and encrypted credentials exist; transfer client does not).
* Database dumps are archived but not produced here.
* Backups live on the same host unless a second disk is registered: this is not disaster protection by itself.
* Restore jobs and archives are not rate-limited per tenant.
