"""Control-plane side registry of allow-listed agent operations.

Each module in this package declares Pydantic parameter schemas and registers them with `register_op`.
A job can only be queued if its operation name is registered here AND its params validate. The agent
re-validates independently (defense in depth) and refuses anything not in its own allow-list.
"""
import importlib
import pkgutil
from typing import Callable

from pydantic import BaseModel

OPERATIONS: dict[str, type[BaseModel]] = {}
OP_META: dict[str, dict] = {}


def register_op(name: str, *, timeout: int = 60, destructive: bool = False) -> Callable[[type[BaseModel]], type[BaseModel]]:
    def deco(schema: type[BaseModel]) -> type[BaseModel]:
        assert name not in OPERATIONS, f"duplicate operation {name}"
        OPERATIONS[name] = schema
        OP_META[name] = {"timeout": timeout, "destructive": destructive}
        return schema

    return deco


for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
