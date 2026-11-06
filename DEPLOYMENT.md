# DEPLOYMENT — Git handoff to the Mac / Linux host

Full staged runbook: `docs/runbooks/deployment.md`. **A GitHub push never deploys anything.** Nothing has been deployed to any host.

## Ubuntu → GitHub (each batch)
```bash
git fetch origin && git status
git switch -c feature/<module>
# build, test (see INSTALLATION.md §5)
git add <reviewed files> && git commit -m "…"
git push -u origin feature/<module>      # then open a pull request
```
Forbidden: `git reset --hard`, `git clean -fd`, force-push, blind conflict resolution, merging unreviewed work to the production branch, committing `.env`/keys/dumps/customer data.

## GitHub → Mac (only after owner approval; reviewed, step by step)
1. Read-only first: `git fetch origin && git log --oneline HEAD..origin/<branch> && git diff --stat HEAD origin/<branch>`.
2. Back up configs and DBs (`BACKUP_RESTORE.md`).
3. Install into **staging** (separate user/port/domain) following `docs/runbooks/deployment.md` §1.
4. Production only with a separate explicit approval and a passed `PRODUCTION_READINESS.md` gate.

## Rollback
Previous release directory kept; point `current` back and restart services; prefer forward-fix for DB, otherwise restore the pre-deploy backup into a new database (`docs/runbooks/deployment.md` §3).
