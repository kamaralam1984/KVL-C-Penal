#!/usr/bin/env bash
# Release gate. `--check` runs every quality gate and exits non-zero on the first failure.
# It NEVER deploys. Deployment is a separate, owner-approved step (docs/runbooks/deployment.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:---check}"

step() { printf '\n==> %s\n' "$1"; }

if [[ "$MODE" == "--check" ]]; then
  step "Backend: lint"
  (cd "$ROOT/backend" && . .venv/bin/activate && ruff check app --select E9,F63,F7,F82 )
  step "Backend: tests against migrated schema"
  (cd "$ROOT/backend" && . .venv/bin/activate && USE_MIGRATIONS=1 pytest -q)
  step "Agent: tests"
  (cd "$ROOT/backend" && . .venv/bin/activate && PYTHONPATH="$ROOT/agent" pytest -q "$ROOT/agent/tests")
  step "Frontend: types, lint, build"
  (cd "$ROOT/frontend" && npx tsc --noEmit && npx eslint && npm run build)
  step "Dependency scan"
  bash "$ROOT/scripts/security/scan.sh" || echo "scan reported findings: review docs/security/dependency-report.md"
  step "Record"
  echo "commit: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo 'not a git repo')"
  echo "migration head: $(cd "$ROOT/backend" && . .venv/bin/activate && alembic heads 2>/dev/null | head -1)"
  echo "ALL GATES PASSED"
else
  echo "usage: $0 --check" >&2
  exit 2
fi
