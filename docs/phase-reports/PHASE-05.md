# KVL Hosting Panel — Phase 05 Report (Server Monitoring Dashboard)

## 1. Phase summary
Live monitoring built only on authenticated agent heartbeats: sample storage with a retention policy, availability states, service health,
configurable thresholds, alert evaluation with deduplication/cooldown/missing-heartbeat alerts and an acknowledge/resolve lifecycle, a monitoring API, and the dashboard and per-server views. No demo data is shown anywhere; with no agent connected the screens say so.

## 2. Requirements completed
- Metrics ingested from heartbeats: CPU %, load 1/5/15, memory total/used/available, swap, disk per mount, network throughput + interface state, uptime, service status, agent version and last heartbeat.
- **Missing vs zero**: every measurement column is nullable; a heartbeat without metrics stores no sample; an unmeasurable value (first network sample, no swap configured, failed psutil call) is null end-to-end; time-series buckets with no samples are `null`, averages ignore nulls, and charts show gaps.
- **Availability**: `connected` (<= 90 s since heartbeat), `stale` (<= 300 s), `disconnected` (> 300 s), `unknown` (never seen), `disabled`.
- **Service health**: nginx, php-fpm, mariadb, redis, pm2 (plus any extra reported names). No report, unknown state or not installed => `not_configured`, never `failed`.
- **Thresholds**: defaults CPU 80/95 %, memory 85/95 %, fullest disk 80/90 %, swap 50/80 %, heartbeat age 90/300 s; per-server overrides with cooldown and enable switch, validated (warning < critical, range checks), audited.
- **Alerting**: one unresolved alert per (server, metric) enforced by a partial unique index (deduplication); repeats only bump `occurrences`/`last_seen_at`; severity change escalates (an acknowledged alert escalated to critical re-opens); recovery auto-resolves; a resolved alert cannot re-open within the metric's cooldown (suppresses flapping and manual-resolve-while-still-bad); missing readings neither open nor resolve; missing-heartbeat alerts come from the sweeper and clear on the next heartbeat; never-seen and disabled servers raise nothing. Lifecycle open -> acknowledged -> resolved records who/when (auto-resolve = resolved_at with no user). There is no outbound notification channel yet (no email/Slack): alerts are records shown in the UI.
- **Time ranges**: 1h/6h/24h/7d/30d/90d or custom start/end, any IANA timezone; buckets are aligned to *local* time (date_bin on the local timestamp), gap-filled, DST-aware (tested on Asia/Kolkata +05:30 and America/New_York across the March DST change).
- **Pagination**: alerts (`page`,`page_size` <= 100), jobs, raw samples (cursor `before`, `limit` <= 500).

