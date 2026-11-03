"""Allow-list of operations. Nothing outside this registry can ever be executed by the agent."""
import importlib
import pkgutil
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


class UnknownOperation(Exception):
    pass


class InvalidParams(Exception):
    pass


@dataclass(frozen=True)
class Operation:
    name: str
    schema: type[BaseModel]
    handler: Callable[[Any, Any], dict]
    timeout: int
    destructive: bool


OPERATIONS: dict[str, Operation] = {}


def operation(name: str, schema: type[BaseModel], *, timeout: int = 60, destructive: bool = False):
    """Register a handler `fn(params: schema, ctx: OpContext) -> dict`. The returned dict is a result summary (no secrets)."""

    def deco(fn: Callable[[Any, Any], dict]):
        assert name not in OPERATIONS, f"duplicate operation {name}"
        OPERATIONS[name] = Operation(name, schema, fn, timeout, destructive)
        return fn

    return deco


def load_operations() -> None:
    from kvl_agent import operations

    for m in pkgutil.iter_modules(operations.__path__):
        importlib.import_module(f"{operations.__name__}.{m.name}")


def validate(name: str, raw: dict | None) -> tuple[Operation, BaseModel]:
    op = OPERATIONS.get(name)
    if op is None:
        raise UnknownOperation(name)
    try:
        return op, op.schema.model_validate(raw or {})
    except ValidationError as e:
        raise InvalidParams("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from None
