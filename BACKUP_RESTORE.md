# BACKUP_RESTORE — operational procedure

Detailed runbook: `docs/runbooks/backup-and-recovery.md`. This is the summary and the evidence gate.

## What exists (code + tests with fakes)
Backup destinations, schedules, retention policies, backup records, restore requests, database export/restore requests (`backend/app/services/backups.py`, agent backup ops).

## Control-plane DB (manual, before every migration)
```bash
pg_dump -Fc -h <host> -p <port> -U <user> <db> > kvl_panel_$(date +%F).dump
createdb restore_check && pg_restore -d restore_check kvl_panel_*.dump   # prove it restores
```
Store dumps outside Git (`.gitignore` does not cover `*.dump` — keep them out of the repo directory).

## Evidence status
| Requirement (brief) | Status |
|---|---|
| Scheduled + manual backups | Implemented; tested with fakes |
| Retention | Implemented; tested with fakes |
| Encryption, off-device copy | Implemented per phase report; **not tested against a real destination** |
| **Tested restoration** | **Not demonstrated.** A restore drill on staging is required before production (`PENDING_TASKS.md`, `PRODUCTION_READINESS.md`). |
| RPO/RTO targets | Not defined |
