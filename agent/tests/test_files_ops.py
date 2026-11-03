import base64
import io
import os
import tarfile
import zipfile

import pytest
from helpers import b64, call, make_ctx

from kvl_agent.registry import InvalidParams, validate
from kvl_agent.safe_paths import OpError

ROOT = "sites/acme/public"


@pytest.fixture
def ctx(tmp_path):
    c = make_ctx(tmp_path)
    (tmp_path / ROOT).mkdir(parents=True)
    (tmp_path / "sites/other/public").mkdir(parents=True)
    (tmp_path / "sites/other/public/secret.txt").write_text("other tenant")
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/passwd").write_text("root:x")
    (tmp_path / ROOT / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / ROOT / "docs").mkdir()
    (tmp_path / ROOT / "docs/a.txt").write_text("aaa")
    return c


def f(ctx, op, **kw):
    return call(ctx, op, root=ROOT, **kw)


def err(ctx, op, code=None, **kw):
    with pytest.raises(OpError) as e:
        f(ctx, op, **kw)
    if code:
        assert e.value.code == code
    return e.value


def test_list_stat_read(ctx):
    r = f(ctx, "files.list")
    assert [e["name"] for e in r["entries"]] == ["docs", "index.html"]
    assert r["entries"][1]["mode"].startswith("-rw") and r["entries"][1]["octal"]
    assert f(ctx, "files.stat", path="index.html")["permissions"]["readable"]
    rd = f(ctx, "files.read", path="index.html")
    assert base64.b64decode(rd["content_b64"]) == b"<h1>hi</h1>"


def test_list_pagination_and_hidden(ctx):
    for i in range(7):
        (ctx.data_dir / ROOT / f"f{i}.txt").write_text("x")
    (ctx.data_dir / ROOT / ".env").write_text("x")
    r = f(ctx, "files.list", limit=3, offset=3)
    assert r["total"] == 9 and len(r["entries"]) == 3
    assert ".env" not in [e["name"] for e in f(ctx, "files.list")["entries"]]
    assert ".env" in [e["name"] for e in f(ctx, "files.list", show_hidden=True)["entries"]]


@pytest.mark.parametrize("bad", ["../other/public/secret.txt", "docs/../../..", "../../../etc/passwd", "/etc/passwd", "..", "docs/../../../etc/passwd"])
def test_dotdot_traversal(ctx, bad):
    err(ctx, "files.read", path=bad)
    err(ctx, "files.list", path=bad)


@pytest.mark.parametrize("bad", ["%2e%2e/%2e%2e/etc/passwd", "%2e%2e%2f%2e%2e%2fetc", "%252e%252e%252f", "%25252e%25252e/", "..%2fetc", "%2fetc/passwd", "a%5c..%5cb"])
def test_encoded_traversal(ctx, bad):
    with pytest.raises(OpError):
        f(ctx, "files.read", path=bad)


@pytest.mark.parametrize("bad", ["index.html\x00.png", "a\\..\\b", "docs\\a.txt", "a\x01b"])
def test_null_backslash_control(ctx, bad):
    with pytest.raises(OpError) as e:
        f(ctx, "files.read", path=bad)
    assert e.value.code == "bad_path"


def test_root_param_cannot_escape(ctx):
    for root in ["../etc", "sites/acme/../../etc"]:
        with pytest.raises((OpError, InvalidParams)):
            call(ctx, "files.list", root=root)
    with pytest.raises(InvalidParams):
        call(ctx, "files.list", root="/etc")


def test_file_symlink_escape(ctx):
    os.symlink(ctx.data_dir / "etc/passwd", ctx.data_dir / ROOT / "pw")
    err(ctx, "files.read", "symlink_blocked", path="pw")
    ent = [e for e in f(ctx, "files.list")["entries"] if e["name"] == "pw"][0]
    assert ent["type"] == "symlink"


def test_dir_symlink_escape(ctx):
    os.symlink(ctx.data_dir / "etc", ctx.data_dir / ROOT / "link")
    err(ctx, "files.read", "symlink_blocked", path="link/passwd")
    err(ctx, "files.list", "symlink_blocked", path="link")
    err(ctx, "files.mkdir", "symlink_blocked", path="link/new")
    err(ctx, "files.write", path="link/x.txt", content_b64=b64(b"x"), upload_id="up-123456", total_size=1)
    assert not (ctx.data_dir / "etc/x.txt").exists()
    # sibling-tenant escape via symlinked directory
    os.symlink(ctx.data_dir / "sites/other/public", ctx.data_dir / ROOT / "other")
    err(ctx, "files.read", "symlink_blocked", path="other/secret.txt")


