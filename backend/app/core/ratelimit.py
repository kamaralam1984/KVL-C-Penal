"""Fixed-window rate limiter. Redis in normal operation; in-process fallback if Redis is unreachable."""
import time
from collections import defaultdict

import redis
from fastapi import HTTPException

from app.core.config import get_settings

_mem: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_client: redis.Redis | None = None
_redis_down_until = 0.0


def _redis() -> redis.Redis | None:
    global _client, _redis_down_until
    if time.time() < _redis_down_until:
        return None
    try:
        if _client is None:
            _client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.3, socket_connect_timeout=0.3)
        _client.ping()
        return _client
    except redis.RedisError:
        _client = None
        _redis_down_until = time.time() + 15
        return None


def hit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """Count a hit. Returns (allowed, retry_after_seconds)."""
    k = f"rl:{get_settings().env}:{key}"
    r = _redis()
    if r is not None:
        try:
            n = r.incr(k)
            if n == 1:
                r.expire(k, window)
            ttl = r.ttl(k)
            return n <= limit, max(ttl, 1)
        except redis.RedisError:
            pass
    now = time.time()
    n, reset = _mem[k]
    if now >= reset:
        n, reset = 0, now + window
    n += 1
    _mem[k] = (n, reset)
    return n <= limit, max(int(reset - now), 1)


def enforce(key: str, limit: int, window: int) -> None:
    ok, retry = hit(key, limit, window)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "message": "Too many attempts. Try again later.", "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )


def reset_all() -> None:
    """Test helper."""
    _mem.clear()
    r = _redis()
    if r is not None:
        for k in r.scan_iter(f"rl:{get_settings().env}:*"):
            r.delete(k)