## 3. Routes and screens
`/admin/monitoring` (summary cards, server cards with state/CPU/RAM/disk/uptime/services/alerts, per-server tabs Metrics / Services / Alerts / Thresholds, incidents + history table with acknowledge/resolve) and the same per-server view at `/admin/servers/[id]` (Monitoring tab). Charts: CPU, load, memory + swap, storage, network with units, legends, threshold reference lines and a range selector; the timezone used (browser's) is printed.

## 4. Components
`frontend/src/components/monitoring/{monitoring-dashboard,server-monitor,metrics-panel,services-list,alerts-panel,thresholds-editor,format,types}`; charts reuse `ChartCard`/`ChartTooltip`.

## 5. Backend endpoints (`/api/v1`, all platform-scope only; tenant/reseller accounts get 403)
| Endpoint | Permission |
|---|---|
| `GET /servers/{id}/metrics?range=&start=&end=&tz=&interval=` (+ `/metrics/latest`, `/metrics/samples`) | monitoring:read |
| `GET /servers/{id}/services` | monitoring:read |
| `GET /monitoring/overview` | monitoring:read |
| `GET /monitoring/alerts?state=&severity=&server_id=&page=&page_size=` | monitoring:read |
| `POST /monitoring/alerts/{id}/acknowledge`, `/resolve` | monitoring:write |
| `GET /monitoring/thresholds?server_id=` / `PUT /monitoring/thresholds` | monitoring:read / monitoring:write |
| `POST /monitoring/sweep` (housekeeping now) | monitoring:write |

Note: the `customer` role holds `monitoring:read`, so the platform-scope guard is what keeps infrastructure data from tenants (tested).

## 6. Database models (new file `app/models/monitoring.py`; migration not generated, per brief)
`metric_samples` (raw / 5m / 1h resolutions, unique per server+resolution+ts), `service_statuses`, `alert_thresholds`, `alerts` (partial unique index on unresolved).

## 7. Integrations
None.

## 8. Authorization and tenant-isolation review
Servers are platform infrastructure, not tenant-owned, so the control is scope: every endpoint calls `platform_only`. Verified: customer (has monitoring:read) gets 403 on all read endpoints and on thresholds PUT and sweep; support can read but cannot write thresholds or acknowledge; anonymous 401.

## 9. Security review
Same agent trust model as Phase 4. Metric payloads are strictly validated (types, 0-100 percentages, non-negative counters, list-size caps, unknown fields rejected). The timezone parameter is validated through `zoneinfo` and passed as a bound parameter; the bucket interval comes from a fixed whitelist before being placed in SQL. Alert messages contain only numbers and fixed labels.

## 10. Retention and sampling policy
Heartbeat/sample interval 30 s (about 2,880 raw rows per server per day). `downsample_and_prune`: raw kept 48 h, then rolled into 5-minute buckets (weighted averages, last disk/interface snapshot); 5-minute rows kept 14 days, then rolled into 1-hour buckets; 1-hour rows kept 400 days, then deleted. Idempotent; only whole buckets are rolled; runs from `python -m app.services.maintenance` (schedule every 1-60 min; hourly is enough) or `POST /monitoring/sweep`. Queries read all resolutions in range (rollups carry `sample_count` so averages stay weighted). Rollups keep averages only: short peaks older than 48 h are no longer visible (documented trade-off). UI polling: overview 30 s, metrics 60 s, services 60 s, alerts 30 s, server page 30 s; a heartbeat is the only write path from the agent.

## 11. API / database load (measured)
`backend/scripts/measure_monitoring_load.py` (refuses non-test DBs; synthetic rows, measurement only). Run on this development machine **while it was heavily loaded by other builds (load average 18-22 on 4 cores)**, so absolute latencies are inflated; the `/healthz` baseline (no DB, no auth) was p50 219 ms for the same reason. Dataset: 1 server, 3 days at 30 s = 8,640 raw rows.

| Operation | p50 | p95 |
|---|---|---|
| `GET /healthz` (machine baseline) | 219 ms | 438 ms |
| Heartbeat ingest (parse -> sample insert -> alert evaluation -> commit) | 157 ms | 479 ms |
| `query_series` 24 h / 15 min (DB + Python) | 381 ms | 687 ms |
| `query_series` 7 d / 1 h | 533 ms | 2.7 s |
| `GET /servers/{id}/metrics?range=24h` | 713 ms | 1.4 s |
| `GET .../metrics/latest` | 217 ms | 729 ms |
| `GET /monitoring/overview` (1 server) | 423 ms | 846 ms |
| `GET /monitoring/alerts` | 237 ms | 686 ms |
| `GET /servers/{id}/services` | 303 ms | 482 ms |
| Retention run rolling 5,756 raw rows into 576 buckets | 14.5 s total | |

Storage: about 1.1 KB per row including indexes, so one server holds roughly 6.5 MB of raw rows (48 h) + 4.5 MB of 5-minute rows (14 d) + 10.8 MB of hourly rows (400 d), about 22 MB steady state. Writes: 1 insert + 1 service upsert batch + a few small selects per heartbeat per server, i.e. about 2 heartbeats/min/server.
An earlier run of the same script at 7 days of data (17,292 raw rows) took 288 s for the retention run before it was changed from per-bucket inserts to batched inserts (14.5 s after, at 5,756 rows on the same busy machine), which is why inserts and deletes are now batched. These figures must be re-measured on the actual hosting hardware; they demonstrate shape (linear in rows, indexed range scans), not production capacity.

## 12. Tests executed
`backend/tests/test_monitoring.py` plus `test_servers_agent.py` (counts in section 13a). Covered: missing metrics vs zero (API and rollup), null-aware averages, stale/disconnected/unknown availability and overview totals, threshold crossing/escalation/auto-resolve, each metric independent, duplicate suppression, cooldown suppress-then-reopen, missing readings, missing-heartbeat alert and recovery, alert lifecycle (who/when, 409s, manual resolve + cooldown), escalation reopening an acknowledged alert, pagination and filters, threshold validation and effect, timezone day/hour bucketing (Kolkata, UTC, New York DST), range API and validation, raw-sample cursor paging, service not_configured honesty, retention roll-up/prune/idempotence/weighting, maintenance endpoint, tenant 403 and support read-only.

## 13. Lint/type/build results
See 13a.

## 13a. Final results
- Backend (`TEST_DATABASE_URL=...kvl_test_g1 PYTHONPATH=../agent pytest tests/test_servers_agent.py tests/test_monitoring.py`): **47 passed** (24 servers/agent + 23 monitoring), run time dominated by a heavily loaded machine (about 6 min).
- Agent (`cd agent && ../backend/.venv/bin/python -m pytest tests`): **131 passed** in the shared suite, 23 of them this phase's `test_agent.py`.
- Frontend: eslint on `components/servers`, `components/monitoring` and the admin servers/monitoring routes: **clean (exit 0)**. `tsc --noEmit`: no errors in this phase's files; the run reported 3 errors in other modules' files (`automation-page.tsx`, `billing-pages.tsx`: `"warning"` toast tone not in `ToastTone`), which are outside this phase.
- `next build` / `next dev` were not run (per brief).

## 14. Known issues and limitations
- Alert evaluation uses the latest heartbeat value (no multi-sample "sustained for N minutes" rule); cooldown limits noise.
- No notification delivery (email/webhooks) for alerts; no maintenance windows/silencing.
- Disk and interface detail is only the latest sample (per-mount history is not stored; the chart shows the fullest mount).
- Rollups store averages only (no min/max), so older peaks are smoothed.
- Hourly/daily buckets in DST-ambiguous local hours use the first occurrence.
- The UI loads up to 100 newest alerts/jobs per table and filters client-side (the API itself paginates).
- Service checks use the process table and PATH, not systemd, so a service managed in a container or a non-standard binary name shows `not_configured`/`stopped`.
- Not verified in a live browser; loads were type- and lint-checked. Real-hardware numbers and a real agent heartbeat from the iMac are outstanding.

## 15. Rollback instructions
Delete `app/models/monitoring.py`, `app/services/{monitoring,maintenance}.py`, `app/api/v1/routers/monitoring.py`, the frontend `components/monitoring` folder, `backend/scripts/measure_monitoring_load.py`, and restore the placeholder monitoring page; drop `metric_samples`, `service_statuses`, `alert_thresholds`, `alerts` if migrated. The agent keeps working (heartbeats are simply not stored).

## 16. Owner approval status
Pending owner review.