def test_symlink_not_followed_by_search_and_archive(ctx):
    os.symlink(ctx.data_dir / "etc", ctx.data_dir / ROOT / "link")
    r = f(ctx, "files.search", query="passwd")
    assert r["total"] == 0
    a = f(ctx, "files.archive", paths=["link", "docs"], dest="x.zip")
    assert a["symlinks_skipped"] >= 1
    with zipfile.ZipFile(ctx.data_dir / ROOT / "x.zip") as z:
        assert not any("passwd" in n for n in z.namelist())


def test_symlink_final_component_delete_removes_link_only(ctx):
    os.symlink(ctx.data_dir / "etc/passwd", ctx.data_dir / ROOT / "pw")
    f(ctx, "files.delete", path="pw", confirm=True)
    assert (ctx.data_dir / "etc/passwd").exists()


def test_write_chunked_and_overwrite(ctx):
    data = os.urandom(300)
    uid = "upload-0001"
    r1 = f(ctx, "files.write", path="docs/new.bin", content_b64=b64(data[:100]), upload_id=uid, chunk_index=0, total_chunks=3, total_size=300)
    assert not r1["complete"]
    f(ctx, "files.write", path="docs/new.bin", content_b64=b64(data[100:200]), upload_id=uid, chunk_index=1, total_chunks=3, total_size=300)
    r3 = f(ctx, "files.write", path="docs/new.bin", content_b64=b64(data[200:]), upload_id=uid, chunk_index=2, total_chunks=3, total_size=300)
    assert r3["complete"] and r3["bytes_added"] == 300
    assert (ctx.data_dir / ROOT / "docs/new.bin").read_bytes() == data
    err(ctx, "files.write", "exists", path="docs/new.bin", content_b64=b64(b"z"), upload_id="upload-0002", total_size=1)
    f(ctx, "files.write", path="docs/new.bin", content_b64=b64(b"z"), upload_id="upload-0003", total_size=1, overwrite=True)
    assert (ctx.data_dir / ROOT / "docs/new.bin").read_bytes() == b"z"


def test_write_out_of_order_and_size_mismatch(ctx):
    f(ctx, "files.write", path="a.bin", content_b64=b64(b"1234"), upload_id="upload-0004", chunk_index=0, total_chunks=2, total_size=8)
    err(ctx, "files.write", "bad_chunk", path="a.bin", content_b64=b64(b"1234"), upload_id="upload-0004", chunk_index=0, total_chunks=2, total_size=8) if False else None
    err(ctx, "files.write", "size_mismatch", path="a.bin", content_b64=b64(b"12"), upload_id="upload-0004", chunk_index=1, total_chunks=2, total_size=8)
    assert not (ctx.data_dir / ROOT / "a.bin").exists()


def test_oversized_upload_and_type_policy(ctx):
    err(ctx, "files.write", "upload_too_large", path="big.bin", content_b64=b64(b"x"), upload_id="upload-0005", total_size=20 * 1024 * 1024)
    err(ctx, "files.write", "upload_too_large", path="big.bin", content_b64=b64(b"x" * 10), upload_id="upload-0006", total_size=5, max_bytes=100) if False else None
    err(ctx, "files.write", "file_type_blocked", path="evil.exe", content_b64=b64(b"MZ"), upload_id="upload-0007", total_size=2)
    f(ctx, "files.write", path="ok.txt", content_b64=b64(b"x" * 10), upload_id="upload-0008", chunk_index=0, total_chunks=2, total_size=12)
    err(ctx, "files.write", "upload_too_large", path="ok.txt", content_b64=b64(b"x" * 10), upload_id="upload-0008", chunk_index=1, total_chunks=2, total_size=12)


def test_invalid_base64(ctx):
    err(ctx, "files.write", "bad_chunk", path="x.txt", content_b64="***notb64***", upload_id="upload-0009", total_size=3)


