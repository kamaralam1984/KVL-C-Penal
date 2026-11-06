#!/usr/bin/env bash
# Render KVL STAGING launchd plists from ops/launchd/*.plist.template into a chosen output dir.
# It only writes files in that dir. It does NOT load/start anything and does not touch ~/Library unless
# you pass that path yourself.
#
# Usage: bash render-launchd.sh <KVL_HOME> <OUT_DIR> [<PG_BIN_DIR> <REDIS_BIN> <NODE_BIN_DIR>]
#   defaults are MacPorts: PG_BIN=/opt/local/lib/postgresql16/bin REDIS_BIN=/opt/local/bin/redis-server
#   NODE_BIN_DIR must be a Node >= 20.9 bin dir (e.g. ~/.nvm/versions/node/v22.x/bin); Next 16 needs it.
#   KVL_HOME  e.g. $HOME/kvl-staging   (code in $KVL_HOME/app, state in $KVL_HOME/var, env in $KVL_HOME/etc)
#   OUT_DIR   e.g. $HOME/kvl-staging/launchd  (review, then copy to ~/Library/LaunchAgents yourself)
set -euo pipefail
KVL_HOME="${1:?KVL_HOME required}"; OUT="${2:?OUT_DIR required}"
PG_BIN="${3:-/opt/local/lib/postgresql16/bin}"; REDIS_BIN="${4:-/opt/local/bin/redis-server}"; NODE_BIN="${5:?NODE_BIN_DIR required (Node >= 20.9)}"
SRC="$(cd "$(dirname "$0")/../.." && pwd)/ops/launchd"
case "$KVL_HOME" in /*) ;; *) echo "KVL_HOME must be an absolute path" >&2; exit 2;; esac
mkdir -p "$OUT"
for t in "$SRC"/*.plist.template; do
  n="$(basename "$t" .template)"
  sed -e "s#__KVL_HOME__#$KVL_HOME#g" -e "s#__PG_BIN__#$PG_BIN#g" -e "s#__REDIS_BIN__#$REDIS_BIN#g" -e "s#__NODE_BIN__#$NODE_BIN#g" "$t" > "$OUT/$n"
  echo "rendered $OUT/$n"
done
echo "Review the files, then see docs/MACOS_STAGING_SETUP.md step 7 for loading."
