"""Pluggable DNS TXT resolution for domain-ownership verification.

The real resolver uses dnspython (read-only TXT queries; never changes DNS). When dnspython is missing the
`UnavailableResolver` is used and verification reports `unavailable` instead of pretending. Tests inject a fake.
"""
from typing import Protocol

CHALLENGE_PREFIX = "_kvl-verify"
VALUE_PREFIX = "kvl-verify="


class ResolverUnavailable(Exception):
    pass


class DnsResolver(Protocol):
    def txt(self, name: str) -> list[str]:
        """TXT strings at `name`; [] when no records / NXDOMAIN. Raises ResolverUnavailable on infrastructure errors."""


class UnavailableResolver:
    def txt(self, name: str) -> list[str]:
        raise ResolverUnavailable("DNS resolver is not available on this server (dnspython not installed).")


class DnspythonResolver:
    def __init__(self, timeout: float = 4.0) -> None:
        import dns.resolver  # noqa: F401  (import check)

        self.timeout = timeout

    def txt(self, name: str) -> list[str]:
        import dns.exception
        import dns.resolver

        r = dns.resolver.Resolver()
        r.lifetime = self.timeout
        try:
            ans = r.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.resolver.LifetimeTimeout) as e:
            raise ResolverUnavailable(f"DNS lookup failed: {type(e).__name__}") from None
        out = []
        for rr in ans:
            out.append(b"".join(rr.strings).decode("utf-8", errors="replace"))
        return out


def default_resolver() -> DnsResolver:
    try:
        return DnspythonResolver()
    except ImportError:
        return UnavailableResolver()


# Overridable factory (tests monkeypatch this).
resolver_factory = default_resolver


def challenge_for(hostname: str, token: str) -> dict:
    return {"type": "TXT", "name": f"{CHALLENGE_PREFIX}.{hostname}", "value": f"{VALUE_PREFIX}{token}", "ttl": 300}


def check_txt(resolver: DnsResolver, hostname: str, token: str) -> bool:
    expected = f"{VALUE_PREFIX}{token}"
    return any(v.strip().strip('"') == expected for v in resolver.txt(f"{CHALLENGE_PREFIX}.{hostname}"))