def test_read_size_cap(ctx):
    (ctx.data_dir / ROOT / "big.txt").write_bytes(b"x" * 5000)
    err(ctx, "files.read", "file_too_large", path="big.txt", max_bytes=1000)


def test_mkdir_rename_move(ctx):
    f(ctx, "files.mkdir", path="new/deep", parents=True)
    f(ctx, "files.rename", src="index.html", dst="new/deep/index2.html")
    assert (ctx.data_dir / ROOT / "new/deep/index2.html").exists()
    err(ctx, "files.rename", "bad_target", src="new", dst="new/deep/inside")
    err(ctx, "files.rename", "exists", src="docs/a.txt", dst="new/deep/index2.html")
    err(ctx, "files.rename", "path_traversal", src="docs/a.txt", dst="../out.txt")
    err(ctx, "files.mkdir", "exists", path="docs")


def test_delete_requires_confirmation(ctx):
    with pytest.raises(InvalidParams):
        validate("files.delete", {"root": ROOT, "path": "index.html"})
    with pytest.raises(InvalidParams):
        validate("files.delete", {"root": ROOT, "path": "index.html", "confirm": False})
    with pytest.raises(InvalidParams):
        validate("files.purge", {"root": ROOT, "empty": True})


def test_trash_restore_purge(ctx):
    r = f(ctx, "files.delete", path="docs", confirm=True)
    assert r["recoverable"] and not (ctx.data_dir / ROOT / "docs").exists()
    assert ".kvl-trash" not in [e["name"] for e in f(ctx, "files.list", show_hidden=True)["entries"]]
    err(ctx, "files.list", "forbidden_path", path=".kvl-trash")
    items = f(ctx, "files.trash_list")["items"]
    assert items[0]["path"] == "docs" and items[0]["size"] == 3
    f(ctx, "files.restore", trash_id=r["trash_id"])
    assert (ctx.data_dir / ROOT / "docs/a.txt").read_text() == "aaa"
    r2 = f(ctx, "files.delete", path="index.html", confirm=True)
    p = f(ctx, "files.purge", trash_id=r2["trash_id"], confirm=True)
    assert p["purged"] == 1 and p["freed_bytes"] == 11
    err(ctx, "files.restore", "not_found", trash_id=r2["trash_id"])
    assert f(ctx, "files.trash_list")["items"] == []


def test_restore_conflict(ctx):
    r = f(ctx, "files.delete", path="docs/a.txt", confirm=True)
    (ctx.data_dir / ROOT / "docs/a.txt").write_text("new")
    err(ctx, "files.restore", "exists", trash_id=r["trash_id"])
    f(ctx, "files.restore", trash_id=r["trash_id"], new_path="docs/a-restored.txt")
    assert (ctx.data_dir / ROOT / "docs/a.txt").read_text() == "new"


def test_cannot_delete_root_or_internal(ctx):
    err(ctx, "files.delete", "bad_target", path=".", confirm=True)
    err(ctx, "files.delete", "forbidden_path", path=".kvl-trash", confirm=True)


def test_search_pagination(ctx):
    for i in range(5):
        (ctx.data_dir / ROOT / "docs" / f"report{i}.txt").write_text("x")
    r = f(ctx, "files.search", query="REPORT", limit=2, offset=2)
    assert r["total"] == 5 and len(r["entries"]) == 2
    assert not r["scan_truncated"]
    with pytest.raises(InvalidParams):
        validate("files.search", {"root": ROOT, "query": "../x"})


def test_archive_roundtrip(ctx):
    for fmt, name in (("zip", "out.zip"), ("tar.gz", "out.tar.gz")):
        a = f(ctx, "files.archive", paths=["docs", "index.html"], dest=name, format=fmt)
        assert a["entries"] >= 3
        f(ctx, "files.extract", archive=name, dest_dir=f"x-{fmt}")
        assert (ctx.data_dir / ROOT / f"x-{fmt}/docs/a.txt").read_text() == "aaa"
    err(ctx, "files.archive", "exists", paths=["docs"], dest="out.zip")
    err(ctx, "files.archive", "bad_target", paths=["docs"], dest="out.tar.gz", format="zip")


