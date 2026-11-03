"""Structured JSON logging. Values under secret-looking keys and bearer-style strings are never emitted."""
import json
import logging
import re
import sys
import time

_SECRET_KEY = re.compile(r"(secret|token|password|passwd|signature|authorization|api[_-]?key)", re.I)
_SECRET_VAL = re.compile(r"(?i)(bearer\s+\S+|kvl_enr_\S+)")


def scrub(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VAL.sub("[redacted]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if extra:
            out.update(scrub(extra))
        if record.exc_info:
            out["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO", stream=None) -> logging.Logger:
    log = logging.getLogger("kvl_agent")
    log.handlers.clear()
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(JsonFormatter())
    log.addHandler(h)
    log.setLevel(level.upper())
    log.propagate = False
    return log


def event(log: logging.Logger, msg: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, msg, extra={"fields": fields})
