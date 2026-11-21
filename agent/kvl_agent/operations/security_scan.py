"""Malware / webshell scan of one site's files (read-only).

Two engines:
- Built-in heuristics (always): well-known PHP webshell/backdoor patterns, obfuscation chains, uploads that contain PHP,
  suspicious file names. Findings are rated high/medium/low; this is a signal for review, not a verdict.
- ClamAV (`clamscan`) when installed on the server, run with an argv list on the site root only.
Nothing is modified: quarantine = the existing file-manager "move to trash" action. Results contain paths, rule names
and line numbers only (no file contents).
"""
import os
import re
from pathlib import Path

from pydantic import Field

from kvl_agent.context import OpContext, allow_binary
from kvl_agent.operations.files import Site
from kvl_agent.registry import operation
from kvl_agent.safe_paths import TRASH_DIR, OpError, site_root

allow_binary("clamscan")

SCAN_EXT = {".php", ".phtml", ".php3", ".php4", ".php5", ".php7", ".phar", ".inc", ".js", ".htaccess", ".ico", ".png", ".jpg", ".gif", ".txt", ".html", ".htm", ".sh", ".pl", ".py", ".cgi"}
MAX_FILE = 4 * 1024 * 1024
MAX_FINDINGS = 500

RULES: list[tuple[str, str, re.Pattern]] = [
    ("high", "eval_decode_chain", re.compile(rb"(?i)\b(eval|assert)\s*\(\s*(base64_decode|gzinflate|gzuncompress|gzdecode|str_rot13|strrev|rawurldecode|hex2bin)\s*\(")),
    ("high", "exec_request_input", re.compile(rb"(?i)\b(system|shell_exec|passthru|exec|popen|proc_open|pcntl_exec)\s*\(\s*\$_(GET|POST|REQUEST|COOKIE|SERVER)\b")),
    ("high", "eval_request_input", re.compile(rb"(?i)\b(eval|assert|create_function)\s*\(\s*(stripslashes\s*\()?\s*\$_(GET|POST|REQUEST|COOKIE)\b")),
    ("high", "preg_replace_eval", re.compile(rb"(?i)preg_replace\s*\(\s*['\"](.).{0,200}?\1[a-z]*e[a-z]*['\"]\s*,")),
    ("high", "known_webshell", re.compile(rb"(?i)(FilesMan|c99shell|r57shell|WSO\s*[0-9.]+|b374k|IndoXploit|Mini\s*Shell|AnonymousFox|alfa\s*team|uname\s*-a.{0,40}passthru)")),
    ("high", "variable_function_input", re.compile(rb"\$_(GET|POST|REQUEST|COOKIE)\s*\[[^\]]{1,40}\]\s*\(\s*\$_(GET|POST|REQUEST|COOKIE)")),
    ("medium", "long_base64_blob", re.compile(rb"['\"][A-Za-z0-9+/]{2000,}={0,2}['\"]")),
    ("medium", "hex_escaped_code", re.compile(rb"(\\x[0-9a-fA-F]{2}){40,}")),
    ("medium", "chr_chain", re.compile(rb"(?i)(chr\s*\(\s*\d+\s*\)\s*\.\s*){15,}")),
    ("medium", "file_put_remote", re.compile(rb"(?i)file_put_contents\s*\([^;]{0,200}(file_get_contents|curl_exec)\s*\(\s*['\"]https?://")),
    ("medium", "htaccess_php_handler", re.compile(rb"(?i)(AddHandler|AddType|SetHandler)\s+[^\n]*(php|x-httpd)[^\n]*\.(jpe?g|png|gif|ico|txt)")),
    ("low", "error_suppressed_include", re.compile(rb"(?i)@\s*(include|require)(_once)?\s*\(?\s*['\"][^'\"]*\.(ico|png|jpg|gif|txt)['\"]")),
]
PHP_OPEN = re.compile(rb"<\?(php|=)", re.I)
SUSPICIOUS_NAME = re.compile(r"(?i)^(\.\w*shell\w*\.php|wso\w*\.php|c99\w*\.php|r57\w*\.php|.*\.php\.(jpg|png|gif|txt)|.*\.(jpg|png|gif|ico)\.php)$")


class Scan(Site):
    max_files: int = Field(50_000, ge=100, le=200_000)
    use_clamav: bool = True


def _line_no(data: bytes, pos: int) -> int:
    return data.count(b"\n", 0, pos) + 1


def scan_bytes(rel: str, data: bytes) -> list[dict]:
    out = []
    name = rel.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    for sev, rule, pat in RULES:
        m = pat.search(data)
        if m:
            out.append({"path": rel, "severity": sev, "rule": rule, "line": _line_no(data, m.start())})
    in_uploads = "/uploads/" in f"/{rel}"
    if ext in {".ico", ".png", ".jpg", ".gif", ".txt"} and PHP_OPEN.search(data[:65536]):
        out.append({"path": rel, "severity": "high", "rule": "php_in_non_php_file", "line": _line_no(data, PHP_OPEN.search(data).start())})
    elif in_uploads and ext in {".php", ".phtml", ".phar", ".php5", ".php7"}:
        out.append({"path": rel, "severity": "medium", "rule": "php_in_uploads", "line": 1})
    if SUSPICIOUS_NAME.match(name):
        out.append({"path": rel, "severity": "medium", "rule": "suspicious_file_name", "line": None})
    return out


def _clamav(ctx: OpContext, root: Path) -> tuple[str, list[dict]]:
    if not ctx.which("clamscan"):
        return "not_installed", []
    r = ctx.run(["clamscan", "-r", "-i", "--no-summary", "--max-filesize=4M", "--max-scansize=200M", f"--exclude-dir=^{re.escape(str(root / TRASH_DIR))}", str(root)], timeout=900)
    # exit 0 = clean, 1 = infected found, 2 = error
    if r.returncode not in (0, 1):
        return "error", []
    found = []
    prefix = str(root) + os.sep
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^(.*): (.+) FOUND$", line)
        if m and m.group(1).startswith(prefix):
            found.append({"path": m.group(1)[len(prefix):], "severity": "high", "rule": f"clamav:{m.group(2)[:80]}", "line": None})
    return "ok", found


@operation("security.scan", Scan, timeout=1200)
def scan(p: Scan, ctx: OpContext) -> dict:
    try:
        root = site_root(ctx, p.root)
    except OpError as e:
        return {"ok": False, "status": e.code, "message": e.message}
    findings: list[dict] = []
    files = skipped = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)) and not (rel_dir == "." and d == TRASH_DIR)]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SCAN_EXT and fn != ".htaccess":
                continue
            files += 1
            if files > p.max_files:
                truncated = True
                break
            try:
                if os.path.getsize(full) > MAX_FILE:
                    skipped += 1
                    continue
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                skipped += 1
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            findings.extend(scan_bytes(rel, data))
            if len(findings) >= MAX_FINDINGS:
                truncated = True
                break
        if truncated:
            break
    clam_state, clam = _clamav(ctx, root) if p.use_clamav else ("disabled", [])
    findings.extend(clam)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["path"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in order}
    return {"ok": True, "status": "scanned", "files_scanned": min(files, p.max_files), "files_skipped": skipped, "truncated": truncated,
            "clamav": clam_state, "counts": counts, "findings": findings[:MAX_FINDINGS], "verdict": "suspicious" if counts["high"] else ("review" if counts["medium"] else "clean")}
