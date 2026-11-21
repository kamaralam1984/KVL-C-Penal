"""Control-plane mirror of agent/kvl_agent/operations/security_scan.py (read-only malware scan)."""
from pydantic import Field

from app.ops import register_op
from app.ops.files import Site


@register_op("security.scan", timeout=1200)
class Scan(Site):
    max_files: int = Field(50_000, ge=100, le=200_000)
    use_clamav: bool = True
