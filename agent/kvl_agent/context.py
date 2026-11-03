"""Execution context handed to operation handlers: sandboxed paths and a shell-free subprocess runner."""
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_BINARIES: set[str] = set()


def allow_binary(*names: str) -> None:
    """Operation modules declare which executables they may invoke (bare names, resolved via PATH)."""
    ALLOWED_BINARIES.update(names)


class PathEscape(Exception):
    pass


class BinaryNotAllowed(Exception):
    pass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass
class OpContext:
    data_dir: Path  # sandbox root: all file writes happen under here
    dry_run: bool = False
    max_output: int = 64 * 1024
    extra: dict = field(default_factory=dict)

    def path(self, *parts: str) -> Path:
        """Resolve inside data_dir. Rejects traversal and symlink escapes (checked on the fully resolved path)."""
        root = self.data_dir.resolve()
        p = (root.joinpath(*parts)).resolve()
        if p != root and root not in p.parents:
            raise PathEscape(f"path escapes sandbox: {parts}")
        return p

    def which(self, binary: str) -> str | None:
        return shutil.which(binary)

    def run(self, argv: list[str], *, timeout: int = 30, cwd: Path | None = None, env: dict | None = None) -> RunResult:
        """Run an allow-listed binary WITHOUT a shell. Output is capped; the process is killed on timeout."""
        if not argv or os.path.basename(argv[0]) != argv[0] or argv[0] not in ALLOWED_BINARIES:
            raise BinaryNotAllowed(argv[0] if argv else "")
        exe = shutil.which(argv[0])
        if exe is None:
            return RunResult(127, "", f"{argv[0]}: not installed")
        try:
            p = subprocess.run([exe, *argv[1:]], capture_output=True, timeout=timeout, cwd=cwd, env={"PATH": os.environ.get("PATH", ""), "LANG": "C", **(env or {})}, shell=False)
        except subprocess.TimeoutExpired as e:
            return RunResult(124, (e.stdout or b"").decode(errors="replace")[: self.max_output], "timed out", timed_out=True)
        out, err = p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
        trunc = len(out) > self.max_output or len(err) > self.max_output
        return RunResult(p.returncode, out[: self.max_output], err[: self.max_output], truncated=trunc)
