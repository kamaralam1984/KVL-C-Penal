import hashlib
import json
import os
import shutil
from collections import namedtuple

import pytest
from helpers import call, make_ctx

from kvl_agent.registry import InvalidParams, validate
from kvl_agent.safe_paths import OpError

SITE = "sites/acme/public"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    c = make_ctx(tmp_path)
    s = tmp_path / SITE
    (s / "wp").mkdir(parents=True)
    (s / "index.html").write_text("<h1>hello</h1>")
    (s / "wp/a.txt").write_text("aaaa")
    (tmp_path / "backups").mkdir()
    (tmp_path / "restores").mkdir()
    return c


def mk(ctx, **kw):
    params = dict(kind="website_files", name="acme-site", scope="tenant1", sources=[SITE])
    params.update(kw)
    return call(ctx, "backups.create", **params)


def test_backup_completion_and_manifest(ctx):
    r = mk(ctx)
    assert r["ok"] and r["entries"] == 2 and len(r["sha256"]) == 64 and r["layout"] == "single"
    a = ctx.data_dir / r["archive"]
    assert a.is_file() and hashlib.sha256(a.read_bytes()).hexdigest() == r["sha256"]
    mf = json.loads((a.with_name(a.name + ".manifest.json")).read_text())
    assert mf["sha256"] == r["sha256"] and mf["kind"] == "website_files"
    assert not list((ctx.data_dir / "backups").rglob("*.partial"))
    assert (ctx.data_dir / SITE / "index.html").exists()  # source untouched


def test_inline_config_backup(ctx):
    r = call(ctx, "backups.create", kind="saas_config", name="tenant-meta", scope="tenant1", inline_files={"tenant.json": '{"a":1}'})
    i = call(ctx, "backups.inspect", archive=r["archive"])
    assert i["files"] == 1 and "meta" in i["top_level"]


def test_symlinks_in_source_skipped(ctx):
    os.symlink(ctx.data_dir, ctx.data_dir / SITE / "evil")
    r = mk(ctx)
    assert r["symlinks_skipped"] >= 1 and r["entries"] == 2


def test_source_symlink_rejected(ctx):
    os.symlink(ctx.data_dir / SITE, ctx.data_dir / "alias")
    with pytest.raises(OpError) as e:
        mk(ctx, sources=["alias"])
    assert e.value.code == "symlink_blocked"


def test_traversal_in_sources_and_archive(ctx):
    for bad in ["../x", "%2e%2e/etc", "a\x00b"]:
        with pytest.raises(OpError):
            mk(ctx, sources=[bad])
    r = mk(ctx)
    with pytest.raises(OpError) as e:
        call(ctx, "backups.verify", archive=f"{SITE}/index.html", expected_sha256="0" * 64)
    assert e.value.code == "path_traversal"
    assert r["ok"]


def test_checksum_mismatch_detected(ctx):
    r = mk(ctx)
    ok = call(ctx, "backups.verify", archive=r["archive"], expected_sha256=r["sha256"], deep=True)
    assert ok["ok"] and ok["deep"] and ok["members"] >= 2
    p = ctx.data_dir / r["archive"]
    with open(p, "ab") as f:
        f.write(b"tamper")
    bad = call(ctx, "backups.verify", archive=r["archive"], expected_sha256=r["sha256"])
    assert bad["ok"] is False and bad["reason"] == "checksum_mismatch"
    with pytest.raises(OpError) as e:
        call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="a" * 32)
    assert e.value.code == "checksum_mismatch"
    assert not any((ctx.data_dir / "restores").iterdir())


def test_disk_unavailable(ctx):
    shutil.rmtree(ctx.data_dir / "backups")
    with pytest.raises(OpError) as e:
        mk(ctx)
    assert e.value.code == "disk_unavailable"
    assert not (ctx.data_dir / "backups").exists()  # never creates the destination root itself


def test_disk_identity_marker(ctx):
    with pytest.raises(OpError) as e:
        mk(ctx, disk_id="disk-abcdef")
    assert e.value.code == "disk_identity_mismatch"
    call(ctx, "backups.init_dest", disk_id="disk-abcdef")
    assert mk(ctx, disk_id="disk-abcdef")["ok"]
    with pytest.raises(OpError):
        mk(ctx, disk_id="disk-other1")
    call(ctx, "backups.init_dest", disk_id="disk-changed")  # never overwrites an existing marker
    assert (ctx.data_dir / "backups/.kvl-backup-disk").read_text().strip() == "disk-abcdef"


