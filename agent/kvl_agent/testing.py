from pathlib import Path

from kvl_agent.context import OpContext
from kvl_agent.registry import load_operations


def make_ctx(tmp_path: Path, **kw) -> OpContext:
    """Context for unit-testing operation handlers against a temp sandbox."""
    load_operations()
    return OpContext(data_dir=tmp_path, **kw)
