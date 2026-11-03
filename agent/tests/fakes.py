"""A scriptable OpContext: no real git/npm/pm2/mysql/psql is ever run. Records every argv."""
from pathlib import Path

from kvl_agent.context import OpContext, RunResult
from kvl_agent.registry import load_operations


class FakeCtx(OpContext):
    def __init__(self, data_dir: Path, **kw):
        load_operations()
        super().__init__(data_dir=data_dir, **kw)
        self.calls: list[list[str]] = []
        self.fail: dict[str, str] = {}  # key (e.g. "git", "npm", "pm2:startOrReload") -> stderr
        self.missing: set[str] = set()
        self.stdout: dict[str, str] = {}
        self.files_on_clone = {"package.json": "{}", "package-lock.json": "{}", "server.js": "//", "index.php": "<?php", "requirements.txt": "flask\n"}
        self.node_version = "v20.11.1"
        self.scripts: list[str] = []  # contents of sql scripts handed to mysql/psql (captured before they are deleted)
        self.envs: list[dict] = []
        self.fail_nth: dict[str, dict[int, str]] = {}  # key -> {call index: stderr}
        self._count: dict[str, int] = {}
        self.extra.setdefault("http_get", lambda port, path: 200)
        self.extra.setdefault("health_retries", 2)
        self.extra.setdefault("sleep", lambda s: None)

    def which(self, binary: str):
        return None if binary in self.missing else f"/usr/bin/{binary}"

    def run(self, argv, *, timeout=30, cwd=None, env=None):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        exe = argv[0]
        n = self._count.get(exe, 0)
        self._count[exe] = n + 1
        for a in argv:
            if a.startswith("source "):
                self.scripts.append(Path(a[7:]).read_text())
        if "-f" in argv and exe == "psql":
            self.scripts.append(Path(argv[argv.index("-f") + 1]).read_text())
        if n in self.fail_nth.get(exe, {}):
            return RunResult(1, "", self.fail_nth[exe][n])
        keys = [exe, f"{exe}:{argv[1]}" if len(argv) > 1 else exe]
        for k in keys:
            if k in self.fail:
                return RunResult(1, "", self.fail[k])
        key = keys[-1]
        if exe == "git" and argv[1] == "clone":
            dest = Path(argv[-1])
            dest.mkdir(parents=True)
            (dest / ".git").mkdir()
            for n, c in self.files_on_clone.items():
                (dest / n).write_text(c)
            return RunResult(0, "", "")
        if exe == "git" and "rev-parse" in argv:
            return RunResult(0, "a" * 40 + "\n", "")
        if exe == "node":
            return RunResult(0, self.node_version + "\n", "")
        if exe.startswith("python3") and "venv" in argv:
            Path(argv[-1], "bin").mkdir(parents=True)
            return RunResult(0, "", "")
        if key in self.stdout:
            return RunResult(0, self.stdout[key], "")
        if exe in self.stdout and not (exe == "mysql" and "--version" in argv):
            return RunResult(0, self.stdout[exe], "")
        if exe == "pm2" and argv[1] == "pid":
            return RunResult(0, "4242\n", "")
        return RunResult(0, "", "")

    def called(self, *prefix) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]
