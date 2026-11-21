"""WordPress toolkit ops: no PHP executed, official downloads only, safe extraction, atomic swap + rollback."""
import io
import zipfile

import pytest
from pydantic import ValidationError

from kvl_agent.operations import wordpress as wp
from kvl_agent.testing import make_ctx

APP = "a-wpblog01"
URL = "https://downloads.wordpress.org/plugin/akismet.5.4.zip"


def make_wp(tmp_path, app=APP):
    root = tmp_path / "apps" / app / "releases" / "r1"
    (root / "wp-includes").mkdir(parents=True)
    (root / "wp-includes/version.php").write_text("<?php\n$wp_version = '6.8.1';\n")
    p = root / "wp-content/plugins/akismet"
    p.mkdir(parents=True)
    (p / "akismet.php").write_text("<?php\n/*\nPlugin Name: Akismet Anti-spam\nVersion: 5.3\n*/\n")
    (root / "wp-content/plugins/hello.php").write_text("<?php\n/*\nPlugin Name: Hello Dolly\nVersion: 1.7.2\n*/")
    t = root / "wp-content/themes/twentytwentyfive"
    t.mkdir(parents=True)
    (t / "style.css").write_text("/*\nTheme Name: Twenty Twenty-Five\nVersion: 1.2\n*/")
    (tmp_path / "apps" / app / "current").symlink_to(root)
    return root


def zip_bytes(files: dict[str, str]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return b.getvalue()


def ctx_with(tmp_path, data):
    c = make_ctx(tmp_path)
    c.extra["wp_fetch"] = lambda url: data
    return c


def test_status_reads_headers_without_php(tmp_path):
    make_wp(tmp_path)
    r = wp.status(wp.Status(app=APP), make_ctx(tmp_path))
    assert r["ok"] and r["core"]["version"] == "6.8.1" and not r["maintenance"]
    assert {(x["slug"], x["version"]) for x in r["plugins"]} == {("akismet", "5.3"), ("hello", "1.7.2")}
    assert r["themes"][0]["slug"] == "twentytwentyfive"


def test_status_not_wordpress(tmp_path):
    (tmp_path / "apps" / APP / "current").mkdir(parents=True)
    assert wp.status(wp.Status(app=APP), make_ctx(tmp_path))["status"] == "not_wordpress"


@pytest.mark.parametrize("url", [
    "http://downloads.wordpress.org/plugin/akismet.zip",
    "https://evil.org/plugin/akismet.zip",
    "https://downloads.wordpress.org.evil.org/plugin/akismet.zip",
    "https://downloads.wordpress.org/plugin/../x.zip",
    "https://downloads.wordpress.org/plugin/akismet.zip?x=1",
])
def test_only_official_downloads(url):
    with pytest.raises(ValidationError):
        wp.UpdateItem(app=APP, kind="plugin", slug="akismet", package_url=url)


@pytest.mark.parametrize("slug", ["../x", "Akismet", "a b", "a/b", ""])
def test_slug_validated(slug):
    with pytest.raises(ValidationError):
        wp.RollbackItem(app=APP, kind="plugin", slug=slug)


def test_update_swap_and_rollback(tmp_path):
    root = make_wp(tmp_path)
    data = zip_bytes({"akismet/akismet.php": "<?php\n/*\nPlugin Name: Akismet Anti-spam\nVersion: 5.4\n*/", "akismet/readme.txt": "x"})
    r = wp.update_item(wp.UpdateItem(app=APP, kind="plugin", slug="akismet", package_url=URL), ctx_with(tmp_path, data))
    assert r["ok"] and r["from"] == "5.3" and r["to"] == "5.4", r
    assert (root / "wp-content/plugins/akismet/readme.txt").exists()
    assert not list((root / "wp-content/plugins").glob(".kvl-*"))
    r = wp.rollback_item(wp.RollbackItem(app=APP, kind="plugin", slug="akismet"), make_ctx(tmp_path))
    assert r["ok"]
    assert "Version: 5.3" in (root / "wp-content/plugins/akismet/akismet.php").read_text()


def test_update_rejects_bad_packages(tmp_path):
    root = make_wp(tmp_path)
    for files in ({"other/x.php": "x"}, {"akismet/a.php": "x", "extra.txt": "x"}, {"../../evil.php": "x"}):
        r = wp.update_item(wp.UpdateItem(app=APP, kind="plugin", slug="akismet", package_url=URL), ctx_with(tmp_path, zip_bytes(files)))
        assert not r["ok"], files
    assert "Version: 5.3" in (root / "wp-content/plugins/akismet/akismet.php").read_text()  # untouched
    assert not (tmp_path / "evil.php").exists()
    r = wp.update_item(wp.UpdateItem(app=APP, kind="plugin", slug="akismet", package_url="https://downloads.wordpress.org/theme/akismet.zip"), ctx_with(tmp_path, b""))
    assert r["status"] == "mismatch"
    r = wp.update_item(wp.UpdateItem(app=APP, kind="plugin", slug="jetpack", package_url="https://downloads.wordpress.org/plugin/jetpack.zip"), ctx_with(tmp_path, b""))
    assert r["status"] == "not_installed"
    big = wp.MAX_ZIP + 1
    r = wp.update_item(wp.UpdateItem(app=APP, kind="plugin", slug="akismet", package_url=URL), ctx_with(tmp_path, b"x" * big))
    assert r["status"] == "too_large"


def test_maintenance_toggle(tmp_path):
    root = make_wp(tmp_path)
    assert wp.maintenance(wp.Maintenance(app=APP, enabled=True), make_ctx(tmp_path))["ok"]
    assert (root / ".maintenance").read_text().startswith("<?php $upgrading = ")
    assert wp.status(wp.Status(app=APP), make_ctx(tmp_path))["maintenance"]
    wp.maintenance(wp.Maintenance(app=APP, enabled=False), make_ctx(tmp_path))
    assert not (root / ".maintenance").exists()