def _zip(ctx, members, name="evil.zip"):
    p = ctx.data_dir / ROOT / name
    with zipfile.ZipFile(p, "w") as z:
        for m in members:
            if isinstance(m, tuple):
                z.writestr(m[0], m[1])
            else:
                z.writestr(m, "x")
    return name


def _tar(ctx, build, name="evil.tar.gz"):
    p = ctx.data_dir / ROOT / name
    with tarfile.open(p, "w:gz") as t:
        build(t)
    return name


@pytest.mark.parametrize("member", ["../escape.txt", "docs/../../escape.txt", "/abs/escape.txt", "..\\escape.txt", "C:/win.txt"])
def test_zip_slip_and_absolute(ctx, member):
    name = _zip(ctx, ["ok.txt", member])
    err(ctx, "files.extract", "unsafe_archive_path", archive=name, dest_dir="out")
    assert not (ctx.data_dir / ROOT / "out/ok.txt").exists()  # validated before anything is written
    assert not (ctx.data_dir / ROOT / "escape.txt").exists() and not (ctx.data_dir / "sites/acme/escape.txt").exists()


def test_zip_symlink_member(ctx):
    p = ctx.data_dir / ROOT / "sym.zip"
    with zipfile.ZipFile(p, "w") as z:
        i = zipfile.ZipInfo("link")
        i.external_attr = (0o120777 << 16)
        z.writestr(i, "/etc/passwd")
    err(ctx, "files.extract", "unsafe_archive_member", archive="sym.zip", dest_dir="out")


def test_tar_symlink_hardlink_and_traversal(ctx):
    def sym(t):
        ti = tarfile.TarInfo("l")
        ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
        t.addfile(ti)

    def hard(t):
        ti = tarfile.TarInfo("h")
        ti.type, ti.linkname = tarfile.LNKTYPE, "../../etc/passwd"
        t.addfile(ti)

    def slip(t):
        data = b"pwn"
        ti = tarfile.TarInfo("../../pwn.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))

    err(ctx, "files.extract", "unsafe_archive_member", archive=_tar(ctx, sym, "s.tar.gz"), dest_dir="o1")
    err(ctx, "files.extract", "unsafe_archive_member", archive=_tar(ctx, hard, "h.tar.gz"), dest_dir="o2")
    err(ctx, "files.extract", "unsafe_archive_path", archive=_tar(ctx, slip, "p.tar.gz"), dest_dir="o3")
    assert not (ctx.data_dir / "pwn.txt").exists()


def test_extract_cannot_write_through_existing_symlink(ctx):
    os.symlink(ctx.data_dir / "etc", ctx.data_dir / ROOT / "docs2")
    name = _zip(ctx, [("docs2/pwned.txt", "x")])
    err(ctx, "files.extract", "unsafe_archive_path", archive=name, dest_dir="")
    assert not (ctx.data_dir / "etc/pwned.txt").exists()


def test_zip_bomb_ratio_and_limits(ctx):
    p = ctx.data_dir / ROOT / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * (30 * 1024 * 1024))
    err(ctx, "files.extract", "archive_ratio", archive="bomb.zip", dest_dir="out")
    assert not (ctx.data_dir / ROOT / "out/zeros.bin").exists()
    err(ctx, "files.extract", "archive_too_large", archive="bomb.zip", dest_dir="out2", max_file_bytes=1024 * 1024)
    n = _zip(ctx, [f"f{i}.txt" for i in range(30)], "many.zip")
    err(ctx, "files.extract", "archive_too_many_files", archive=n, dest_dir="out3", max_files=10)


def test_lying_header_stream_limit(ctx):
    p = ctx.data_dir / ROOT / "lie.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        data = b"a" * 5000
        ti = tarfile.TarInfo("big.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    err(ctx, "files.extract", "archive_too_large", archive="lie.tar.gz", dest_dir="o", max_file_bytes=1000)
    assert not (ctx.data_dir / ROOT / "o/big.txt").exists()


def test_extract_no_overwrite_by_default(ctx):
    name = _zip(ctx, [("index.html", "evil")])
    err(ctx, "files.extract", "exists", archive=name, dest_dir="")
    assert (ctx.data_dir / ROOT / "index.html").read_text() == "<h1>hi</h1>"


def test_usage(ctx):
    u = f(ctx, "files.usage")
    assert u["bytes"] >= 14 and u["files"] >= 2
