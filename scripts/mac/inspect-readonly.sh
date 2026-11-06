#!/usr/bin/env bash
# KVL Control Panel - READ-ONLY inspection of a macOS server.
#
# Changes NOTHING: no sudo, no writes (output goes to stdout), no service start/stop,
# no package install, no config edits. It only runs read commands and prints names/versions.
# It never prints file contents of env files, certificates, keys or database data.
#
# Run on the Mac:   bash inspect-readonly.sh | tee ~/kvl-inspect.txt
# Then paste ~/kvl-inspect.txt back for review (review it first; remove anything private).
set -u
export LC_ALL=C

have() { command -v "$1" >/dev/null 2>&1; }
section() { printf '\n==== %s ====\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 | head -n "${LINES_MAX:-40}"; }

section "Host"
run date
run uname -a
have sw_vers && run sw_vers
have sysctl && { run sysctl -n machdep.cpu.brand_string; run sysctl -n hw.ncpu; run sysctl -n hw.memsize; }
whoami; id

section "Disk"
run df -h

section "Memory / load"
have vm_stat && run vm_stat
run uptime

section "Tool versions (blank = not installed)"
for t in brew port git node npm python3 python3.11 python3.12 python3.13 python3.14 pip3 psql pg_ctl postgres redis-server redis-cli nginx php php-fpm mysql mysqld mariadb mariadbd pm2 certbot docker; do
  if [ "$t" = pm2 ]; then printf '%-14s %s  ->  (version skipped: pm2 --version would start a PM2 daemon)\n' "$t" "$(command -v "$t")"
  elif [ "$t" = nginx ]; then printf '%-14s %s  ->  ' "$t" "$(command -v "$t")"; nginx -v 2>&1 | head -n 1
  elif have "$t"; then printf '%-14s %s  ->  ' "$t" "$(command -v "$t")"; "$t" --version 2>&1 | head -n 1 || true
  else printf '%-14s (not found)\n' "$t"; fi
done
have nginx && { printf '$ nginx -V\n'; nginx -V 2>&1 | head -n 3; }

section "Listening TCP ports (own view; without sudo other users' sockets may be hidden)"
if have lsof; then lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR==1 || /LISTEN/' | head -n 60; else netstat -an 2>/dev/null | grep LISTEN | head -n 60; fi

section "Candidate KVL staging ports - must be FREE (empty = free)"
for p in 54330 63790 18000 13000 19710; do
  printf 'port %s: ' "$p"; if have lsof && lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then echo "IN USE"; else echo free; fi
done

section "Relevant processes (names only)"
ps -axo pid,user,comm 2>/dev/null | grep -Ei 'nginx|php-fpm|mysqld|mariadbd|redis-server|postgres|node|pm2|uvicorn' | grep -v grep | head -n 40

section "launchd jobs mentioning relevant services (user domain)"
have launchctl && launchctl list 2>/dev/null | grep -Ei 'nginx|php|mysql|maria|redis|postgres|pm2|kvl' | head -n 30
ls ~/Library/LaunchAgents 2>/dev/null | head -n 40

section "MacPorts packages (if port present)"
have port && port installed requested 2>/dev/null | grep -Ei 'nginx|php|mariadb|mysql|redis|postgres|nodejs|python3|certbot|pm2' | head -n 40
ls -d /opt/local/lib/postgresql*/bin /opt/local/lib/mariadb*/bin 2>/dev/null

section "Homebrew packages (if brew present)"
have brew && { brew --prefix; brew list --versions 2>/dev/null | grep -Ei 'nginx|php|mysql|maria|redis|postgres|node|python|certbot' | head -n 40; brew services list 2>/dev/null | head -n 30; }

section "nginx layout (file NAMES and server_name/listen/root lines only)"
for d in /opt/local/etc/nginx /opt/homebrew/etc/nginx /usr/local/etc/nginx /etc/nginx; do
  if [ -d "$d" ]; then
    echo "dir: $d"; ls -la "$d" 2>&1 | head -n 30
    for sub in servers sites-enabled conf.d; do [ -d "$d/$sub" ] && { echo "-- $d/$sub"; ls -la "$d/$sub" 2>&1 | head -n 40; }; done
    grep -RhsE '^\s*(server_name|listen|root)\s' "$d" 2>/dev/null | sed 's/^\s*//' | sort -u | head -n 60
  fi
done

section "Existing sites / document roots (directory names only)"
ls -d /Library/WebServer/Documents /opt/local/www /opt/local/www/* /opt/homebrew/var/www /usr/local/var/www ~/Sites /var/www 2>/dev/null

section "Sizes of key data dirs (read-only, may be slow)"
for d in /opt/local/var/db/mariadb* /opt/local/var/db/postgresql* /opt/local/var/db/redis* /opt/homebrew/var/mysql /usr/local/var/mysql /opt/homebrew/var/postgresql* /usr/local/var/postgres* /opt/homebrew/var/db/redis /usr/local/var/db/redis; do
  [ -e "$d" ] && du -sh "$d" 2>/dev/null
done

section "System launchd daemons mentioning relevant services (plist FILE NAMES only)"
ls /Library/LaunchDaemons 2>/dev/null | grep -Ei 'nginx|php|mysql|maria|redis|postgres|pm2|cloudflared|macports' | head -n 30

section "Listening processes on 3000/8000/5432/3306 (name + command only)"
for p in 3000 8000 5432 3306 6379 80; do
  pid=$(lsof -nP -iTCP:"$p" -sTCP:LISTEN -t 2>/dev/null | head -n 1); [ -n "$pid" ] && { printf 'port %s -> ' "$p"; ps -o pid=,user=,command= -p "$pid" | cut -c1-160; }
done

section "Memory pressure (read-only)"
have memory_pressure && memory_pressure 2>&1 | tail -n 4
have sysctl && sysctl -n vm.swapusage

section "Network (hostname only; no keys)"
hostname; have scutil && scutil --get LocalHostName 2>/dev/null
have tailscale && tailscale status 2>&1 | head -n 5

echo; echo "==== done: nothing was modified ===="
