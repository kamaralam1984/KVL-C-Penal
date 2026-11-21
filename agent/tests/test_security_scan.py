"""Malware scan: detects common webshell patterns, ignores trash and symlinks, read-only, no content leaks."""
import pytest
from pydantic import ValidationError

from kvl_agent.operations import security_scan as ss
from kvl_agent.testing import make_ctx

SHELLS = {
    "a.php": b"<?php eval(base64_decode('ZWNobyAxOw==')); ?>",
    "b.php": b"<?php system($_GET['cmd']); ?>",
    "c.php": b"<?php @eval($_POST['x']); ?>",
    "d.php": b"<?php // FilesMan\n echo 1;",
    "wp-content/uploads/2026/x.php": b"<?php echo 'hi';",
    "wp-content/uploads/logo.png": b"\x89PNG....<?php echo 'x'; ?>",
    "e.php": b"<?php $_GET['f']($_POST['a']);",
    "g.php": b"<?php preg_replace('/x/e', $_REQUEST['c'], 'x');",
}
CLEAN = {"index.php": b"<?php require __DIR__ . '/wp-blog-header.php';", "style.css": b"body{}", "readme.txt": b"hello"}


def site(tmp_path, files):
    root = tmp_path / "sites" / "acme" / "blog"
    for rel, data in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return root


def run(tmp_path, **kw):
    return ss.scan(ss.Scan(root="sites/acme/blog", use_clamav=False, **kw), make_ctx(tmp_path))


def test_detects_shells_and_clean_files_pass(tmp_path):
    site(tmp_path, {**SHELLS, **CLEAN})
    r = run(tmp_path)
    flagged = {f["path"] for f in r["findings"]}
    assert set(SHELLS) <= flagged and not (set(CLEAN) & flagged)
    assert r["verdict"] == "suspicious" and r["counts"]["high"] >= 6
    rules = {(f["path"], f["rule"]) for f in r["findings"]}
    assert ("a.php", "eval_decode_chain") in rules and ("wp-content/uploads/logo.png", "php_in_non_php_file") in rules
    assert "base64_decode" not in str(r) and "cmd" not in str(r)  # no file contents in results


def test_clean_site(tmp_path):
    site(tmp_path, CLEAN)
    r = run(tmp_path)
    assert r["verdict"] == "clean" and r["findings"] == [] and r["files_scanned"] == 2  # .css is not a scanned type


def test_trash_and_symlinks_skipped(tmp_path):
    root = site(tmp_path, {**CLEAN, ".kvl-trash/old/b.php": SHELLS["b.php"]})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.php").write_bytes(SHELLS["a.php"])
    (root / "link").symlink_to(outside)
    (root / "lf.php").symlink_to(outside / "x.php")
    assert run(tmp_path)["findings"] == []


def test_does_not_modify_files(tmp_path):
    root = site(tmp_path, SHELLS)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    run(tmp_path)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("bad", ["../etc", "/etc", "sites/../../x"])
def test_root_validated(bad):
    with pytest.raises(ValidationError):
        ss.Scan(root=bad)


def test_missing_root(tmp_path):
    assert run(tmp_path)["ok"] is False


def test_clamav_not_installed_reported(tmp_path, monkeypatch):
    site(tmp_path, CLEAN)
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(ctx, "which", lambda b: None)
    r = ss.scan(ss.Scan(root="sites/acme/blog"), ctx)
    assert r["clamav"] == "not_installed"
