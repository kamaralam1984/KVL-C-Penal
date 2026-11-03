import base64

from kvl_agent.registry import validate
from kvl_agent.testing import make_ctx  # noqa: F401


def call(ctx, op_name: str, /, **params):
    op, p = validate(op_name, params)
    return op.handler(p, ctx)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
