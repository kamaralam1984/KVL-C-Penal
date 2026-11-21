"""AI endpoints: status, Support AI agent, Admin AI assistant, and a read-only MCP server for external AI clients."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import Ctx, api_error, client_ip, get_ctx, require
from app.core.ratelimit import enforce
from app.services import ai
from app.services.audit import audit

router = APIRouter(tags=["ai"])


@router.get("/ai/status")
def status(ctx: Ctx = Depends(get_ctx)):
    return {**ai.state(), "assistant_tools": [t["name"] for t in ai.tools_for(ctx)]}


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)


@router.post("/support/ai/answer")
def support_answer(body: AskIn, request: Request, ctx: Ctx = Depends(require("kb:read")), db: Session = Depends(get_db)):
    """First-line answer from the knowledge base; `handoff` tells the UI to offer a ticket to a human."""
    enforce(f"ai-support:{ctx.user.id}", 20, 3600)
    out = ai.support_answer(db, body.question.strip())
    audit(db, "ai.support_answer", actor=ctx.user.id, tenant_id=ctx.tenant_id, detail={"handoff": out["handoff"], "reason": out.get("handoff_reason"), "sources": [s["slug"] for s in out["sources"]]}, ip=client_ip(request))
    db.commit()
    return out


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AssistantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[Turn] = Field(min_length=1, max_length=20)

    @field_validator("messages")
    @classmethod
    def _last_user(cls, v: list[Turn]) -> list[Turn]:
        if v[-1].role != "user" or v[0].role != "user":
            raise ValueError("the conversation must start and end with a user message")
        return v


@router.post("/ai/assistant")
def assistant(body: AssistantIn, request: Request, ctx: Ctx = Depends(get_ctx), db: Session = Depends(get_db)):
    if not ai.tools_for(ctx):
        raise api_error(403, "forbidden", "The assistant needs at least one of: monitoring, backups, tickets, websites, tenants or audit read access.")
    enforce(f"ai-assistant:{ctx.user.id}", 30, 3600)
    out = ai.assistant(db, ctx, [m.model_dump() for m in body.messages])
    audit(db, "ai.assistant", actor=ctx.user.id, tenant_id=ctx.tenant_id, detail={"tools": out["tools_used"], "stopped": out["stopped"]}, ip=client_ip(request))
    db.commit()
    return out


# ----------------------------------------------------------------------------- MCP (Model Context Protocol), read-only
MCP_VERSION = "2025-06-18"


def _rpc(id_: Any, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    if error:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": error[0], "message": error[1]}}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


@router.post("/mcp")
async def mcp(request: Request, db: Session = Depends(get_db)):
    """Streamable-HTTP MCP endpoint (JSON responses). Authenticate with a KVL API key: `Authorization: Bearer kvl_...`.
    Exposes the same read-only tools as the assistant, filtered by the key's permissions and tenant scope."""
    ctx = get_ctx(request, db)
    enforce(f"mcp:{ctx.user.id}", 120, 60)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse(_rpc(None, error=(-32700, "Parse error")), status_code=400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return JSONResponse(_rpc(msg.get("id") if isinstance(msg, dict) else None, error=(-32600, "Invalid request")), status_code=400)
    method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notification
        return Response(status_code=202)
    if method == "initialize":
        return _rpc(mid, {"protocolVersion": MCP_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "kvl-panel", "version": "1.0"},
                          "instructions": "Read-only access to KVL Panel operations data (servers, alerts, backups, tickets, quotas, websites, audit). Results are limited to the API key's permissions."})
    if method == "ping":
        return _rpc(mid, {})
    if method == "tools/list":
        return _rpc(mid, {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in ai.tools_for(ctx)]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str):
            return _rpc(mid, error=(-32602, "Invalid params"))
        ok, out = ai.run_tool(db, ctx, name, args)
        audit(db, "ai.mcp_tool", actor=ctx.user.id, tenant_id=ctx.tenant_id, detail={"tool": name, "ok": ok}, ip=client_ip(request))
        db.commit()
        import json

        return _rpc(mid, {"content": [{"type": "text", "text": json.dumps(out)[:100_000]}], "isError": not ok})
    return _rpc(mid, error=(-32601, "Method not found"))
