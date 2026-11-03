"""ACME certificate operations through the `certbot` binary. Metadata only: private keys are never read or returned.

Certbot state lives under <data_dir>/letsencrypt/<staging|production>/ (config, work, logs). Staging is the default;
production requires environment="production" AND confirm_production=true.
"""
import re
from datetime import UTC
from pathlib import Path
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kvl_agent.context import allow_binary
from kvl_agent.registry import operation
from kvl_agent.safe_paths import OpError, split_rel

allow_binary("certbot")
_HOST = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CertBase(_B):
    domain: str = Field(pattern=_HOST, max_length=253)
    environment: Literal["staging", "production"] = "staging"
    confirm_production: bool = False

    @model_validator(mode="after")
    def _prod(self):
        if self.environment == "production" and not self.confirm_production:
            raise ValueError("production issuance requires confirm_production=true")
        return self


class RequestP(CertBase):
    email: str = Field(pattern=r"^[^@\s]{1,64}@[A-Za-z0-9.-]{3,253}$")
    webroot: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$", description="Webroot relative to the agent data dir")


class RenewP(CertBase):
    pass


class InspectP(CertBase):
    pass


def _dirs(ctx, env: str) -> dict[str, Path]:
    base = ctx.path("letsencrypt", env)
    return {"config": base / "config", "work": base / "work", "logs": base / "logs"}


def _clean(text: str) -> str:
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", "[key material removed]", text, flags=re.S)
    return " ".join(text.split())[-500:]


def cert_metadata(ctx, domain: str, env: str) -> dict:
    """Parse cert.pem only (never privkey.pem)."""
    pem = _dirs(ctx, env)["config"] / "live" / domain / "cert.pem"
    if not pem.is_file():
        raise OpError("cert_not_found", "No certificate found for this domain.")
    cert = x509.load_pem_x509_certificate(pem.read_bytes())
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    return {
        "domain": domain, "environment": env, "issuer": cert.issuer.rfc4514_string()[:200],
        "not_before": cert.not_valid_before_utc.astimezone(UTC).isoformat(), "not_after": cert.not_valid_after_utc.astimezone(UTC).isoformat(),
        "serial": format(cert.serial_number, "x"), "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(), "sans": sans,
    }


def _run(ctx, args: list[str], d: dict[str, Path], timeout: int):
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return ctx.run(["certbot", *args, "--config-dir", str(d["config"]), "--work-dir", str(d["work"]), "--logs-dir", str(d["logs"]), "--non-interactive"], timeout=timeout)


@operation("ssl.request_certificate", RequestP, timeout=300)
def request_certificate(p: RequestP, ctx) -> dict:
    parts = split_rel(p.webroot, allow_internal=True)
    webroot = ctx.path(*parts)
    if not webroot.is_dir():
        raise OpError("webroot_missing", "Webroot does not exist on this server.")
    d = _dirs(ctx, p.environment)
    if ctx.dry_run:
        return {"ok": True, "dry_run": True, "environment": p.environment}
    if ctx.which("certbot") is None:
        raise OpError("certbot_not_installed", "certbot is not installed on this server; certificates cannot be issued.")
    args = ["certonly", "--webroot", "-w", str(webroot), "-d", p.domain, "--agree-tos", "-m", p.email, "--keep-until-expiring"]
    if p.environment == "staging":
        args.append("--staging")
    r = _run(ctx, args, d, 280)
    if not r.ok:
        raise OpError("acme_failed", _clean(r.stderr or r.stdout) or "certbot failed")
    return {"ok": True, **cert_metadata(ctx, p.domain, p.environment)}


@operation("ssl.renew", RenewP, timeout=300)
def renew(p: RenewP, ctx) -> dict:
    d = _dirs(ctx, p.environment)
    if ctx.dry_run:
        return {"ok": True, "dry_run": True}
    if ctx.which("certbot") is None:
        raise OpError("certbot_not_installed", "certbot is not installed on this server; certificates cannot be renewed.")
    r = _run(ctx, ["renew", "--cert-name", p.domain], d, 280)
    if not r.ok:
        raise OpError("renewal_failed", _clean(r.stderr or r.stdout) or "certbot renew failed")
    return {"ok": True, **cert_metadata(ctx, p.domain, p.environment)}


@operation("ssl.inspect", InspectP, timeout=30)
def inspect(p: InspectP, ctx) -> dict:
    return {"ok": True, **cert_metadata(ctx, p.domain, p.environment)}
