import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .models import Span, Trace
from .proxy import HOP_BY_HOP, forward_response_headers, request_json_object
from .rules import run_rules

router = APIRouter()


def mcp_request_headers(request: Request, default_key: str | None = None) -> dict[str, str]:
    excluded = HOP_BY_HOP | {"x-mcp-upstream", "x-agenttrace-id", "x-admin-key"}
    headers = {
        key: value for key, value in request.headers.items() if key.lower() not in excluded
    }
    if default_key and not any(key.lower() == "authorization" for key in headers):
        headers["Authorization"] = f"Bearer {default_key}"
    return headers


def mcp_failure(output: object) -> object | None:
    """Return the JSON-RPC/tool error payload, or None for a successful result."""
    if not isinstance(output, dict):
        return None
    if output.get("error") is not None:
        return output["error"]
    result = output.get("result")
    if isinstance(result, dict) and result.get("isError") is True:
        return result
    return None


def persist_mcp(
    session_factory,
    trace_id: str,
    payload: dict,
    output: object,
    upstream_status: int,
    started_at: datetime,
) -> None:
    ended_at = datetime.now(UTC)
    protocol_error = mcp_failure(output)
    if upstream_status >= 400:
        protocol_error = output
    failed = protocol_error is not None
    with session_factory() as session:
        trace = session.get(Trace, trace_id)
        if trace is None:
            trace = Trace(
                id=trace_id,
                name="MCP 协议代理",
                source="mcp-proxy",
                status="completed",
                started_at=started_at,
            )
            session.add(trace)
            session.flush()
        next_sequence = max((span.sequence for span in trace.spans), default=-1) + 1
        previous_failed = trace.status in {"failed", "error"} or any(
            span.status in {"failed", "error"} or span.error is not None for span in trace.spans
        )
        protocol = Span(
            id=f"{trace_id}-mcp-{uuid4()}",
            trace_id=trace_id,
            sequence=next_sequence,
            kind="mcp",
            name=payload.get("method", "unknown"),
            status="error" if failed else "completed",
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=(ended_at - started_at).total_seconds() * 1000,
            input=payload,
            output=output,
            error=protocol_error,
            metadata_json={"upstream_status": upstream_status},
        )
        session.add(protocol)
        if payload.get("method") == "tools/call":
            params = payload.get("params")
            if not isinstance(params, dict):
                params = {}
            session.add(
                Span(
                    id=f"{trace_id}-tool-{uuid4()}",
                    trace_id=trace_id,
                    parent_span_id=protocol.id,
                    sequence=next_sequence + 1,
                    kind="tool",
                    name=params.get("name", "MCP 工具"),
                    status="error" if failed else "completed",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=(ended_at - started_at).total_seconds() * 1000,
                    input=params,
                    output=output,
                    tool_name=params.get("name"),
                    tool_arguments=params.get("arguments"),
                    tool_result=output.get("result") if isinstance(output, dict) else None,
                    error=protocol_error,
                    metadata_json={"upstream_status": upstream_status},
                )
            )
        trace.status = "failed" if failed or previous_failed else "completed"
        trace.input = payload
        trace.output = output
        trace.ended_at = ended_at
        trace_started = trace.started_at
        if trace_started.tzinfo is None:
            trace_started = trace_started.replace(tzinfo=UTC)
        trace.duration_ms = (ended_at - trace_started).total_seconds() * 1000
        session.flush()
        session.expire(trace, ["spans", "findings"])
        run_rules(session, trace)
        session.commit()


@router.post("/mcp")
@router.post("/mcp/{path:path}")
async def mcp_proxy(request: Request, path: str = ""):
    payload = await request_json_object(request)
    upstream_url = request.headers.get(
        "X-MCP-Upstream", request.app.state.settings.default_mcp_upstream
    )
    if not upstream_url:
        return JSONResponse(
            status_code=400,
            content={"detail": "未配置 MCP 上游，请设置 X-MCP-Upstream 或 DEFAULT_MCP_UPSTREAM"},
        )
    body = await request.body()
    trace_id = request.headers.get("X-AgentTrace-Id", str(uuid4()))
    started_at = datetime.now(UTC)
    _ = path
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            upstream = await client.post(
                upstream_url,
                content=body,
                headers=mcp_request_headers(
                    request,
                    request.app.state.settings.default_mcp_key
                    if "x-mcp-upstream" not in request.headers
                    else None,
                ),
            )
    except httpx.HTTPError as exc:
        output = {"type": type(exc).__name__, "message": str(exc)}
        persist_mcp(
            request.app.state.session_factory,
            trace_id,
            payload,
            output,
            502,
            started_at,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "MCP 上游网络错误"},
            headers={"X-AgentTrace-Id": trace_id},
        )
    try:
        output = upstream.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        output = {"content": upstream.content.decode(errors="replace")}
    persist_mcp(
        request.app.state.session_factory,
        trace_id,
        payload,
        output,
        upstream.status_code,
        started_at,
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=forward_response_headers(upstream.headers, trace_id, decoded=True),
        media_type=upstream.headers.get("content-type"),
    )
