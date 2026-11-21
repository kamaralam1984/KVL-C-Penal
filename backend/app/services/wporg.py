"""Latest versions from the official WordPress.org API (fixed host, https, no redirects, short cache).

Only used to (a) show "update available" and (b) obtain the official download URL for an update.
"""
import re
import time
from collections.abc import Callable
from typing import Any

from app.ops.wordpress import DL_RE, SLUG_RE

API = "https://api.wordpress.org"
TTL = 6 * 3600
_cache: dict[str, tuple[float, Any]] = {}

Fetch = Callable[[str], Any]


def http_json(url: str) -> Any:
    import httpx

    assert url.startswith(API + "/")
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as c:
        r = c.get(url, headers={"User-Agent": "KVL-Panel"})
        if r.status_code != 200:
            return None
        return r.json()


fetch: Fetch = http_json


def _cached(key: str, url: str) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        data = fetch(url)
    except Exception:  # noqa: BLE001 - WordPress.org being down must never break the toolkit
        data = None
    _cache[key] = (time.time(), data)
    return data


def latest(kind: str, slug: str) -> dict[str, str] | None:
    """{"version", "download"} for a wordpress.org plugin/theme, or None (not hosted on wordpress.org / unavailable)."""
    if not SLUG_RE.fullmatch(slug):
        return None
    if kind == "plugin":
        url = f"{API}/plugins/info/1.2/?action=plugin_information&request[slug]={slug}&request[fields][sections]=0"
    else:
        url = f"{API}/themes/info/1.2/?action=theme_information&request[slug]={slug}&request[fields][sections]=0"
    d = _cached(f"{kind}:{slug}", url)
    if not isinstance(d, dict) or not isinstance(d.get("version"), str) or not isinstance(d.get("download_link"), str):
        return None
    m = DL_RE.fullmatch(d["download_link"])
    if not m or m.group(1) != kind or m.group(2) != slug or not re.fullmatch(r"[0-9][0-9A-Za-z.\-]{0,30}", d["version"]):
        return None
    return {"version": d["version"], "download": d["download_link"]}


def latest_core() -> str | None:
    d = _cached("core", f"{API}/core/version-check/1.7/")
    try:
        v = d["offers"][0]["current"]
    except (TypeError, KeyError, IndexError):
        return None
    return v if isinstance(v, str) and re.fullmatch(r"\d+\.\d+(\.\d+)?", v) else None


def newer(a: str | None, b: str | None) -> bool:
    """True when b is a newer version than a (numeric compare of dotted parts)."""
    if not a or not b:
        return False

    def parts(v: str) -> list[int]:
        return [int(x) for x in re.findall(r"\d+", v)[:4]]

    return parts(b) > parts(a)
