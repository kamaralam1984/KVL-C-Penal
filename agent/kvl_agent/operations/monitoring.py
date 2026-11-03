from pydantic import BaseModel, ConfigDict

from kvl_agent.metrics import MetricsCollector
from kvl_agent.registry import operation
from kvl_agent.services import check_services


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


_collector = MetricsCollector()


@operation("agent.collect_metrics", NoParams, timeout=30)
def collect_metrics(_p: NoParams, _ctx) -> dict:
    return {"metrics": _collector.collect()}


@operation("agent.service_status", NoParams, timeout=30)
def service_status(_p: NoParams, _ctx) -> dict:
    return {"services": check_services()}
