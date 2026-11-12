import json
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import func, select

from .audit import make_span
from .gateway import gateway_metadata, load_candidates
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
    gateway: dict | None = None,
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
        if gateway is not None:
            previous_gateway = (trace.metadata_json or {}).get("gateway", {})
            previous_cost = (
                previous_gateway.get("cost_usd", 0)
                if isinstance(previous_gateway, dict)
                else 0
            )
            if not isinstance(previous_cost, (int, float)) or isinstance(previous_cost, bool):
                previous_cost = 0
            current_cost = gateway.get("cost_usd", 0)
            gateway = {
                **gateway,
                "last_cost_usd": current_cost,
                "cost_usd": round(previous_cost + current_cost, 10),
            }
            trace.metadata_json = {**(trace.metadata_json or {}), "gateway": gateway}

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
    candidate=None,
    settings=None,
    requested_model=None,
    attempts=None,
    circuits=None,
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
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        with anyio.CancelScope(shield=True):
            try:
                try:
                    ended_at = datetime.now(UTC)
                    output = accumulator.output(payload)
                    if (
                        candidate is not None
                        and not candidate.explicit
                        and settings is not None
                        and circuits is not None
                    ):
                        completed = error is None and accumulator.done
                        if completed:
                            circuits.record_success(candidate.circuit_key)
                        else:
                            circuits.record_failure(
                                candidate.circuit_key,
                                settings.gateway_failure_threshold,
                                settings.gateway_cooldown_seconds,
                            )
                        if attempts is not None:
                            attempt = {
                                **attempt_info(candidate),
                                "outcome": "success" if completed else "error",
                                "status_code": upstream.status_code,
                            }
                            if error is not None:
                                attempt["error_type"] = error["type"]
                            attempts.append(attempt)
                    gateway = (
                        gateway_metadata(
                            candidate, settings, requested_model, attempts or [], output
                        )
                        if candidate is not None and not candidate.explicit and settings is not None
                        else None
                    )
                    persist_openai_trace(
                        session_factory,
                        trace_id,
                        payload,
                        output,
                        "completed" if error is None and accumulator.done else "failed",
                        started_at,
                        ended_at,
                        error,
                        gateway,
                    )
                finally:
                    await upstream.aclose()
            finally:
                await client.aclose()


def network_failure_response(
    session_factory,
    trace_id: str,
    payload: dict,
    started_at: datetime,
    exc: httpx.HTTPError,
    gateway: dict | None = None,
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
        gateway,
    )
    return JSONResponse(
        status_code=502,
        content={"detail": "上游网络错误"},
        headers={"X-AgentTrace-Id": trace_id},
    )


def retryable_upstream(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


def candidate_headers(request: Request, candidate, settings) -> dict[str, str]:
    headers = forward_request_headers(request)
    authorization = next(
        (value for key, value in headers.items() if key.lower() == "authorization"),
        None,
    )
    use_default_key = not getattr(candidate, "explicit", False) and (
        authorization is None or authorization == "Bearer agenttrace"
    )
    if candidate.api_key and (candidate.route is not None or use_default_key):
        for key in list(headers):
            if key.lower() == "authorization":
                headers.pop(key)
        headers["Authorization"] = f"Bearer {candidate.api_key}"
    headers.setdefault("Content-Type", "application/json")
    return headers


def attempt_info(candidate) -> dict:
    return {
        "route_id": candidate.route.id if candidate.route else None,
        "route_name": candidate.route.name if candidate.route else "默认上游",
        "target_model": candidate.target_model,
    }


def response_json(content: bytes) -> object:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"content": content.decode(errors="replace")}


