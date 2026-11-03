"""SSRF-safe outbound URL validation. https only; every resolved address must be globally routable.

Callers MUST re-run `resolve_public()` at connection time (DNS can change between save and delivery) and connect to the
returned address, not to the hostname again (prevents DNS rebinding).
"""
import ipaddress
import re
import socket
from urllib.parse import urlsplit

ALLOWED_PORTS = {443, 8443}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".lan", ".home.arpa", ".intranet", ".corp")
BLOCKED_NAMES = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*$", re.I)


class UnsafeURL(ValueError):
    pass


def resolve(host: str) -> list[str]:
    """Default resolver. Tests replace this module attribute with a fake."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURL("hostname does not resolve") from e
    return sorted({i[4][0] for i in infos})


def ip_is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


def parse_url(url: str) -> tuple[str, int, str]:
    """Static checks. Returns (ascii hostname, port, path+query). Raises UnsafeURL."""
    if not isinstance(url, str) or len(url) > 500 or any(c in url for c in "\\\x00\r\n\t ") or any(ord(c) < 32 for c in url):
        raise UnsafeURL("invalid URL")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURL("only https URLs are allowed")
    if "@" in parts.netloc:
        raise UnsafeURL("credentials in URLs are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("missing host")
    try:
        port = parts.port or 443
    except ValueError as e:
        raise UnsafeURL("invalid port") from e
    if port not in ALLOWED_PORTS:
        raise UnsafeURL("port not allowed")
    if ":" in host or host.startswith("[") or _NUMERIC_HOST.match(host):
        raise UnsafeURL("IP address literals are not allowed; use a DNS name")
    try:
        socket.inet_aton(host)
        raise UnsafeURL("IP address literals are not allowed; use a DNS name")
    except OSError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise UnsafeURL("invalid hostname") from e
    if host in BLOCKED_NAMES or host.endswith(BLOCKED_SUFFIXES) or "." not in host:
        raise UnsafeURL("internal hostnames are not allowed")
    path = parts.path or "/"
    return host, port, path + (f"?{parts.query}" if parts.query else "")


def resolve_public(host: str) -> list[str]:
    addrs = resolve(host)
    if not addrs:
        raise UnsafeURL("hostname does not resolve")
    bad = [a for a in addrs if not ip_is_public(a)]
    if bad:
        raise UnsafeURL("hostname resolves to a non-public address")
    return addrs


def validate_public_url(url: str, *, resolve_dns: bool = True) -> tuple[str, int, str, list[str]]:
    host, port, path = parse_url(url)
    return host, port, path, (resolve_public(host) if resolve_dns else [])
