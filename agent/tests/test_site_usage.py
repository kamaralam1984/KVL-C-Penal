import os
import subprocess
import sys
import time

from kvl_agent.site_usage import SiteUsageCollector


def _site(tmp_path, name="w-abc", body=b"x" * 1000):
    root = tmp_path / "docroot" / name
    root.mkdir(parents=True)
    (root / "index.html").write_bytes(body)
    sites = tmp_path / "nginx" / "sites"
    sites.mkdir(parents=True, exist_ok=True)
    (sites / f"{name}.conf").write_text(f"server {{\n    root {root};\n}}\n")
    logs = tmp_path / "nginx" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return root, logs / f"{name}.access.log"


def test_no_sites_is_empty(tmp_path):
    assert SiteUsageCollector(tmp_path).collect() == []


def test_disk_and_unmeasured_values_are_none_not_zero(tmp_path):
    _site(tmp_path)
    [s] = SiteUsageCollector(tmp_path).collect()
    assert s["disk_bytes"] == 1000 and s["kind"] == "site"
    assert s["rss_bytes"] is None and s["cpu_percent"] is None and s["procs"] is None  # no process inside the site
    assert s["bytes_out_total"] is None or s["bytes_out_total"] == 0


def test_access_log_counts_bytes_and_statuses_incrementally(tmp_path):
    _root, log = _site(tmp_path)
    line = '1.2.3.4 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" {} {} "-" "ua"\n'
    log.write_text(line.format(200, 500) + line.format(404, 100) + line.format(500, "-"))
    c = SiteUsageCollector(tmp_path)
    [s] = c.collect()
    assert (s["requests_total"], s["bytes_out_total"], s["errors_4xx_total"], s["errors_5xx_total"]) == (3, 600, 1, 1)
    with open(log, "a") as f:
        f.write(line.format(200, 400))
        f.write("partial line without newline")
    [s] = c.collect()
    assert (s["requests_total"], s["bytes_out_total"]) == (4, 1000)  # only the new complete line is added, nothing double counted
    [s] = c.collect()
    assert s["requests_total"] == 4


def test_log_rotation_keeps_cumulative_totals(tmp_path):
    _root, log = _site(tmp_path)
    line = '1.2.3.4 - - [x] "GET / HTTP/1.1" 200 100 "-" "ua"\n'
    log.write_text(line * 5)
    c = SiteUsageCollector(tmp_path)
    c.collect()
    log.unlink()
    log.write_text(line * 2)
    [s] = c.collect()
    assert s["requests_total"] == 7 and s["bytes_out_total"] == 700


def test_process_inside_site_root_is_attributed(tmp_path):
    root, _log = _site(tmp_path)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=root)
    try:
        c = SiteUsageCollector(tmp_path)
        c.collect()  # primes cpu counters
        time.sleep(0.3)
        [s] = c.collect()
        assert s["procs"] == 1 and s["rss_bytes"] > 0 and s["cpu_percent"] is not None
    finally:
        p.kill()
        p.wait()


def test_unreadable_root_does_not_crash(tmp_path):
    root, _log = _site(tmp_path)
    os.chmod(root, 0)
    try:
        [s] = SiteUsageCollector(tmp_path).collect()
        assert s["name"] == "w-abc"
    finally:
        os.chmod(root, 0o755)
