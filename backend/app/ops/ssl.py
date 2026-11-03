"""Control-plane mirror of agent ACME operation parameters (agent/kvl_agent/operations/ssl.py)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ops import register_op

HOST_RE = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CertBase(_B):
    domain: str = Field(pattern=HOST_RE, max_length=253)
    environment: Literal["staging", "production"] = "staging"
    confirm_production: bool = False

    @model_validator(mode="after")
    def _prod(self):
        if self.environment == "production" and not self.confirm_production:
            raise ValueError("production issuance requires confirm_production=true")
        return self


@register_op("ssl.request_certificate", timeout=300)
class RequestP(CertBase):
    email: str = Field(pattern=r"^[^@\s]{1,64}@[A-Za-z0-9.-]{3,253}$")
    webroot: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,254}$")


@register_op("ssl.renew", timeout=300)
class RenewP(CertBase):
    pass


@register_op("ssl.inspect", timeout=30)
class InspectP(CertBase):
    pass
