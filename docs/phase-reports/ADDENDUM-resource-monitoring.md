# Addendum: live resource monitoring and per-website usage

Added after Phase 18 at the owner's request: server speed, RAM, CPU and which website uses how much, with animated gauges and charts.

## What it shows
- **Live tab** (Admin > Servers > Monitoring): round gauges (CPU, memory, fullest disk, swap), per-core bars, load vs core count, network speed (download/upload, link capacity, per interface), storage per mount, heaviest processes by RAM. Refreshes every 10 s; the agent reports every 30 s.
- **Websites tab**: RAM, disk and data-sent donut/bar charts, a per-website table with animated bars, and a history drill-down (RAM, CPU, data per 5 min; 6 h to 30 d).
- **Customer view**: each website's detail page shows only that tenant's own RAM, CPU, disk (vs quota) and data (`GET /websites/{id}/usage/live|history`).

## How the numbers are measured (agent, read-only)
- Disk: size of the site's document root (refreshed every 10 min).
- RAM/CPU: processes whose working directory is inside the site folder. A site with no such process shows a dash, not zero.
- Data and requests: parsed incrementally from `<data_dir>/nginx/logs/<site>.access.log` (combined format). Counters are cumulative; the API differences them, and a counter restart contributes nothing instead of a negative.
- Top processes: name and pid only; command lines are never collected.

## Storage
`site_usage_samples` (5-minute buckets, 30-day retention, pruned by `downsample_and_prune`) and `metric_samples.detail` (cores, processes). Migration `041f68a04c4d`.

## Limits
- PHP-FPM workers are only attributed if their cwd is inside the site folder; shared pools are not split per site.
- "Data sent 24 h" counts traffic seen after monitoring started, not the whole log history.
- Tested with a local test agent on a dev laptop and a local web server; not yet against nginx/PHP-FPM on the iMac.