def daily_token_usage(session_factory) -> int:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_factory() as session:
        return int(
            session.scalar(
                select(func.sum(Trace.total_tokens)).where(
                    Trace.source == "openai-proxy",
                    func.coalesce(Trace.started_at, Trace.created_at) >= today,
                )
            )
            or 0
        )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request_json_object(request)
    trace_id = request.headers.get("X-AgentTrace-Id", str(uuid4()))
    started_at = datetime.now(UTC)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    budget = settings.gateway_daily_token_budget
    if (
        not request.headers.get("X-Upstream-Base")
        and budget
        and daily_token_usage(session_factory) >= budget
    ):
        error = {"type": "DailyTokenBudgetExceeded", "message": "每日 Token 预算已用尽"}
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
            status_code=429,
            content={"detail": "每日 Token 预算已用尽，请调整预算或明日再试"},
            headers={"X-AgentTrace-Id": trace_id},
        )

    requested_model = payload.get("model")
    with session_factory() as session:
        candidates = load_candidates(
            session,
            settings,
            request.app.state.env_path,
            requested_model,
            request.headers.get("X-Upstream-Base"),
        )
    attempts = []
    stream = bool(payload.get("stream"))
    for index, candidate in enumerate(candidates):
        info = attempt_info(candidate)
        if not candidate.explicit and request.app.state.circuits.is_open(candidate.circuit_key):
            attempts.append({**info, "outcome": "circuit_open"})
            continue
        attempt_payload = dict(payload)
        if candidate.target_model:
            attempt_payload["model"] = candidate.target_model
        body = json.dumps(attempt_payload, ensure_ascii=False).encode("utf-8")
        headers = candidate_headers(request, candidate, settings)
        url = f"{candidate.upstream_base.rstrip('/')}/chat/completions"
        client = httpx.AsyncClient(timeout=60)
        upstream = None
        content = b""
        try:
            if stream:
                upstream_request = client.build_request(
                    "POST", url, content=body, headers=headers
                )
                upstream = await client.send(upstream_request, stream=True)
            else:
                upstream = await client.post(url, content=body, headers=headers)
            if not upstream.is_success:
                content = await upstream.aread()
        except httpx.HTTPError as exc:
            with anyio.CancelScope(shield=True):
                try:
                    if upstream is not None:
                        with suppress(Exception):
                            await upstream.aclose()
                finally:
                    with suppress(Exception):
                        await client.aclose()
            if not candidate.explicit:
                request.app.state.circuits.record_failure(
                    candidate.circuit_key,
                    settings.gateway_failure_threshold,
                    settings.gateway_cooldown_seconds,
                )
            has_next = index + 1 < len(candidates)
            attempts.append(
                {
                    **info,
                    "outcome": "retry" if has_next else "error",
                    "error_type": type(exc).__name__,
                }
            )
            if has_next:
                continue
            gateway = (
                None
                if candidate.explicit
                else gateway_metadata(candidate, settings, requested_model, attempts, None)
            )
            return network_failure_response(
                session_factory,
                trace_id,
                attempt_payload,
                started_at,
                exc,
                gateway,
            )

        if upstream.is_success:
            if not stream:
                if not candidate.explicit:
                    request.app.state.circuits.record_success(candidate.circuit_key)
                attempts.append(
                    {**info, "outcome": "success", "status_code": upstream.status_code}
                )
            if stream:
                response_headers = forward_response_headers(upstream.headers, trace_id)
                if candidate.route:
                    response_headers["X-AgentTrace-Route-Id"] = candidate.route.id
                return StreamingResponse(
                    relay_sse(
                        upstream,
                        client,
                        session_factory,
                        trace_id,
                        attempt_payload,
                        started_at,
                        candidate,
                        settings,
                        requested_model,
                        attempts,
                        request.app.state.circuits,
                    ),
                    status_code=upstream.status_code,
                    headers=response_headers,
                    media_type=upstream.headers.get("content-type"),
                )
            content = upstream.content
            output = response_json(content)
            response_headers = forward_response_headers(
                upstream.headers, trace_id, decoded=True
            )
            media_type = upstream.headers.get("content-type")
            status_code = upstream.status_code
            await upstream.aclose()
            await client.aclose()
            gateway = (
                None
                if candidate.explicit
                else gateway_metadata(candidate, settings, requested_model, attempts, output)
            )
            persist_openai_trace(
                session_factory,
                trace_id,
                attempt_payload,
                output,
                "completed",
                started_at,
                datetime.now(UTC),
                gateway=gateway,
            )
            if candidate.route:
                response_headers["X-AgentTrace-Route-Id"] = candidate.route.id
            return Response(
                content=content,
                status_code=status_code,
                headers=response_headers,
                media_type=media_type,
            )

        output = response_json(content)
        status_code = upstream.status_code
        response_headers = forward_response_headers(upstream.headers, trace_id, decoded=True)
        media_type = upstream.headers.get("content-type")
        await upstream.aclose()
        await client.aclose()
        retryable = retryable_upstream(status_code)
        if retryable and not candidate.explicit:
            request.app.state.circuits.record_failure(
                candidate.circuit_key,
                settings.gateway_failure_threshold,
                settings.gateway_cooldown_seconds,
            )
        has_next = retryable and index + 1 < len(candidates)
        attempts.append(
            {
                **info,
                "outcome": "retry" if has_next else "error",
                "status_code": status_code,
            }
        )
        if has_next:
            continue
        if candidate.route:
            response_headers["X-AgentTrace-Route-Id"] = candidate.route.id
        gateway = (
            None
            if candidate.explicit
            else gateway_metadata(candidate, settings, requested_model, attempts, output)
        )
        persist_openai_trace(
            session_factory,
            trace_id,
            attempt_payload,
            output,
            "failed",
            started_at,
            datetime.now(UTC),
            {"status_code": status_code, "body": output},
            gateway,
        )
        return Response(
            content=content,
            status_code=status_code,
            headers=response_headers,
            media_type=media_type,
        )

    candidate = candidates[0]
    error = {"type": "CircuitOpen", "message": "所有匹配路由均处于熔断冷却期"}
    gateway = gateway_metadata(candidate, settings, requested_model, attempts, None)
    persist_openai_trace(
        session_factory,
        trace_id,
        payload,
        None,
        "failed",
        started_at,
        datetime.now(UTC),
        error,
        gateway,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "所有匹配路由均处于熔断冷却期，请稍后重试或手动复位"},
        headers={"X-AgentTrace-Id": trace_id},
    )