def test_disk_full(ctx, monkeypatch):
    from kvl_agent.operations import backups as b

    DU = namedtuple("DU", "total used free")
    monkeypatch.setattr(b.shutil, "disk_usage", lambda p: DU(100, 99, 1000))
    with pytest.raises(OpError) as e:
        mk(ctx, reserve_bytes=64 * 1024**2)
    assert e.value.code == "disk_full"
    assert not list((ctx.data_dir / "backups").rglob("*"))
    assert call(ctx, "backups.disk_check", required_bytes=10, reserve_bytes=0)["ok"] is True
    assert call(ctx, "backups.disk_check", required_bytes=10**6)["ok"] is False


def test_enospc_during_write_cleans_partial(ctx, monkeypatch):
    from kvl_agent.operations import backups as b

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(b.shutil, "copyfile", boom)
    with pytest.raises(OpError) as e:
        mk(ctx)
    assert e.value.code == "disk_full"
    assert not [p for p in (ctx.data_dir / "backups").rglob("*") if p.is_file()]


def test_encrypted_backup_roundtrip_and_key_safety(ctx):
    with pytest.raises(OpError) as e:
        mk(ctx, encrypt=True)
    assert e.value.code == "key_missing"
    k = call(ctx, "backups.init_key")
    assert len(k["key_id"]) == 16 and "key" not in {x for x in k if x != "key_id"}
    keyfile = ctx.data_dir / "keys/backup.key"
    assert oct(keyfile.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(OpError) as e:
        call(ctx, "backups.init_key")
    assert e.value.code == "key_exists"
    r = mk(ctx, encrypt=True)
    assert r["archive"].endswith(".enc") and r["encrypted"] and r["key_id"] == k["key_id"]
    raw = (ctx.data_dir / r["archive"]).read_bytes()
    assert b"hello" not in raw and b"index.html" not in raw
    assert keyfile.read_text().strip() not in json.dumps(r)
    assert call(ctx, "backups.verify", archive=r["archive"], expected_sha256=r["sha256"], deep=True)["ok"]
    rest = call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="b" * 32)
    assert (ctx.data_dir / rest["location"] / "data/index.html").read_text() == "<h1>hello</h1>"


def test_encrypted_tamper_and_wrong_key(ctx):
    call(ctx, "backups.init_key")
    r = mk(ctx, encrypt=True)
    p = ctx.data_dir / r["archive"]
    blob = bytearray(p.read_bytes())
    blob[-5] ^= 0xFF
    p.write_bytes(bytes(blob))
    sha = hashlib.sha256(bytes(blob)).hexdigest()
    res = call(ctx, "backups.verify", archive=r["archive"], expected_sha256=sha, deep=True)
    assert res["ok"] is False and res["reason"] == "decrypt_failed"
    r2 = mk(ctx, encrypt=True)
    (ctx.data_dir / "keys/backup.key").unlink()
    call(ctx, "backups.init_key")  # different key now
    res2 = call(ctx, "backups.verify", archive=r2["archive"], expected_sha256=r2["sha256"], deep=True)
    assert res2["ok"] is False


def test_isolated_restore_integrity_and_source_preserved(ctx):
    r = mk(ctx)
    before = (ctx.data_dir / SITE / "wp/a.txt").read_text()
    (ctx.data_dir / SITE / "wp/a.txt").write_text("changed-after-backup")
    rest = call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="c" * 32)
    assert rest["mode"] == "isolated" and rest["files"] == 2 and rest["source_untouched"]
    assert (ctx.data_dir / rest["location"] / "data/wp/a.txt").read_text() == before
    assert (ctx.data_dir / SITE / "wp/a.txt").read_text() == "changed-after-backup"
    with pytest.raises(OpError) as e:  # restore ids are single-use
        call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="c" * 32)
    assert e.value.code == "exists"


