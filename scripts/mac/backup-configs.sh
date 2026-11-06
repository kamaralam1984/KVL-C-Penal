#!/usr/bin/env bash
# KVL Control Panel - back up EXISTING Mac server configs BEFORE any install.
#
# Only COPIES files into a brand-new directory (~/kvl-backups/<timestamp>, mode 700).
# Never modifies, moves or deletes anything it backs up. No sudo. Does not touch services.
# MariaDB dump is opt-in (--with-mariadb) and uses your own ~/.my.cnf credentials.
#
# Usage:  bash backup-configs.sh --yes [--with-mariadb]
set -euo pipefail

YES=0; WITH_MARIADB=0
for a in "$@"; do
  case "$a" in
    --yes) YES=1 ;;
    --with-mariadb) WITH_MARIADB=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
[ "$YES" = 1 ] || { echo "Dry run. Re-run with --yes to create the backup (copy-only)."; exit 0; }

DEST="$HOME/kvl-backups/$(date +%Y%m%d-%H%M%S)"
umask 077
mkdir -p "$DEST"
echo "Backup directory: $DEST"

copy_if() { # copy_if <src> : copy a dir or file when it exists
  local src="$1"
  [ -e "$src" ] || return 0
  local rel; rel="$(echo "$src" | sed 's#^/##; s#/#_#g')"
  cp -Rp "$src" "$DEST/$rel" 2>>"$DEST/errors.log" && echo "copied $src" || echo "SKIPPED (unreadable) $src"
}

for p in /opt/local/etc/nginx /opt/local/etc/php83 /opt/local/etc/mariadb-10.11 /opt/local/etc/redis.conf /opt/local/etc/redis \
         /opt/homebrew/etc/nginx /usr/local/etc/nginx \
         /opt/homebrew/etc/php /usr/local/etc/php \
         /opt/homebrew/etc/my.cnf /opt/homebrew/etc/my.cnf.d /usr/local/etc/my.cnf /usr/local/etc/my.cnf.d \
         /opt/homebrew/etc/redis.conf /usr/local/etc/redis.conf \
         "$HOME/Library/LaunchAgents"; do
  copy_if "$p"
done

# Record state (names/versions only) for later comparison.
{ date; sw_vers 2>/dev/null || true; command -v brew >/dev/null && brew list --versions 2>/dev/null || true; } > "$DEST/state.txt"

if [ "$WITH_MARIADB" = 1 ]; then
  MYSQLDUMP="$(command -v mysqldump || ls /opt/local/lib/mariadb*/bin/mysqldump 2>/dev/null | head -n 1 || true)"
  if [ -n "$MYSQLDUMP" ]; then
    "$MYSQLDUMP" --all-databases --single-transaction --routines --events > "$DEST/mariadb-all.sql" \
      && echo "MariaDB dump written" || echo "MariaDB dump FAILED (check ~/.my.cnf credentials)"
  else
    echo "mysqldump not found; skipping MariaDB dump"
  fi
fi

( cd "$DEST" && find . -type f -print0 | xargs -0 shasum -a 256 > SHA256SUMS 2>/dev/null ) || true
echo "Done. Verify: ls -la '$DEST'. Keep this directory OUT of Git. Website files (docroots) are not copied here; back those up separately."
