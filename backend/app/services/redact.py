"""Secret redaction for log text and job results. Used for application logs, job results and error messages."""
import re
from typing import Any

REDACTED = "[REDACTED]"
_KEYS = r"(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|auth(?:orization)?|credential|client[_-]?secret|database_url|dsn)"
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), REDACTED),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1" + REDACTED + "@"),  # scheme://user:pass@host
    (re.compile(rf"(?i)(\b[\w.-]*{_KEYS}[\w.-]*\b\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"), r"\1" + REDACTED),
    (re.compile(rf"(?i)(\"[\w.-]*{_KEYS}[\w.-]*\"\s*:\s*)(\"[^\"]*\"|[^\s,}}]+)"), r"\1" + REDACTED),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),  # JWT
]
_SENSITIVE_KEY = re.compile(rf"(?i){_KEYS}")


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    for s in extra_secrets or []:
        if s and len(s) >= 4:
            text = text.replace(s, REDACTED)
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact dict values under sensitive keys and secrets embedded in strings."""
    if isinstance(value, dict):
        return {k: (REDACTED if _SENSITIVE_KEY.search(str(k)) else redact_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
