import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .audit import make_span
from .models import Trace
from .rules import run_rules
from .schemas import SpanIn

router = APIRouter()
HOP_BY_HOP = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def forward_request_headers(request: Request) -> dict[str, str]:
    excluded = HOP_BY_HOP | {"x-upstream-base", "x-agenttrace-id", "x-admin-key"}
    return {key: value for key, value in request.headers.items() if key.lower() not in excluded}


async def request_json_object(request: Request) -> dict:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="请求体不是有效的 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体必须是 JSON 对象")
    return payload


def forward_response_headers(
    headers: httpx.Headers, trace_id: str, *, decoded: bool = False
) -> dict[str, str]:
    excluded = HOP_BY_HOP | ({"content-encoding"} if decoded else set())
    result = {key: value for key, value in headers.items() if key.lower() not in excluded}
    result["X-AgentTrace-Id"] = trace_id
    return result


def parse_arguments(arguments) -> tuple[object, bool]:
    if not isinstance(arguments, str):
        return arguments, True
    try:
        return json.loads(arguments), True
    except json.JSONDecodeError:
        return arguments, False


def persist_openai_trace(
    session_factory,
    trace_id: str,
    payload: dict,
    output: object,
    status: str,
    started_at: datetime,
    ended_at: datetime,
    error=None,
) -> None:
    invocation_id = uuid4().hex
    span_prefix = f"{trace_id}-{invocation_id}"
    usage = output.get("usage", {}) if isinstance(output, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    message = {}
    if isinstance(output, dict):
        try:
            candidate = output["choices"][0]["message"]
            message = candidate if isinstance(candidate, dict) else {}
        except (KeyError, IndexError, TypeError):
            message = {}
    spans = []
    sequence = 0
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        if not isinstance(role, str):
            role = "user"
        kind = {"user": "user", "system": "system", "tool": "tool_result"}.get(role, "user")
        spans.append(
            SpanIn(
                id=f"{span_prefix}-message-{sequence}",
                sequence=sequence,
                kind=kind,
                name=f"{role} 消息",
                content=str(item.get("content") or ""),
                input=item,
                tool_name=item.get("name"),
                tool_result=item.get("content") if role == "tool" else None,
            )
        )
        sequence += 1
    llm_id = f"{span_prefix}-llm"
    spans.append(
        SpanIn(
            id=llm_id,
            sequence=sequence,
            kind="llm",
            name="模型响应",
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            content=message.get("content"),
            reasoning=message.get("reasoning_content") or message.get("reasoning"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            error=error,
            metadata={
                "invocation_id": invocation_id,
                "upstream_status": (
                    200
                    if status == "completed"
                    else (error or {}).get("status_code")
                    if isinstance(error, dict)
                    else None
                )
            },
        )
    )
    sequence += 1
    schemas = {}
    tools = payload.get("tools", [])
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
                continue
            function = tool["function"]
            schemas[function.get("name")] = function.get("parameters", {})
    tool_calls = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        tool_calls = []
    for call in tool_calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            continue
        function = call["function"]
        arguments, _ = parse_arguments(function.get("arguments"))
        spans.append(
            SpanIn(
                id=f"{span_prefix}-tool-{sequence}",
                parent_span_id=llm_id,
                sequence=sequence,
                kind="tool",
                name=function.get("name", "工具调用"),
                tool_name=function.get("name"),
                tool_arguments=arguments,
                metadata={"tool_schema": schemas.get(function.get("name"), {})},
            )
        )
        sequence += 1
    with session_factory() as session:
        trace = session.get(Trace, trace_id)
        if trace is None:
            trace = Trace(
                id=trace_id,
                name="OpenAI 对话代理",
                source="openai-proxy",
                status="completed",
                started_at=started_at,
            )
            session.add(trace)
            session.flush()

        previous_failed = trace.status in {"failed", "error"} or any(
            span.status in {"failed", "error"} or span.error is not None for span in trace.spans
        )
        next_sequence = max((span.sequence for span in trace.spans), default=-1) + 1
        for data in sorted(spans, key=lambda item: item.sequence):
            span = make_span(data, trace.id)
            span.sequence += next_sequence
            trace.spans.append(span)

        trace.name = "OpenAI 对话代理"
        trace.source = "openai-proxy"
        trace.status = "failed" if previous_failed or status == "failed" else status
        trace.model = payload.get("model")
        trace.input = payload
        trace.output = output
        trace.prompt_tokens = (trace.prompt_tokens or 0) + usage.get("prompt_tokens", 0)
        trace.completion_tokens = (trace.completion_tokens or 0) + usage.get(
            "completion_tokens", 0
        )
        trace.total_tokens = (trace.total_tokens or 0) + usage.get("total_tokens", 0)
        trace.error = error if error is not None else trace.error

        trace_started = trace.started_at or started_at
        if trace_started.tzinfo is None:
            trace_started = trace_started.replace(tzinfo=UTC)
        trace.started_at = min(trace_started, started_at)
        previous_ended = trace.ended_at
        if previous_ended is not None and previous_ended.tzinfo is None:
            previous_ended = previous_ended.replace(tzinfo=UTC)
        trace.ended_at = max(previous_ended, ended_at) if previous_ended else ended_at
        trace.duration_ms = (trace.ended_at - trace.started_at).total_seconds() * 1000
        session.flush()
        run_rules(session, trace)
        session.commit()


class SSEAccumulator:
    def __init__(self):
        self.buffer = b""
        self.data_lines: list[bytes] = []
        self.content = ""
        self.reasoning = ""
        self.tool_calls: dict[int, dict] = {}
        self.usage = {}
        self.done = False

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line:
                self._event()
            elif line.startswith(b"data:"):
                self.data_lines.append(line[5:].lstrip())

    def finish(self) -> None:
        if self.buffer:
            line, self.buffer = self.buffer, b""
            if line.startswith(b"data:"):
                self.data_lines.append(line[5:].lstrip())
        if self.data_lines:
            self._event()

    def _event(self) -> None:
        if not self.data_lines:
            return
        raw = b"\n".join(self.data_lines)
        self.data_lines = []
        if raw.strip() == b"[DONE]":
            self.done = True
            return
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        if isinstance(event.get("usage"), dict):
            self.usage = event["usage"]
        choices = event.get("choices", [])
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(content, str):
                self.content += content
            if isinstance(reasoning, str):
                self.reasoning += reasoning
            calls = delta.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                index = call.get("index", 0)
                if not isinstance(index, int) or isinstance(index, bool):
                    continue
                current = self.tool_calls.setdefault(
                    index,
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if isinstance(call.get("id"), str):
                    current["id"] = call["id"] or current["id"]
                if isinstance(call.get("type"), str):
                    current["type"] = call["type"] or current["type"]
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                if isinstance(function.get("name"), str):
                    current["function"]["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    current["function"]["arguments"] += function["arguments"]

    def output(self, payload: dict) -> dict:
        return {
            "object": "chat.completion",
            "model": payload.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": self.content,
                        "reasoning_content": self.reasoning,
                        "tool_calls": [self.tool_calls[index] for index in sorted(self.tool_calls)],
                    },
                }
            ],
            "usage": self.usage,
            "stream": True,
        }


async def relay_sse(
    upstream: httpx.Response,
    client: httpx.AsyncClient,
    session_factory,
    trace_id: str,
    payload: dict,
    started_at: datetime,
):
    accumulator = SSEAccumulator()
    error = None
    try:
        async for chunk in upstream.aiter_raw():
            accumulator.feed(chunk)
            yield chunk
        accumulator.finish()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        ended_at = datetime.now(UTC)
        persist_openai_trace(
            session_factory,
            trace_id,
            payload,
            accumulator.output(payload),
            "completed" if error is None and accumulator.done else "failed",
            started_at,
            ended_at,
            error,
        )
        await upstream.aclose()
        await client.aclose()


def network_failure_response(
    session_factory,
    trace_id: str,
    payload: dict,
    started_at: datetime,
    exc: httpx.HTTPError,
) -> JSONResponse:
    error = {"type": type(exc).__name__, "message": str(exc)}
    persist_openai_trace(
        session_factory,
        trace_id,
        payload,
        None,
        "failed",
        started_at,
        datetime.now(UTC),
        error,
    )
    return JSONResponse(
        status_code=502,
        content={"detail": "上游网络错误"},
        headers={"X-AgentTrace-Id": trace_id},
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request_json_object(request)
    trace_id = request.headers.get("X-AgentTrace-Id", str(uuid4()))
    upstream_base = request.headers.get(
        "X-Upstream-Base", request.app.state.settings.default_upstream_base
    )
    started_at = datetime.now(UTC)
    body = await request.body()
    if payload.get("stream"):
        client = httpx.AsyncClient(timeout=60)
        upstream_request = client.build_request(
            "POST",
            f"{upstream_base.rstrip('/')}/chat/completions",
            content=body,
            headers=forward_request_headers(request),
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            return network_failure_response(
                request.app.state.session_factory, trace_id, payload, started_at, exc
            )
        if not upstream.is_success:
            content = await upstream.aread()
            try:
                output = json.loads(content)
            except json.JSONDecodeError:
                output = {"content": content.decode(errors="replace")}
            persist_openai_trace(
                request.app.state.session_factory,
                trace_id,
                payload,
                output,
                "failed",
                started_at,
                datetime.now(UTC),
                {"status_code": upstream.status_code, "body": output},
            )
            headers = forward_response_headers(upstream.headers, trace_id, decoded=True)
            media_type = upstream.headers.get("content-type")
            await upstream.aclose()
            await client.aclose()
            return Response(
                content=content,
                status_code=upstream.status_code,
                headers=headers,
                media_type=media_type,
            )
        return StreamingResponse(
            relay_sse(
                upstream,
                client,
                request.app.state.session_factory,
                trace_id,
                payload,
                started_at,
            ),
            status_code=upstream.status_code,
            headers=forward_response_headers(upstream.headers, trace_id),
            media_type=upstream.headers.get("content-type"),
        )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            upstream = await client.post(
                f"{upstream_base.rstrip('/')}/chat/completions",
                content=body,
                headers=forward_request_headers(request),
            )
    except httpx.HTTPError as exc:
        return network_failure_response(
            request.app.state.session_factory, trace_id, payload, started_at, exc
        )
    try:
        output = upstream.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        output = {"content": upstream.content.decode(errors="replace")}
    ended_at = datetime.now(UTC)
    persist_openai_trace(
        request.app.state.session_factory,
        trace_id,
        payload,
        output,
        "completed" if upstream.is_success else "failed",
        started_at,
        ended_at,
        None if upstream.is_success else {"status_code": upstream.status_code, "body": output},
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=forward_response_headers(upstream.headers, trace_id, decoded=True),
        media_type=upstream.headers.get("content-type"),
    )
