from pydantic import BaseModel, ConfigDict

from app.ops import register_op


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register_op("agent.ping", timeout=10)
class Ping(NoParams):
    pass


@register_op("agent.collect_metrics", timeout=30)
class CollectMetrics(NoParams):
    pass


@register_op("agent.service_status", timeout=30)
class ServiceStatus(NoParams):
    pass
