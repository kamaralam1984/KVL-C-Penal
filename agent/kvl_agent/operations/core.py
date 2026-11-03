import platform
import time

from pydantic import BaseModel, ConfigDict

from kvl_agent import __version__
from kvl_agent.registry import operation


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@operation("agent.ping", NoParams, timeout=10)
def ping(_p: NoParams, _ctx) -> dict:
    return {"pong": True, "version": __version__, "time": time.time(), "host": platform.node()}
