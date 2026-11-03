#!/usr/bin/env bash
# Dependency vulnerability scan for the backend (pip-audit) and frontend (npm audit).
# Usage: scripts/security/scan.sh [--json]   Exit code is non-zero if either scanner reports findings.
# Neither tool is required: a missing tool is reported as SKIPPED, never as "clean".
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FMT="${1:-}"
status=0

echo "== backend: pip-audit =="
PYAUDIT=""
if [ -x "$ROOT/backend/.venv/bin/pip-audit" ]; then PYAUDIT="$ROOT/backend/.venv/bin/pip-audit"; elif command -v pip-audit >/dev/null 2>&1; then PYAUDIT="$(command -v pip-audit)"; fi
if [ -n "$PYAUDIT" ]; then
  if [ "$FMT" = "--json" ]; then "$PYAUDIT" --progress-spinner off -f json -l --local 2>&1 || status=1
  else (cd "$ROOT/backend" && "$PYAUDIT" --progress-spinner off --local 2>&1) || status=1; fi
else
  echo "SKIPPED: pip-audit not installed (pip install pip-audit in backend/.venv)"
fi

echo "== frontend: npm audit =="
if command -v npm >/dev/null 2>&1 && [ -f "$ROOT/frontend/package-lock.json" ]; then
  (cd "$ROOT/frontend" && if [ "$FMT" = "--json" ]; then npm audit --json; else npm audit; fi) 2>&1 || status=1
else
  echo "SKIPPED: npm or frontend/package-lock.json not available"
fi

echo "== agent: pip-audit (requirements) =="
if [ -n "$PYAUDIT" ] && [ -f "$ROOT/agent/pyproject.toml" ]; then echo "NOTE: agent shares the backend venv tooling; audit its environment separately on the server."; fi
exit $status