def test_restore_integrity_manifest_mismatch(ctx):
    r = mk(ctx)
    a = ctx.data_dir / r["archive"]
    mf = a.with_name(a.name + ".manifest.json")
    m = json.loads(mf.read_text())
    m["entries"] = 99
    mf.write_text(json.dumps(m))
    with pytest.raises(OpError) as e:
        call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="d" * 32)
    assert e.value.code == "restore_integrity"
    assert not (ctx.data_dir / "restores" / ("d" * 32)).exists()


def test_overwrite_requires_confirmation_and_keeps_previous(ctx):
    r = mk(ctx)
    base = dict(archive=r["archive"], expected_sha256=r["sha256"], restore_id="e" * 32, mode="overwrite", target=SITE)
    with pytest.raises(InvalidParams):
        validate("backups.restore", base)
    with pytest.raises(InvalidParams):
        validate("backups.restore", {**base, "confirm_overwrite": True, "target": None})
    (ctx.data_dir / SITE / "index.html").write_text("DAMAGED")
    out = call(ctx, "backups.restore", **base, confirm_overwrite=True)
    assert (ctx.data_dir / SITE / "index.html").read_text() == "<h1>hello</h1>"
    kept = ctx.data_dir / out["previous_data_kept_at"]
    assert (kept / "index.html").read_text() == "DAMAGED"  # nothing deleted


def test_overwrite_target_traversal_rejected(ctx):
    r = mk(ctx)
    for t in ("../etc", "sites/../../etc"):
        with pytest.raises((OpError, InvalidParams)):
            call(ctx, "backups.restore", archive=r["archive"], expected_sha256=r["sha256"], restore_id="f" * 32, mode="overwrite", target=t, confirm_overwrite=True)


def test_prune_refuses_without_confirmed_recovery_point(ctx):
    a, b, c = mk(ctx), mk(ctx), mk(ctx)
    ref = lambda x: {"archive": x["archive"], "sha256": x["sha256"]}
    with pytest.raises(InvalidParams):
        validate("backups.prune", {"recovery_point": ref(a), "delete": [ref(b)]})
    with pytest.raises(OpError) as e:
        call(ctx, "backups.prune", confirm=True, recovery_point={"archive": a["archive"], "sha256": "0" * 64}, delete=[ref(b)])
    assert e.value.code == "no_confirmed_recovery_point"
    assert (ctx.data_dir / b["archive"]).exists()
    with pytest.raises(OpError):
        call(ctx, "backups.prune", confirm=True, recovery_point=ref(a), delete=[ref(a)])
    out = call(ctx, "backups.prune", confirm=True, recovery_point=ref(a), delete=[ref(b), {"archive": c["archive"], "sha256": "1" * 64}])
    assert out["deleted"] == [b["archive"]] and out["skipped"][0]["reason"] == "checksum_changed"
    assert (ctx.data_dir / a["archive"]).exists() and (ctx.data_dir / c["archive"]).exists() and not (ctx.data_dir / b["archive"]).exists()


def test_prune_cannot_touch_outside_backup_area(ctx):
    a = mk(ctx)
    victim = ctx.data_dir / SITE / "index.html"
    sha = hashlib.sha256(victim.read_bytes()).hexdigest()
    out = call(ctx, "backups.prune", confirm=True, recovery_point={"archive": a["archive"], "sha256": a["sha256"]}, delete=[{"archive": f"{SITE}/index.html", "sha256": sha}])
    assert out["deleted"] == [] and out["skipped"]
    assert victim.exists()


def test_offsite_local_copy(ctx):
    (ctx.data_dir / "offsite").mkdir()
    r = mk(ctx)
    out = call(ctx, "backups.offsite_copy_local", archive=r["archive"], expected_sha256=r["sha256"], dest_dir="offsite")
    assert (ctx.data_dir / out["copied_to"]).stat().st_size == r["size"]


def test_dry_run_writes_nothing(tmp_path):
    from kvl_agent.context import OpContext
    from kvl_agent.registry import load_operations

    load_operations()
    (tmp_path / "backups").mkdir()
    (tmp_path / "s").mkdir()
    (tmp_path / "s/f").write_text("x")
    c = OpContext(data_dir=tmp_path, dry_run=True)
    r = call(c, "backups.create", kind="website_files", name="x", scope="t1", sources=["s"])
    assert r["dry_run"] and not list((tmp_path / "backups").iterdir())
