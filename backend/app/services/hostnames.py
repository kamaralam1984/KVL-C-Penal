"""Strict hostname validation and normalisation (IDNA 2008 / UTS46). Output is lower-case ASCII (punycode)."""
import ipaddress
import re
import unicodedata

import idna

LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".arpa", ".lan", ".home", ".corp", ".invalid")
BLOCKED_NAMES = {"localhost"}


class HostnameError(ValueError):
    pass


def normalize_hostname(raw: str, *, allow_apex_only: bool = False) -> tuple[str, str]:
    """Return (ascii_hostname, unicode_hostname) or raise HostnameError with a user-safe message."""
    if not isinstance(raw, str) or not raw:
        raise HostnameError("Hostname is required.")
    if raw != raw.strip() or any(c.isspace() for c in raw):
        raise HostnameError("Hostname must not contain whitespace.")
    if len(raw) > 253 * 2:
        raise HostnameError("Hostname is too long.")
    for ch in raw:
        if ord(ch) > 127:
            cat = unicodedata.category(ch)
            if not (cat[0] in {"L", "N", "M"}):  # rejects ideographic full stops, zero-width joiners, bidi marks, symbols
                raise HostnameError("Hostname contains an unsupported character.")
        elif not (ch.isalnum() or ch in ".-"):
            raise HostnameError("Hostname may only contain letters, digits, hyphens and dots (no wildcards, ports, underscores or paths).")
    if raw.endswith(".") or raw.startswith(".") or ".." in raw:
        raise HostnameError("Hostname must not start or end with a dot or contain empty labels.")
    try:
        ascii_name = idna.encode(raw, uts46=True, std3_rules=True, transitional=False).decode("ascii").lower()
    except (idna.IDNAError, UnicodeError) as e:
        raise HostnameError(f"Invalid internationalised hostname: {e}") from None
    if len(ascii_name) > 253:
        raise HostnameError("Hostname is too long (max 253 characters).")
    labels = ascii_name.split(".")
    if len(labels) < 2:
        raise HostnameError("Hostname must include a top-level domain, e.g. example.com.")
    for lab in labels:
        if not LABEL_RE.match(lab):
            raise HostnameError(f"Invalid label '{lab[:20]}': 1-63 chars, letters/digits/hyphens, no leading or trailing hyphen.")
        if lab.startswith("xn--"):
            try:
                idna.decode(lab)
            except idna.IDNAError:
                raise HostnameError("Invalid punycode label.") from None
        elif lab[2:4] == "--":
            raise HostnameError("Labels with hyphens in the 3rd and 4th position are reserved.")
    if labels[-1].isdigit() or re.fullmatch(r"[0-9.]+", ascii_name):
        raise HostnameError("IP addresses and numeric hostnames are not allowed.")
    try:
        ipaddress.ip_address(ascii_name)
        raise HostnameError("IP addresses are not allowed.")
    except ValueError:
        pass
    if ascii_name in BLOCKED_NAMES or ascii_name.endswith(BLOCKED_SUFFIXES):
        raise HostnameError("This hostname is reserved and cannot be used.")
    try:
        unicode_name = idna.decode(ascii_name)
    except idna.IDNAError:
        unicode_name = ascii_name
    return ascii_name, unicode_name


def is_subdomain_of(host: str, parent: str) -> bool:
    return host != parent and host.endswith("." + parent)


def ancestors(host: str) -> list[str]:
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(1, len(labels) - 1)]
