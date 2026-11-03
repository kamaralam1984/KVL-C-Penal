import os
import stat
from pathlib import Path

FAKE_NGINX = """#!/bin/sh
# Fake nginx for tests. Flags are files in $FAKE_NGINX_STATE.
S="$(dirname "$0")/../fake-nginx-state"
echo "$@" >> "$S/calls.log"
case "$1" in
  -t)
    if [ -f "$S/fail_test" ]; then echo "nginx: [emerg] unknown directive in test" >&2; exit 1; fi
    echo "syntax is ok" >&2; exit 0;;
  -s)
    if [ -f "$S/fail_reload" ]; then echo "reload failed" >&2; exit 1; fi
    echo reload >> "$S/reloads.log"; exit 0;;
esac
exit 2
"""


def install_fake_nginx(tmp_path: Path, monkeypatch) -> Path:
    state = tmp_path / "fake-nginx-state"
    state.mkdir()
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    exe = bindir / "nginx"
    exe.write_text(FAKE_NGINX)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NGINX_STATE", str(state))
    return state


def hide_binary(tmp_path: Path, monkeypatch) -> None:
    d = tmp_path / "emptybin"
    d.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(d))
