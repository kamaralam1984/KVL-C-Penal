# Agent deployment notes (reference only)

These steps are documentation. Phase 4 deliberately did **not** install or start anything on any host.

## Account and directories
```
useradd --system --home /var/lib/kvl-agent --shell /usr/sbin/nologin kvl-agent
install -d -o kvl-agent -g kvl-agent -m 0700 /var/lib/kvl-agent
install -d -o root      -g kvl-agent -m 0750 /etc/kvl-agent
```
The agent code lives in a root-owned virtualenv (e.g. `/opt/kvl-agent/venv`) so the agent cannot modify itself.

## Enrollment (once)
```
sudo -u kvl-agent env KVL_AGENT_DATA_DIR=/var/lib/kvl-agent \
  /opt/kvl-agent/venv/bin/python -m kvl_agent enroll --api-url https://panel.example.com/api/v1 --token kvl_enr_...
```
Credentials are written to `/var/lib/kvl-agent/credentials.json` with mode 0600; the agent refuses a group/world-readable file.

## systemd unit (`/etc/systemd/system/kvl-agent.service`)
```ini
[Unit]
Description=KVL server agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kvl-agent
Group=kvl-agent
Environment=KVL_AGENT_API_URL=https://panel.example.com/api/v1
Environment=KVL_AGENT_DATA_DIR=/var/lib/kvl-agent
ExecStart=/opt/kvl-agent/venv/bin/python -m kvl_agent run
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM

# Hardening: no new privileges, read-only system, writes only to the state dir
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ReadWritePaths=/var/lib/kvl-agent
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
```
`TimeoutStopSec` must exceed `shutdown_grace` (20 s default). Operations added in later phases that need more access
(for example writing under `/etc/nginx`) must go through narrowly-scoped helpers or sudoers rules reviewed in that phase;
the unit above intentionally grants none.

## Control-plane periodic task
Run once a minute (cron or systemd timer) from the backend directory:
`python -m app.services.maintenance` (job timeout sweeper, missing-heartbeat alerts, nonce purge, metric retention).
An admin can also trigger it with `POST /api/v1/monitoring/sweep`.
## Reverse proxy
The signature covers the request path as the API sees it (`/api/v1/agent/...`). A proxy must not rewrite the path prefix.
