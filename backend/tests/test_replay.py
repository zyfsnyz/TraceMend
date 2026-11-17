import asyncio
import json
import socket
import threading
import time
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

pytest_plugins = ["upstream_fixtures"]

TRACE_SERIALIZER_FIELDS = {
    "id",
    "parent_id",
    "name",
    "source",
    "status",
    "model",
    "input",
    "output",
    "started_at",
    "ended_at",
    "duration_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "tags",
    "metadata",
    "estimated_cost_usd",
    "error",
    "created_at",
    "span_count",
    "risk_count",
}
SPAN_SERIALIZER_FIELDS = {
    "id",
    "trace_id",
    "parent_span_id",
    "sequence",
    "kind",
    "name",
    "status",
    "started_at",
    "ended_at",
    "duration_ms",
    "input",
    "output",
    "content",
    "reasoning",
    "tool_name",
    "tool_arguments",
    "tool_result",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "error",
    "metadata",
}
WEATHER_TOOL_CALL = {
    "id": "call-weather",
    "type": "function",
    "function": {"name": "weather", "arguments": '{"city":"北京"}'},
}
ATTRIBUTION_NOTE = "单次一致或差异均不能单独证明模型随机性或幻觉成因；请结合多次实验与事实评估。"


@pytest.fixture
def scenario_openai_upstream():
    """Real local upstream that can return 500 or an SSE stream missing [DONE]."""
    application = FastAPI()
    requests = []

    @application.post("/v1/chat/completions")
    async def completions(request: Request):
        payload = await request.json()
        requests.append({"payload": payload, "headers": dict(request.headers)})
        if payload.get("model") == "server-error-model":
            return JSONResponse(
                status_code=500, content={"error": {"message": "上游内部错误"}}
            )
        if payload.get("model") == "incomplete-sse-model":

            async def generate():
                chunk = json.dumps(
                    {"choices": [{"delta": {"content": "未完成"}, "index": 0}]},
                    ensure_ascii=False,
                )
                yield f"data: {chunk}\n\n".encode()
                await asyncio.sleep(0)

            return StreamingResponse(generate(), media_type="text/event-stream")
        if payload.get("model") == "inband-error-sse-model":

            async def generate_inband_error():
                event = {
                    "error": {
                        "message": "上游流内错误",
                        "type": "upstream_stream_error",
                        "code": "bad_gateway_stream",
                    }
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                await asyncio.sleep(0)
                yield b"data: [DONE]\n\n"

            return StreamingResponse(
                generate_inband_error(), media_type="text/event-stream"
            )
        return {
            "id": "chatcmpl-scenario",
            "object": "chat.completion",
            "model": payload.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "场景上游回答", "tool_calls": []},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(application, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("场景本地上游启动失败")
    try:
        yield SimpleNamespace(base_url=f"http://127.0.0.1:{port}/v1", requests=requests)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def chat_output(
    *,
    content="原始回答",
    reasoning="原始推理",
    model="source-model",
    tool_calls=None,
):
    return {
        "id": "chatcmpl-source",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning,
                    "tool_calls": deepcopy(tool_calls or []),
                },
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    }


def stream_output(model="stream-model"):
    return {
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "明天 25°C",
                    "reasoning_content": "需要",
                    "tool_calls": [
                        WEATHER_TOOL_CALL,
                        {
                            "id": "call-calendar",
                            "type": "function",
                            "function": {"name": "calendar", "arguments": "{}"},
                        },
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 9, "total_tokens": 29},
        "stream": True,
    }


async def configure_default_upstream(client, upstream, **overrides):
    settings = {
        "default_upstream_base": upstream.base_url,
        "default_upstream_model": "default-model",
    }
    settings.update(overrides)
    response = await client.put("/api/settings", json=settings)
    assert response.status_code == 200


async def create_route(client, upstream, **overrides):
    payload = {
        "name": "重放当前路由",
        "match_model": "demo-*",
        "target_model": "current-routed-model",
        "upstream_base": upstream.base_url,
        "priority": 10,
        "enabled": True,
        "input_price_per_million": 0,
        "output_price_per_million": 0,
    }
    payload.update(overrides)
    response = await client.post("/api/gateway/routes", json=payload)
    assert response.status_code == 201
    return response.json()


async def seed_source(
    client,
    trace_id,
    *,
    span_id="source-llm",
    span_kind="llm",
    span_input=None,
    trace_input=None,
    span_output=None,
    trace_metadata=None,
    span_metadata=None,
    extra_spans=None,
):
    output = deepcopy(span_output) if span_output is not None else chat_output()
    message = output["choices"][0]["message"]
    span = {
        "id": span_id,
        "sequence": 0,
        "kind": span_kind,
        "name": "历史模型调用",
        "status": "completed",
        "input": deepcopy(span_input),
        "output": output,
        "content": message["content"],
        "reasoning": message["reasoning_content"],
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12,
        "metadata": deepcopy(span_metadata or {}),
    }
    payload = {
        "trace": {
            "id": trace_id,
            "name": "历史重放来源",
            "source": "custom-agent",
            "status": "completed",
            "model": "source-model",
            "input": deepcopy(trace_input),
            "output": deepcopy(output),
            "metadata": deepcopy(trace_metadata or {}),
        },
        "spans": [span, *deepcopy(extra_spans or [])],
    }
    response = await client.post("/api/traces/ingest", json=payload)
    assert response.status_code == 200
    return payload


GENERATE_IDEMPOTENCY_KEY = object()


async def post_replay(
    client,
    trace_id,
    span_id="source-llm",
    body=None,
    idempotency_key=GENERATE_IDEMPOTENCY_KEY,
):
    if idempotency_key is GENERATE_IDEMPOTENCY_KEY:
        idempotency_key = str(uuid4())
    headers = (
        {} if idempotency_key is None else {"Idempotency-Key": idempotency_key}
    )
    return await client.post(
        f"/api/traces/{trace_id}/spans/{span_id}/replay",
        json={"confirm": True} if body is None else body,
        headers=headers,
    )


def reservation_state(application, idempotency_key):
    from app.models import ReplayReservation

    with application.state.session_factory() as session:
        row = session.get(ReplayReservation, str(idempotency_key))
        if row is None:
            return None
        return {
            "status": row.status,
            "request_fingerprint": row.request_fingerprint,
            "source_trace_id": row.source_trace_id,
            "source_span_id": row.source_span_id,
            "response_json": deepcopy(row.response_json),
            "error": deepcopy(row.error),
        }


@pytest.mark.asyncio
async def test_capture_keeps_each_llm_input_and_output_independent(client, openai_upstream):
    trace_id = "replay-capture-repeated"
    first = {
        "model": "first-model",
        "messages": [
            {"role": "system", "content": "第一次系统指令"},
            {"role": "user", "content": "第一个问题"},
        ],
        "temperature": 0.1,
    }
    second = {
        "model": "second-model",
        "messages": [{"role": "user", "content": "第二个问题"}],
        "temperature": 0.9,
        "max_tokens": 77,
    }
    responses = []
    for payload in (first, second):
        response = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers={
                "X-Upstream-Base": openai_upstream.base_url,
                "X-AgentTrace-Id": trace_id,
            },
        )
        assert response.status_code == 200
        responses.append(response.json())

    detail = (await client.get(f"/api/traces/{trace_id}")).json()
    llm_spans = [span for span in detail["spans"] if span["kind"] == "llm"]
    assert len(llm_spans) == 2
    assert len({span["id"] for span in llm_spans}) == 2
    assert [span["input"] for span in llm_spans] == [first, second]
    assert [span["output"] for span in llm_spans] == responses


@pytest.mark.asyncio
async def test_capture_saves_effective_routed_payload_and_caller_model_on_llm_span(
    client, openai_upstream
):
    trace_id = "replay-capture-current-route"
    caller_model = "demo-capture-current-route"
    target_model = "capture-current-routed-model"
    caller_payload = {
        "model": caller_model,
        "messages": [{"role": "user", "content": "记录实际路由后的请求"}],
        "temperature": 0.3,
    }
    await configure_default_upstream(client, openai_upstream)
    await create_route(
        client,
        openai_upstream,
        name="采集当前路由有效载荷",
        match_model=caller_model,
        target_model=target_model,
    )

    response = await client.post(
        "/v1/chat/completions",
        json=caller_payload,
        headers={"X-AgentTrace-Id": trace_id},
    )

    assert response.status_code == 200
    expected = deepcopy(caller_payload)
    expected["model"] = target_model
    assert len(openai_upstream.requests) == 1
    assert openai_upstream.requests[0]["payload"] == expected
    detail = (await client.get(f"/api/traces/{trace_id}")).json()
    llm = next(span for span in detail["spans"] if span["kind"] == "llm")
    assert llm["input"] == expected
    assert llm["metadata"]["requested_model"] == caller_model


@pytest.mark.asyncio
async def test_capture_saves_sse_request_and_normalized_response_on_llm_span(
    client, openai_upstream
):
    payload = {
        "model": "stream-model",
        "stream": True,
        "messages": [{"role": "user", "content": "流式天气"}],
        "stream_options": {"include_usage": True},
    }
    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "replay-capture-sse",
        },
    )
    assert response.status_code == 200

    detail = (await client.get("/api/traces/replay-capture-sse")).json()
    llm = next(span for span in detail["spans"] if span["kind"] == "llm")
    assert llm["input"] == payload
    assert llm["output"] == stream_output()


@pytest.mark.asyncio
async def test_capture_saves_failed_llm_request_and_upstream_response(client, openai_upstream):
    payload = {"model": "denied-model", "messages": [{"role": "user", "content": "会失败"}]}
    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "replay-capture-failure",
        },
    )
    assert response.status_code == 429

    detail = (await client.get("/api/traces/replay-capture-failure")).json()
    llm = next(span for span in detail["spans"] if span["kind"] == "llm")
    assert llm["input"] == payload
    assert llm["output"] == {"error": {"message": "上游限流"}}
    assert llm["status"] == "failed"
    assert llm["error"]["status_code"] == 429


@pytest.mark.asyncio
async def test_replay_reuses_exact_payload_applies_whitelist_and_current_route_key(
    client, openai_upstream
):
    saved_input = {
        "model": "historical-rewritten-target",
        "messages": [
            {"role": "system", "content": "保持系统指令"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "保留多模态消息结构"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "只发送 schema，不执行工具",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "weather"}},
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "top_p": 0.8,
        "seed": 7,
        "max_tokens": 64,
        "stream": False,
        "metadata": {"request_tag": "preserved"},
    }
    overrides = {
        "model": "demo-override",
        "temperature": 0.2,
        "top_p": 0.9,
        "seed": 42,
        "max_tokens": 512,
    }
    await seed_source(
        client,
        "replay-exact-source",
        span_input=saved_input,
        trace_input={"messages": [{"role": "user", "content": "过期 Trace 输入不能使用"}]},
        span_metadata={"requested_model": "demo-original"},
    )
    await configure_default_upstream(client, openai_upstream)
    await create_route(
        client,
        openai_upstream,
        name="不应选中的原模型路由",
        match_model="demo-original",
        target_model="wrong-original-route",
        api_key="wrong-original-key",
        priority=1,
    )
    await create_route(
        client,
        openai_upstream,
        name="覆盖模型当前路由",
        match_model="demo-override",
        target_model="current-routed-model",
        api_key="current-route-secret",
        priority=2,
    )

    response = await post_replay(
        client, "replay-exact-source", body={"confirm": True, "overrides": overrides}
    )

    assert response.status_code == 201
    assert len(openai_upstream.requests) == 1
    upstream = openai_upstream.requests[0]
    expected = deepcopy(saved_input) | overrides
    expected["model"] = "current-routed-model"
    assert upstream["payload"] == expected
    assert upstream["headers"]["authorization"] == "Bearer current-route-secret"
    body = response.json()
    assert body["replay_span"]["input"] == expected
    assert body["replay_trace"]["metadata"]["replay"]["overrides"] == overrides
    replay_detail = (
        await client.get(f"/api/traces/{body['replay_trace']['id']}")
    ).json()
    persisted_replay_span = next(
        span for span in replay_detail["spans"] if span["id"] == body["replay_span"]["id"]
    )
    assert persisted_replay_span["input"] == expected


@pytest.mark.asyncio
async def test_replay_returns_full_lineage_and_output_diff_without_mutating_source(
    client, openai_upstream
):
    source_output = chat_output(
        content="昨天 20°C", reasoning="旧推理", tool_calls=[WEATHER_TOOL_CALL]
    )
    source_input = {
        "model": "historical-rewritten-target",
        "messages": [{"role": "user", "content": "天气"}],
    }
    await seed_source(
        client,
        "replay-lineage-source",
        span_input=source_input,
        span_output=source_output,
        span_metadata={"requested_model": "demo-original"},
        trace_metadata={"gateway": {"requested_model": "demo-original"}},
    )
    source_before = (await client.get("/api/traces/replay-lineage-source")).json()
    await configure_default_upstream(client, openai_upstream)
    route = await create_route(
        client,
        openai_upstream,
        match_model="demo-original",
        target_model="current-from-original",
        api_key="current-lineage-key",
    )

    response = await post_replay(client, "replay-lineage-source")

    assert response.status_code == 201
    assert len(openai_upstream.requests) == 1
    assert openai_upstream.requests[-1]["payload"]["model"] == "current-from-original"
    body = response.json()
    assert set(body) == {"replay_trace", "replay_span", "source_span", "comparison"}
    assert set(body["replay_trace"]) == TRACE_SERIALIZER_FIELDS
    assert set(body["replay_span"]) == SPAN_SERIALIZER_FIELDS
    assert set(body["source_span"]) == SPAN_SERIALIZER_FIELDS
    replay_trace = body["replay_trace"]
    replay_span = body["replay_span"]
    assert replay_trace["id"] != "replay-lineage-source"
    assert replay_trace["parent_id"] == "replay-lineage-source"
    assert replay_span["trace_id"] == replay_trace["id"]
    assert replay_span["kind"] == "llm"
    fresh_output = replay_span["output"]
    assert fresh_output != source_output
    assert fresh_output["id"] == "chatcmpl-local"
    assert fresh_output["model"] == "current-from-original"
    fresh_message = fresh_output["choices"][0]["message"]
    assert fresh_message["content"] == "明天 25°C"
    assert fresh_message["reasoning_content"] == "查过天气"
    assert fresh_message["tool_calls"] == [WEATHER_TOOL_CALL]
    assert fresh_output["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert replay_span["content"] == "明天 25°C"
    assert replay_span["reasoning"] == "查过天气"
    assert replay_span["prompt_tokens"] == 11
    assert replay_span["completion_tokens"] == 7
    assert replay_span["total_tokens"] == 18
    assert body["source_span"]["id"] == "source-llm"
    comparison = body["comparison"]
    assert comparison["status"] == "different"
    assert comparison["content_equal"] is False
    assert comparison["reasoning_equal"] is False
    assert comparison["tool_calls_equal"] is True
    assert 0 <= comparison["content_similarity"] < 1
    assert comparison["content_diff"].startswith("--- 原始输出\n+++ 重放输出\n")
    assert "-昨天 20°C" in comparison["content_diff"]
    assert "+明天 25°C" in comparison["content_diff"]
    assert comparison["attribution_note"] == ATTRIBUTION_NOTE
    lineage = replay_trace["metadata"]["replay"]
    assert lineage["source_trace_id"] == "replay-lineage-source"
    assert lineage["source_span_id"] == "source-llm"
    assert lineage["request_source"] == "span"
    assert lineage["overrides"] == {}
    assert lineage["mode"] == "llm_span"
    assert lineage["comparison"] == comparison

    replay_detail = (await client.get(f"/api/traces/{replay_trace['id']}")).json()
    assert replay_detail["trace"] == replay_trace
    replay_kinds = [span["kind"] for span in replay_detail["spans"]]
    assert replay_kinds.count("llm") == 1
    assert "tool" not in replay_kinds
    assert "tool_result" not in replay_kinds
    persisted_replay_span = next(
        span for span in replay_detail["spans"] if span["id"] == replay_span["id"]
    )
    assert persisted_replay_span == replay_span
    persisted_lineage = replay_detail["trace"]["metadata"]["replay"]
    assert replay_detail["trace"]["parent_id"] == "replay-lineage-source"
    assert persisted_lineage["source_trace_id"] == "replay-lineage-source"
    assert persisted_lineage["source_span_id"] == "source-llm"
    assert persisted_lineage["comparison"] == comparison
    assert (await client.get("/api/traces/replay-lineage-source")).json() == source_before
    routes = (await client.get("/api/gateway/routes")).json()["items"]
    current_route = next(item for item in routes if item["id"] == route["id"])
    assert current_route["circuit"]["status"] == "closed"


@pytest.mark.asyncio
async def test_replay_idempotent_retry_returns_persisted_response_without_new_trace(
    client, openai_upstream
):
    source_trace_id = "replay-idempotent-retry-source"
    replay_trace_id = str(uuid4())
    request_body = {"confirm": True, "overrides": {"temperature": 0.2}}
    await seed_source(
        client,
        source_trace_id,
        span_input={"model": "safe-model", "messages": [{"role": "user", "content": "重试"}]},
    )
    await configure_default_upstream(client, openai_upstream)

    first = await post_replay(
        client,
        source_trace_id,
        body=request_body,
        idempotency_key=replay_trace_id,
    )
    retry = await post_replay(
        client,
        source_trace_id,
        body=request_body,
        idempotency_key=replay_trace_id,
    )

    assert first.status_code == 201
    assert retry.status_code == first.status_code
    first_body = first.json()
    assert first_body["replay_trace"]["id"] == replay_trace_id
    assert retry.json() == first_body
    assert len(openai_upstream.requests) == 1

    listing = await client.get("/api/traces", params={"page_size": 200})
    assert listing.status_code == 200
    replay_traces = [
        trace
        for trace in listing.json()["items"]
        if trace["parent_id"] == source_trace_id
    ]
    assert [trace["id"] for trace in replay_traces] == [replay_trace_id]


@pytest.mark.asyncio
async def test_replay_same_key_while_upstream_running_returns_409_without_duplicate(
    client, controlled_openai_upstream
):
    source_trace_id = "replay-inflight-idempotency-source"
    replay_trace_id = str(uuid4())
    request_body = {"confirm": True, "overrides": {"temperature": 0.2}}
    controlled_openai_upstream.release.clear()
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "inflight-model",
            "messages": [{"role": "user", "content": "仍在执行"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)

    first_task = asyncio.create_task(
        post_replay(
            client,
            source_trace_id,
            body=request_body,
            idempotency_key=replay_trace_id,
        )
    )
    arrival_task = asyncio.create_task(
        asyncio.to_thread(controlled_openai_upstream.request_arrived.wait)
    )
    try:
        done, _ = await asyncio.wait(
            {first_task, arrival_task},
            timeout=5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if first_task in done:
            early_response = first_task.result()
            assert early_response.status_code == 201, early_response.text
        assert arrival_task in done, "首个重放未在上游门控前到达"

        conflict = await asyncio.wait_for(
            post_replay(
                client,
                source_trace_id,
                body=request_body,
                idempotency_key=replay_trace_id,
            ),
            timeout=5,
        )
        assert conflict.status_code == 409
        assert len(controlled_openai_upstream.requests) == 1
    finally:
        controlled_openai_upstream.release.set()
        controlled_openai_upstream.request_arrived.set()
        first = await asyncio.wait_for(first_task, timeout=5)
        if not arrival_task.done():
            arrival_task.cancel()
        await asyncio.gather(arrival_task, return_exceptions=True)

    assert first.status_code == 201
    assert first.json()["replay_trace"]["id"] == replay_trace_id
    assert len(controlled_openai_upstream.requests) == 1
    listing = await client.get("/api/traces", params={"page_size": 200})
    assert listing.status_code == 200
    replay_traces = [
        trace
        for trace in listing.json()["items"]
        if trace["parent_id"] == source_trace_id
    ]
    assert [trace["id"] for trace in replay_traces] == [replay_trace_id]


@pytest.mark.asyncio
async def test_replay_idempotency_key_conflict_skips_second_upstream_call(
    client, openai_upstream
):
    source_trace_id = "replay-idempotent-conflict-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={"model": "safe-model", "messages": [{"role": "user", "content": "冲突"}]},
    )
    await configure_default_upstream(client, openai_upstream)

    first = await post_replay(
        client,
        source_trace_id,
        body={"confirm": True, "overrides": {"temperature": 0.2}},
        idempotency_key=replay_trace_id,
    )
    conflict = await post_replay(
        client,
        source_trace_id,
        body={"confirm": True, "overrides": {"temperature": 0.9}},
        idempotency_key=replay_trace_id,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert len(openai_upstream.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "idempotency_key",
    [None, "not-a-uuid"],
    ids=["missing", "malformed"],
)
async def test_replay_requires_uuid_idempotency_key_before_upstream(
    client, openai_upstream, idempotency_key
):
    await seed_source(
        client,
        "replay-idempotency-key-validation",
        span_input={"model": "safe-model", "messages": [{"role": "user", "content": "校验"}]},
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(
        client,
        "replay-idempotency-key-validation",
        idempotency_key=idempotency_key,
    )

    assert response.status_code == 422
    assert openai_upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"confirm": False},
        {"confirm": 1},
        {"confirm": "true"},
        {"confirm": None},
    ],
)
async def test_replay_requires_literal_true_confirmation(
    client, openai_upstream, request_body
):
    span_input = {"model": "confirm-model", "messages": [{"role": "user", "content": "确认"}]}
    await seed_source(client, "replay-confirm-source", span_input=span_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-confirm-source", body=request_body)

    assert response.status_code == 422
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_rejects_non_llm_span_before_upstream(client, openai_upstream):
    await seed_source(
        client,
        "replay-non-llm-source",
        span_kind="tool",
        span_input={"messages": [{"role": "user", "content": "不能重放工具"}]},
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-non-llm-source")

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_selected_llm_does_not_replay_other_llm_or_tool_spans(
    client, controlled_openai_upstream
):
    source_trace_id = "replay-selected-llm-only-source"
    selected_input = {
        "model": "selected-model",
        "messages": [{"role": "user", "content": "只重放这次调用"}],
    }
    await seed_source(
        client,
        source_trace_id,
        span_input=selected_input,
        span_output=chat_output(content="选中调用的历史回答", reasoning="历史推理"),
        extra_spans=[
            {
                "id": "other-llm",
                "sequence": 1,
                "kind": "llm",
                "name": "另一个历史 LLM 调用",
                "status": "completed",
                "input": {
                    "model": "other-model",
                    "messages": [{"role": "user", "content": "不应重放"}],
                },
                "output": chat_output(content="另一个回答", model="other-model"),
                "content": "另一个回答",
                "reasoning": "原始推理",
            },
            {
                "id": "source-tool",
                "sequence": 2,
                "kind": "tool",
                "name": "历史工具调用",
                "status": "completed",
                "input": {"city": "北京"},
                "output": {"temperature": 25},
                "tool_name": "weather",
            },
        ],
    )
    await configure_default_upstream(client, controlled_openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 201
    assert len(controlled_openai_upstream.requests) == 1
    assert controlled_openai_upstream.requests[0]["payload"] == selected_input
    body = response.json()
    assert body["replay_span"]["output"]["choices"][0]["message"]["tool_calls"] == []
    replay_detail = (
        await client.get(f"/api/traces/{body['replay_trace']['id']}")
    ).json()
    assert [span["kind"] for span in replay_detail["spans"]] == ["llm"]
    assert replay_detail["spans"][0] == body["replay_span"]


@pytest.mark.asyncio
async def test_replay_rejects_span_from_another_trace(client, openai_upstream):
    await seed_source(client, "replay-cross-source-a", span_id="span-in-a")
    await seed_source(client, "replay-cross-source-b", span_id="span-in-b")
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-cross-source-a", span_id="span-in-b")

    assert response.status_code == 404
    assert response.json() == {"detail": "该 Span 不属于当前链路"}
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_rejects_missing_span_input_without_legacy_input(
    client, openai_upstream
):
    await seed_source(client, "replay-missing-input-source")
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-missing-input-source")

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("input_location", ["span", "legacy_trace"])
async def test_replay_rejects_non_list_messages_before_upstream(
    client, openai_upstream, input_location
):
    invalid_input = {
        "model": "invalid-messages-model",
        "messages": {"role": "user", "content": "消息必须是列表"},
    }
    source_trace_id = f"replay-non-list-messages-{input_location}"
    if input_location == "span":
        await seed_source(client, source_trace_id, span_input=invalid_input)
    else:
        await seed_source(client, source_trace_id, trace_input=invalid_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_rejects_ambiguous_multi_llm_legacy_trace(client, openai_upstream):
    legacy_input = {"model": "legacy-model", "messages": [{"role": "user", "content": "旧输入"}]}
    await seed_source(
        client,
        "replay-ambiguous-legacy",
        span_id="legacy-llm-a",
        trace_input=legacy_input,
        extra_spans=[
            {
                "id": "legacy-llm-b",
                "sequence": 1,
                "kind": "llm",
                "name": "第二次旧调用",
                "status": "completed",
                "content": "另一个回答",
            }
        ],
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-ambiguous-legacy", span_id="legacy-llm-a")

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_uses_unambiguous_single_llm_legacy_trace_input(client, openai_upstream):
    legacy_input = {
        "model": "legacy-model",
        "messages": [{"role": "user", "content": "唯一旧调用"}],
        "temperature": 0.4,
    }
    await seed_source(client, "replay-single-legacy", trace_input=legacy_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-single-legacy")

    assert response.status_code == 201
    assert openai_upstream.requests[-1]["payload"] == legacy_input
    body = response.json()
    assert body["replay_span"]["input"] == legacy_input
    assert body["replay_trace"]["metadata"]["replay"]["request_source"] == (
        "legacy_single_llm_trace"
    )


@pytest.mark.asyncio
async def test_replay_uses_current_key_not_original_client_credential(client, openai_upstream):
    original_payload = {
        "model": "credential-model",
        "messages": [{"role": "user", "content": "密钥"}],
    }
    await configure_default_upstream(client, openai_upstream)
    original = await client.post(
        "/v1/chat/completions",
        json=original_payload,
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "replay-credential-source",
            "Authorization": "Bearer original-client-secret",
        },
    )
    assert original.status_code == 200
    assert openai_upstream.requests[-1]["headers"]["authorization"] == (
        "Bearer original-client-secret"
    )
    source_detail = (await client.get("/api/traces/replay-credential-source")).json()
    source_llm = next(span for span in source_detail["spans"] if span["kind"] == "llm")
    await configure_default_upstream(
        client, openai_upstream, default_upstream_key="current-server-secret"
    )

    response = await post_replay(client, "replay-credential-source", span_id=source_llm["id"])

    assert response.status_code == 201
    replay_request = openai_upstream.requests[-1]
    assert replay_request["headers"]["authorization"] == "Bearer current-server-secret"
    assert replay_request["headers"]["authorization"] != "Bearer original-client-secret"
    assert "original-client-secret" not in response.text
    replay_detail = await client.get(f"/api/traces/{response.json()['replay_trace']['id']}")
    assert "original-client-secret" not in replay_detail.text


@pytest.mark.asyncio
async def test_replay_rejects_open_current_route_circuit_before_upstream(
    client, openai_upstream
):
    source_trace_id = "replay-open-circuit-source"
    requested_model = "demo-replay-open"
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
    )
    route = await create_route(
        client,
        openai_upstream,
        name="重放时已熔断的当前路由",
        match_model=requested_model,
        target_model="denied-model",
    )
    trip = await client.post(
        "/v1/chat/completions",
        json={
            "model": requested_model,
            "messages": [{"role": "user", "content": "通过公开网关触发熔断"}],
        },
        headers={"X-AgentTrace-Id": "replay-open-circuit-trip"},
    )
    assert trip.status_code == 429
    routes = (await client.get("/api/gateway/routes")).json()["items"]
    current_route = next(item for item in routes if item["id"] == route["id"])
    assert current_route["circuit"]["status"] == "open"
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "denied-model",
            "messages": [{"role": "user", "content": "熔断时不能发送"}],
        },
        span_metadata={"requested_model": requested_model},
    )
    before_replay = len(openai_upstream.requests)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 503
    assert len(openai_upstream.requests) == before_replay


@pytest.mark.asyncio
async def test_replay_enforces_daily_token_budget_before_upstream(client, openai_upstream):
    await configure_default_upstream(client, openai_upstream, gateway_daily_token_budget=1)
    first = await client.post(
        "/v1/chat/completions",
        json={"model": "budget-model", "messages": []},
        headers={"X-AgentTrace-Id": "replay-budget-usage"},
    )
    assert first.status_code == 200
    await seed_source(
        client,
        "replay-budget-source",
        span_input={"model": "budget-model", "messages": [{"role": "user", "content": "预算"}]},
    )
    before = len(openai_upstream.requests)

    response = await post_replay(client, "replay-budget-source")

    assert response.status_code == 429
    assert "预算" in response.json()["detail"]
    assert len(openai_upstream.requests) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "replay-sensitive-value"},
        {"upstream_base": "https://user:replay-sensitive-value@example.test/v1"},
        {"messages": [{"role": "user", "content": "replay-sensitive-value"}]},
        {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "secret", "description": "replay-sensitive-value"},
                }
            ]
        },
        {"stream": True},
        {"temperature": None},
        {"temperature": 3},
        {"top_p": 2},
        {"seed": 1.5},
        {"max_tokens": 0},
    ],
)
async def test_replay_rejects_sensitive_or_invalid_overrides_without_echoing_input(
    client, openai_upstream, overrides
):
    await seed_source(
        client,
        "replay-invalid-overrides",
        span_input={"model": "safe-model", "messages": [{"role": "user", "content": "安全"}]},
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(
        client,
        "replay-invalid-overrides",
        body={"confirm": True, "overrides": overrides},
    )

    assert response.status_code == 422
    assert "replay-sensitive-value" not in response.text
    errors = response.json()["detail"]
    assert isinstance(errors, list)
    assert all("input" not in error and "ctx" not in error for error in errors)
    assert openai_upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_key", ["authorization", "api_key", "access_token"])
async def test_replay_rejects_credentials_in_historical_payload(
    client, openai_upstream, credential_key
):
    unsafe_input = {
        "model": "safe-model",
        "messages": [{"role": "user", "content": "历史凭据"}],
        credential_key: "persisted-client-secret",
    }
    await seed_source(client, "replay-persisted-credential", span_input=unsafe_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-persisted-credential")

    assert response.status_code == 409
    assert "persisted-client-secret" not in response.text
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_persists_upstream_failure_as_auditable_201_attempt(
    client, openai_upstream
):
    source_trace_id = "replay-upstream-failure-source"
    replay_trace_id = str(uuid4())
    failed_input = {"model": "denied-model", "messages": [{"role": "user", "content": "失败"}]}
    await seed_source(client, source_trace_id, span_input=failed_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(
        client,
        source_trace_id,
        idempotency_key=replay_trace_id,
    )
    assert response.status_code == 201
    retry = await post_replay(
        client,
        source_trace_id,
        idempotency_key=replay_trace_id,
    )

    assert retry.status_code == 201
    body = response.json()
    assert retry.json() == body
    assert len(openai_upstream.requests) == 1
    assert body["replay_trace"]["id"] == replay_trace_id
    assert body["replay_trace"]["parent_id"] == source_trace_id
    assert body["comparison"]["status"] == "failed"
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert body["replay_span"]["input"] == failed_input
    assert body["replay_span"]["output"] == {"error": {"message": "上游限流"}}
    assert body["replay_span"]["error"]["status_code"] == 429
    lineage = body["replay_trace"]["metadata"]["replay"]
    assert lineage["source_trace_id"] == source_trace_id
    assert lineage["source_span_id"] == "source-llm"
    assert lineage["comparison"] == body["comparison"]
    assert lineage["comparison"]["status"] == "failed"
    replay_id = body["replay_trace"]["id"]
    persisted = (await client.get(f"/api/traces/{replay_id}")).json()
    assert persisted["trace"] == body["replay_trace"]
    assert persisted["trace"]["id"] == replay_trace_id
    assert persisted["trace"]["status"] == "failed"
    failed_span = next(
        span for span in persisted["spans"] if span["id"] == body["replay_span"]["id"]
    )
    assert failed_span == body["replay_span"]
    assert failed_span["error"]["status_code"] == 429


@pytest.mark.asyncio
async def test_replay_preserves_sse_mode_and_persists_normalized_stream_output(
    client, openai_upstream
):
    fresh_stream = stream_output()
    source_output = deepcopy(fresh_stream)
    source_message = source_output["choices"][0]["message"]
    source_message["content"] = "昨天 20°C"
    source_message["reasoning_content"] = "旧流式推理"
    source_output["usage"] = {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    stream_input = {
        "model": "stream-model",
        "stream": True,
        "messages": [{"role": "user", "content": "重放流式调用"}],
        "stream_options": {"include_usage": True},
    }
    await seed_source(
        client,
        "replay-sse-source",
        span_input=stream_input,
        span_output=source_output,
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-sse-source")

    assert response.status_code == 201
    assert openai_upstream.requests[-1]["payload"] == stream_input
    body = response.json()
    replay_span = body["replay_span"]
    assert replay_span["output"] == fresh_stream
    assert replay_span["output"] != source_output
    assert replay_span["content"] == "明天 25°C"
    assert replay_span["reasoning"] == "需要"
    assert replay_span["prompt_tokens"] == 20
    assert replay_span["completion_tokens"] == 9
    assert replay_span["total_tokens"] == 29
    comparison = body["comparison"]
    assert comparison["status"] == "different"
    assert comparison["content_equal"] is False
    assert comparison["reasoning_equal"] is False
    assert comparison["tool_calls_equal"] is True
    assert 0 <= comparison["content_similarity"] < 1
    replay_detail = (
        await client.get(f"/api/traces/{body['replay_trace']['id']}")
    ).json()
    persisted_replay_span = next(
        span for span in replay_detail["spans"] if span["id"] == replay_span["id"]
    )
    assert persisted_replay_span["output"] == fresh_stream
    assert persisted_replay_span["content"] == "明天 25°C"
    assert persisted_replay_span["reasoning"] == "需要"
    assert persisted_replay_span["prompt_tokens"] == 20
    assert persisted_replay_span["completion_tokens"] == 9
    assert persisted_replay_span["total_tokens"] == 29


async def route_states(client):
    response = await client.get("/api/gateway/routes")
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["items"]}


@pytest.mark.asyncio
async def test_replay_usage_consumes_daily_budget_for_a_new_replay_key(
    client, openai_upstream
):
    await configure_default_upstream(
        client, openai_upstream, gateway_daily_token_budget=10
    )
    await seed_source(
        client,
        "replay-budget-replay-source",
        span_input={
            "model": "budget-replay-model",
            "messages": [{"role": "user", "content": "预算计算"}],
        },
    )

    first = await post_replay(client, "replay-budget-replay-source")

    assert first.status_code == 201
    assert first.json()["replay_span"]["total_tokens"] == 18
    assert len(openai_upstream.requests) == 1

    second = await post_replay(client, "replay-budget-replay-source")

    assert second.status_code == 429
    assert "预算" in second.json()["detail"]
    assert len(openai_upstream.requests) == 1
    stats = (await client.get("/api/stats")).json()
    assert stats["today_tokens"] == 18
    assert stats["daily_token_budget"] == 10


@pytest.mark.asyncio
async def test_replay_retries_next_route_after_retryable_error_and_opens_circuit(
    client, openai_upstream, scenario_openai_upstream
):
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
    )
    first = await create_route(
        client,
        scenario_openai_upstream,
        name="故障主路由",
        match_model="demo-*",
        target_model="server-error-model",
        api_key="primary-route-key",
        priority=1,
    )
    second = await create_route(
        client,
        openai_upstream,
        name="健康备用路由",
        match_model="demo-*",
        target_model="fallback-model",
        api_key="fallback-route-key",
        priority=2,
    )
    await seed_source(
        client,
        "replay-fallback-source",
        span_input={
            "model": "demo-fallback",
            "messages": [{"role": "user", "content": "回退到备用路由"}],
        },
    )

    response = await post_replay(client, "replay-fallback-source")

    assert response.status_code == 201
    body = response.json()
    assert body["replay_trace"]["status"] == "completed"
    assert body["replay_span"]["status"] == "completed"
    assert body["replay_span"]["output"]["model"] == "fallback-model"
    assert len(scenario_openai_upstream.requests) == 1
    primary_request = scenario_openai_upstream.requests[0]
    assert primary_request["payload"]["model"] == "server-error-model"
    assert primary_request["headers"]["authorization"] == "Bearer primary-route-key"
    assert len(openai_upstream.requests) == 1
    fallback_request = openai_upstream.requests[0]
    assert fallback_request["payload"]["model"] == "fallback-model"
    assert fallback_request["headers"]["authorization"] == "Bearer fallback-route-key"
    gateway = body["replay_trace"]["metadata"]["gateway"]
    attempts = gateway["attempts"]
    assert [item["outcome"] for item in attempts] == ["retry", "success"]
    assert attempts[0]["route_id"] == first["id"]
    assert attempts[0]["status_code"] == 500
    assert attempts[1]["route_id"] == second["id"]
    assert gateway["route_id"] == second["id"]

    routes = await route_states(client)
    assert routes[first["id"]]["circuit"]["status"] == "open"
    assert routes[second["id"]]["circuit"]["status"] == "closed"


@pytest.mark.asyncio
async def test_replay_connect_error_records_circuit_failure_and_falls_back(
    client, openai_upstream
):
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
    )
    first = await create_route(
        client,
        SimpleNamespace(base_url="http://127.0.0.1:1/v1"),
        name="离线主路由",
        match_model="demo-*",
        target_model="offline-model",
        priority=1,
    )
    await create_route(
        client,
        openai_upstream,
        name="在线备用路由",
        match_model="demo-*",
        target_model="fallback-model",
        priority=2,
    )
    await seed_source(
        client,
        "replay-network-fallback-source",
        span_input={
            "model": "demo-network",
            "messages": [{"role": "user", "content": "网络失败回退"}],
        },
    )

    response = await post_replay(client, "replay-network-fallback-source")

    assert response.status_code == 201
    body = response.json()
    assert body["replay_trace"]["status"] == "completed"
    assert body["replay_span"]["output"]["model"] == "fallback-model"
    assert len(openai_upstream.requests) == 1
    attempts = body["replay_trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[0]["outcome"] == "retry"
    assert attempts[0]["error_type"] == "ConnectError"
    assert attempts[0]["status_code"] is None
    assert attempts[-1]["outcome"] == "success"

    routes = await route_states(client)
    assert routes[first["id"]]["circuit"]["status"] == "open"


@pytest.mark.asyncio
async def test_replay_failure_counts_reach_circuit_threshold_then_skip_route(
    client, openai_upstream, scenario_openai_upstream
):
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=2,
        gateway_cooldown_seconds=60,
    )
    first = await create_route(
        client,
        scenario_openai_upstream,
        name="两次失败后熔断的主路由",
        match_model="demo-*",
        target_model="server-error-model",
        priority=1,
    )
    await create_route(
        client,
        openai_upstream,
        name="始终健康的备用路由",
        match_model="demo-*",
        target_model="fallback-model",
        priority=2,
    )
    await seed_source(
        client,
        "replay-circuit-threshold-source",
        span_input={
            "model": "demo-threshold",
            "messages": [{"role": "user", "content": "累计失败"}],
        },
    )

    for expected_failures in (1, 2):
        response = await post_replay(client, "replay-circuit-threshold-source")
        assert response.status_code == 201
        assert response.json()["replay_trace"]["status"] == "completed"
        assert len(scenario_openai_upstream.requests) == expected_failures
        routes = await route_states(client)
        assert routes[first["id"]]["circuit"]["failures"] == expected_failures

    routes = await route_states(client)
    assert routes[first["id"]]["circuit"]["status"] == "open"

    skipped = await post_replay(client, "replay-circuit-threshold-source")

    assert skipped.status_code == 201
    body = skipped.json()
    assert body["replay_trace"]["status"] == "completed"
    assert body["replay_trace"]["metadata"]["gateway"]["attempts"][0]["outcome"] == (
        "circuit_open"
    )
    assert len(scenario_openai_upstream.requests) == 2
    assert len(openai_upstream.requests) == 3


@pytest.mark.asyncio
async def test_replay_incomplete_sse_records_failure_and_does_not_retry(
    client, openai_upstream, scenario_openai_upstream
):
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
    )
    first = await create_route(
        client,
        scenario_openai_upstream,
        name="流未完成主路由",
        match_model="demo-*",
        target_model="incomplete-sse-model",
        priority=1,
    )
    await create_route(
        client,
        openai_upstream,
        name="不应重试的备用路由",
        match_model="demo-*",
        target_model="fallback-model",
        priority=2,
    )
    await seed_source(
        client,
        "replay-incomplete-sse-source",
        span_input={
            "model": "demo-stream",
            "stream": True,
            "messages": [{"role": "user", "content": "流式未完成"}],
        },
    )

    response = await post_replay(client, "replay-incomplete-sse-source")

    assert response.status_code == 201
    body = response.json()
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert len(scenario_openai_upstream.requests) == 1
    assert openai_upstream.requests == []
    span_error = body["replay_span"]["error"]
    assert span_error["type"] == "IncompleteStream"
    assert span_error["message"]
    assert "status_code" not in span_error
    assert body["replay_trace"]["error"] == span_error
    assert body["replay_span"]["metadata"]["upstream_status"] == 200
    assert body["comparison"]["status"] == "failed"
    assert body["replay_span"]["content"] == "未完成"
    attempts = body["replay_trace"]["metadata"]["gateway"]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "error"
    assert attempts[0]["status_code"] == 200
    assert attempts[0]["error_type"] == "IncompleteStream"

    routes = await route_states(client)
    assert routes[first["id"]]["circuit"]["status"] == "open"


@pytest.mark.asyncio
async def test_replay_sse_http_error_records_status_body_and_failed_comparison(
    client, openai_upstream
):
    await configure_default_upstream(client, openai_upstream)
    await seed_source(
        client,
        "replay-sse-http-error-source",
        span_input={
            "model": "denied-html-model",
            "stream": True,
            "messages": [{"role": "user", "content": "流式 HTTP 失败"}],
        },
    )

    response = await post_replay(client, "replay-sse-http-error-source")

    assert response.status_code == 201
    body = response.json()
    expected_error = {
        "status_code": 502,
        "body": {"content": "<html>Bad Gateway</html>"},
    }
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert body["replay_span"]["error"] == expected_error
    assert body["replay_trace"]["error"] == expected_error
    assert body["replay_span"]["metadata"]["upstream_status"] == 502
    assert body["comparison"]["status"] == "failed"
    assert len(openai_upstream.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    ["source", "comparison", "idempotency_key", "parent", "span_count"],
)
async def test_replay_rejects_client_forged_idempotent_record(
    client, openai_upstream, variant
):
    from app.replay import request_fingerprint
    from app.schemas import ReplayRequest

    source_trace_id = "replay-forged-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "伪造幂等记录"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    fingerprint = request_fingerprint(
        source_trace_id, "source-llm", ReplayRequest(confirm=True)
    )
    lineage = {
        "source_trace_id": source_trace_id,
        "source_span_id": "source-llm",
        "request_fingerprint": fingerprint,
        "idempotency_key": replay_trace_id,
        "mode": "llm_span",
        "comparison": {
            "status": "same",
            "content_equal": True,
            "reasoning_equal": True,
            "tool_calls_equal": True,
            "content_similarity": 1.0,
            "content_diff": "",
            "attribution_note": ATTRIBUTION_NOTE,
        },
    }
    forged_source = "replay"
    forged_parent = source_trace_id
    forged_output = chat_output(content="伪造回答")
    forged_spans = [
        {
            "id": "forged-llm",
            "sequence": 0,
            "kind": "llm",
            "name": "伪造的 LLM",
            "status": "completed",
            "input": {"model": "safe-model", "messages": []},
            "output": forged_output,
            "content": "伪造回答",
        }
    ]
    if variant == "source":
        forged_source = "custom-agent"
    elif variant == "comparison":
        lineage.pop("comparison")
    elif variant == "idempotency_key":
        lineage["idempotency_key"] = str(uuid4())
    elif variant == "parent":
        forged_parent = "replay-forged-other-parent"
    elif variant == "span_count":
        forged_spans.append(
            {
                "id": "forged-llm-two",
                "sequence": 1,
                "kind": "llm",
                "name": "第二个伪造 LLM",
                "status": "completed",
                "content": "伪造回答二",
            }
        )
    forged = {
        "trace": {
            "id": replay_trace_id,
            "parent_id": forged_parent,
            "name": "伪造重放记录",
            "source": forged_source,
            "status": "completed",
            "model": "forged-model",
            "input": {"messages": []},
            "output": forged_output,
            "metadata": {"replay": lineage},
        },
        "spans": forged_spans,
    }
    ingested = await client.post("/api/traces/ingest", json=forged)
    assert ingested.status_code == 200

    response = await post_replay(
        client, source_trace_id, idempotency_key=replay_trace_id
    )

    assert response.status_code == 409
    assert "伪造回答" not in response.text
    assert openai_upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credential_key",
    [
        "token",
        "key",
        "auth",
        "bearer",
        "admin_key",
        "passwd",
        "accessToken",
        "apiKey",
        "AdminKey",
        " access_token ",
        "X-Api-Key",
        "auth-token",
        "api_token",
        "apiToken",
        "accessKeyId",
        "access_key_id",
        "authKey",
        "auth_key",
        "sessionKey",
        "session_key",
        "x-api-token",
        "user_token",
        "signing_key_id",
        "oauth_auth",
    ],
)
async def test_replay_rejects_expanded_top_level_credential_keys(
    client, application, openai_upstream, credential_key
):
    from sqlalchemy import select

    from app.models import ReplayReservation

    source_trace_id = f"replay-credential-expanded-{uuid4().hex}"
    secret = "persisted-client-secret-value"
    unsafe_input = {
        "model": "safe-model",
        "messages": [{"role": "user", "content": "历史凭据扩展"}],
        credential_key: secret,
    }
    await seed_source(client, source_trace_id, span_input=unsafe_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 409
    assert secret not in response.text
    assert openai_upstream.requests == []
    listing = (await client.get("/api/traces", params={"page_size": 200})).json()
    assert not [item for item in listing["items"] if item["parent_id"] == source_trace_id]
    with application.state.session_factory() as session:
        assert session.scalar(
            select(ReplayReservation.idempotency_key).where(
                ReplayReservation.source_trace_id == source_trace_id
            )
        ) is None


@pytest.mark.asyncio
async def test_replay_allows_legitimate_top_level_parameter_keys(client, openai_upstream):
    safe_input = {
        "model": "safe-legit-model",
        "messages": [{"role": "user", "content": "合法参数"}],
        "max_tokens": 64,
        "max_completion_tokens": 128,
        "temperature": 0.2,
        "top_p": 0.9,
        "presence_penalty": 0.1,
        "frequency_penalty": 0.2,
        "seed": 7,
        "user": "audit-user",
        "stop": ["END"],
        "n": 1,
        "response_format": {"type": "json_object"},
    }
    await seed_source(client, "replay-legit-parameters", span_input=safe_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-legit-parameters")

    assert response.status_code == 201
    assert openai_upstream.requests[-1]["payload"] == safe_input


@pytest.mark.asyncio
async def test_replay_uses_trace_input_when_span_input_lacks_messages(
    client, openai_upstream
):
    legacy_input = {
        "model": "legacy-empty-span-model",
        "messages": [{"role": "user", "content": "空 Span 输入回退"}],
        "temperature": 0.3,
    }
    await seed_source(
        client,
        "replay-empty-span-input",
        span_input={},
        trace_input=legacy_input,
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-empty-span-input")

    assert response.status_code == 201
    assert openai_upstream.requests[-1]["payload"] == legacy_input
    body = response.json()
    assert body["replay_span"]["input"] == legacy_input
    assert (
        body["replay_trace"]["metadata"]["replay"]["request_source"]
        == "legacy_single_llm_trace"
    )


@pytest.mark.asyncio
async def test_replay_persist_failure_marks_reservation_terminal_and_retry_returns_failure(
    client, openai_upstream, monkeypatch
):
    source_trace_id = "replay-persist-failure-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "持久化失败"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)

    def broken_persist(*args, **kwargs):
        raise RuntimeError("forced persist failure")

    monkeypatch.setattr("app.replay.persist_openai_trace", broken_persist)

    with pytest.raises(RuntimeError, match="forced persist failure"):
        await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)

    retry = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)

    assert retry.status_code == 201
    body = retry.json()
    assert body["replay_trace"]["id"] == replay_trace_id
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert body["replay_span"]["error"]["type"] == "RuntimeError"
    assert body["comparison"]["status"] == "failed"
    assert len(openai_upstream.requests) == 1

    persisted = (await client.get(f"/api/traces/{replay_trace_id}")).json()
    assert persisted["trace"]["status"] == "failed"
    assert persisted["trace"]["parent_id"] == source_trace_id
    assert persisted["trace"]["metadata"]["replay"]["idempotency_key"] == replay_trace_id
    assert len([span for span in persisted["spans"] if span["kind"] == "llm"]) == 1


def forged_replay_shape(source_trace_id, replay_trace_id, fingerprint):
    """A fully valid replay record shape a client could POST to /api/traces/ingest."""
    forged_output = chat_output(content="伪造回答")
    lineage = {
        "source_trace_id": source_trace_id,
        "source_span_id": "source-llm",
        "request_fingerprint": fingerprint,
        "idempotency_key": replay_trace_id,
        "mode": "llm_span",
        "comparison": {
            "status": "same",
            "content_equal": True,
            "reasoning_equal": True,
            "tool_calls_equal": True,
            "content_similarity": 1.0,
            "content_diff": "",
            "attribution_note": ATTRIBUTION_NOTE,
        },
    }
    return {
        "trace": {
            "id": replay_trace_id,
            "parent_id": source_trace_id,
            "name": "LLM 单 Span 重放",
            "source": "replay",
            "status": "completed",
            "model": "forged-model",
            "input": {"model": "safe-model", "messages": []},
            "output": forged_output,
            "metadata": {"replay": lineage},
        },
        "spans": [
            {
                "id": "forged-llm",
                "sequence": 0,
                "kind": "llm",
                "name": "伪造的 LLM",
                "status": "completed",
                "input": {"model": "safe-model", "messages": []},
                "output": forged_output,
                "content": "伪造回答",
            }
        ],
    }


@pytest.mark.asyncio
async def test_replay_rejects_preingested_fully_shaped_forgery_with_zero_upstream(
    client, application, openai_upstream
):
    from app.replay import request_fingerprint
    from app.schemas import ReplayRequest

    source_trace_id = "replay-forged-shape-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "伪造完整形状"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    fingerprint = request_fingerprint(
        source_trace_id, "source-llm", ReplayRequest(confirm=True)
    )
    forged = forged_replay_shape(source_trace_id, replay_trace_id, fingerprint)
    ingested = await client.post("/api/traces/ingest", json=forged)
    assert ingested.status_code == 200

    response = await post_replay(
        client, source_trace_id, idempotency_key=replay_trace_id
    )

    assert response.status_code == 409
    assert "伪造回答" not in response.text
    assert "replay_trace" not in response.text
    assert openai_upstream.requests == []
    assert reservation_state(application, replay_trace_id) is None


@pytest.mark.asyncio
async def test_replay_retry_returns_server_snapshot_after_client_mutates_replay_trace(
    client, application, openai_upstream
):
    source_trace_id = "replay-tamper-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "先正常重放再篡改"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)

    first = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    assert first.status_code == 201
    original = first.json()
    assert original["replay_trace"]["status"] == "completed"

    tampered_trace = deepcopy(original["replay_trace"])
    tampered_trace["output"] = chat_output(content="篡改回答")
    tampered_trace["status"] = "completed"
    tampered_trace["metadata"]["replay"]["comparison"] = {
        **tampered_trace["metadata"]["replay"]["comparison"],
        "status": "same",
        "content_equal": True,
        "content_similarity": 1.0,
        "content_diff": "",
    }
    tampered_span = deepcopy(original["replay_span"])
    tampered_span["content"] = "篡改回答"
    tampered_span["output"] = tampered_trace["output"]
    mutated = await client.post(
        "/api/traces/ingest",
        json={"trace": tampered_trace, "spans": [tampered_span]},
    )
    assert mutated.status_code == 200

    retry = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)

    assert retry.status_code == 201
    assert retry.json() == original
    assert "篡改回答" not in retry.text
    assert len(openai_upstream.requests) == 1
    state = reservation_state(application, replay_trace_id)
    assert state["status"] == "completed"
    assert state["response_json"] == original


@pytest.mark.asyncio
async def test_replay_same_key_retry_after_source_deleted_returns_cached_snapshot(
    client, application, openai_upstream
):
    source_trace_id = "replay-deleted-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "删除来源后重试"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)

    first = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    assert first.status_code == 201
    original = first.json()

    deleted = await client.delete(f"/api/traces/{source_trace_id}")
    assert deleted.status_code == 204

    retry = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)

    assert retry.status_code == 201
    assert retry.json() == original
    assert len(openai_upstream.requests) == 1
    assert reservation_state(application, replay_trace_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_replay_rejects_preexisting_trace_at_key_without_reservation(
    client, application, openai_upstream
):
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        "replay-preexisting-key-source",
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "占用幂等键"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    occupy = {
        "trace": {
            "id": replay_trace_id,
            "name": "普通链路占用了幂等键",
            "source": "custom-agent",
            "status": "completed",
            "output": {"answer": "preexisting-secret-answer"},
        },
        "spans": [],
    }
    response = await client.post("/api/traces/ingest", json=occupy)
    assert response.status_code == 200

    replay = await post_replay(
        client, "replay-preexisting-key-source", idempotency_key=replay_trace_id
    )

    assert replay.status_code == 409
    assert "preexisting-secret-answer" not in replay.text
    assert openai_upstream.requests == []
    assert reservation_state(application, replay_trace_id) is None


@pytest.mark.asyncio
async def test_replay_running_reservation_returns_409_without_upstream(
    client, application, openai_upstream
):
    from app.models import ReplayReservation
    from app.replay import request_fingerprint
    from app.schemas import ReplayRequest

    source_trace_id = "replay-direct-running-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "预留执行中"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    with application.state.session_factory() as session:
        session.add(
            ReplayReservation(
                idempotency_key=replay_trace_id,
                request_fingerprint=request_fingerprint(
                    source_trace_id, "source-llm", ReplayRequest(confirm=True)
                ),
                source_trace_id=source_trace_id,
                source_span_id="source-llm",
                status="running",
            )
        )
        session.commit()

    response = await post_replay(
        client, source_trace_id, idempotency_key=replay_trace_id
    )

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_terminal_reservation_without_snapshot_returns_409(
    client, application, openai_upstream
):
    from app.models import ReplayReservation
    from app.replay import request_fingerprint
    from app.schemas import ReplayRequest

    source_trace_id = "replay-terminal-snapshot-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "终态无快照"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)
    with application.state.session_factory() as session:
        session.add(
            ReplayReservation(
                idempotency_key=replay_trace_id,
                request_fingerprint=request_fingerprint(
                    source_trace_id, "source-llm", ReplayRequest(confirm=True)
                ),
                source_trace_id=source_trace_id,
                source_span_id="source-llm",
                status="completed",
                response_json=None,
            )
        )
        session.commit()

    response = await post_replay(
        client, source_trace_id, idempotency_key=replay_trace_id
    )

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_persists_reservation_snapshots_for_completed_and_failed(
    client, application, scenario_openai_upstream
):
    await configure_default_upstream(client, scenario_openai_upstream)
    completed_source = "replay-reservation-completed-source"
    failed_source = "replay-reservation-failed-source"
    completed_key = str(uuid4())
    failed_key = str(uuid4())
    await seed_source(
        client,
        completed_source,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "正常完成"}],
        },
    )
    await seed_source(
        client,
        failed_source,
        span_id="failed-source-llm",
        span_input={
            "model": "server-error-model",
            "messages": [{"role": "user", "content": "上游失败"}],
        },
    )

    completed = await post_replay(
        client, completed_source, idempotency_key=completed_key
    )
    failed = await post_replay(
        client,
        failed_source,
        span_id="failed-source-llm",
        idempotency_key=failed_key,
    )

    assert completed.status_code == 201
    assert completed.json()["replay_trace"]["status"] == "completed"
    completed_state = reservation_state(application, completed_key)
    assert completed_state["status"] == "completed"
    assert completed_state["response_json"] == completed.json()
    assert completed_state["source_trace_id"] == completed_source
    assert completed_state["source_span_id"] == "source-llm"

    assert failed.status_code == 201
    assert failed.json()["replay_trace"]["status"] == "failed"
    assert failed.json()["comparison"]["status"] == "failed"
    failed_state = reservation_state(application, failed_key)
    assert failed_state["status"] == "failed"
    assert failed_state["response_json"] == failed.json()
    assert failed_state["error"] is not None
    assert failed_state["error"]["status_code"] == 500


@pytest.mark.asyncio
async def test_replay_inband_sse_error_marks_failed_and_records_circuit_failure(
    client, openai_upstream, scenario_openai_upstream
):
    await configure_default_upstream(
        client,
        openai_upstream,
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
    )
    first = await create_route(
        client,
        scenario_openai_upstream,
        name="流内错误主路由",
        match_model="demo-*",
        target_model="inband-error-sse-model",
        priority=1,
    )
    await create_route(
        client,
        openai_upstream,
        name="流内错误不应重试的备用路由",
        match_model="demo-*",
        target_model="fallback-model",
        priority=2,
    )
    await seed_source(
        client,
        "replay-inband-sse-error-source",
        span_input={
            "model": "demo-stream",
            "stream": True,
            "messages": [{"role": "user", "content": "流内错误"}],
        },
    )

    response = await post_replay(client, "replay-inband-sse-error-source")

    assert response.status_code == 201
    body = response.json()
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert body["comparison"]["status"] == "failed"
    span_error = body["replay_span"]["error"]
    assert span_error["type"] == "UpstreamSSEError"
    assert span_error["message"] == "上游流内错误"
    assert span_error["body"]["error"]["code"] == "bad_gateway_stream"
    assert body["replay_trace"]["error"] == span_error
    assert body["replay_span"]["metadata"]["upstream_status"] == 200
    attempts = body["replay_trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[0]["outcome"] == "error"
    assert attempts[0]["status_code"] == 200
    assert attempts[0]["error_type"] == "UpstreamSSEError"
    assert len(attempts) == 1
    assert len(scenario_openai_upstream.requests) == 1
    assert openai_upstream.requests == []

    routes = await route_states(client)
    assert routes[first["id"]]["circuit"]["status"] == "open"


@pytest.mark.asyncio
async def test_replay_snapshot_commit_failure_never_returns_fake_success(
    client, application, openai_upstream, monkeypatch
):
    source_trace_id = "replay-snapshot-commit-failure-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "快照提交失败"}],
        },
    )
    await configure_default_upstream(client, openai_upstream)

    def broken_snapshot(*args, **kwargs):
        raise RuntimeError("forced snapshot commit failure")

    monkeypatch.setattr("app.replay.store_reservation_snapshot", broken_snapshot)

    with pytest.raises(RuntimeError, match="forced snapshot commit failure"):
        await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)

    assert len(openai_upstream.requests) == 1
    state = reservation_state(application, replay_trace_id)
    assert state["status"] == "failed"
    assert state["response_json"] is None

    retry = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    assert retry.status_code == 409
    assert len(openai_upstream.requests) == 1


@pytest.mark.asyncio
async def test_running_replay_trace_rejects_ingest_and_delete(
    client, application, controlled_openai_upstream
):
    source_trace_id = "replay-running-mutation-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "运行期间禁止篡改"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived

    forged = {
        "trace": {
            "id": replay_trace_id,
            "parent_id": "forged-parent",
            "name": "伪造重放链路",
            "source": "forged-source",
            "status": "completed",
            "metadata": {"replay": {"kind": "forged"}},
        },
        "spans": [
            {
                "id": "forged-running-span",
                "trace_id": replay_trace_id,
                "sequence": -100,
                "kind": "llm",
                "name": "伪造 LLM",
                "content": "FORGED-SPAN",
                "status": "completed",
            }
        ],
    }
    ingested = await client.post("/api/traces/ingest", json=forged)
    deleted = await client.delete(f"/api/traces/{replay_trace_id}")
    controlled_openai_upstream.release.set()
    replayed = await replay_task

    assert ingested.status_code == 409
    assert deleted.status_code == 409
    assert replayed.status_code == 201
    body = replayed.json()
    assert body["replay_trace"]["parent_id"] == source_trace_id
    assert body["replay_trace"]["source"] == "replay"
    assert body["replay_trace"]["metadata"]["replay"]["source_trace_id"] == source_trace_id
    assert body["replay_trace"]["metadata"]["replay"]["source_span_id"] == "source-llm"
    assert body["replay_trace"]["span_count"] == 1
    assert body["replay_span"]["content"] == "无工具重放回答"
    assert "FORGED-SPAN" not in replayed.text
    assert len(controlled_openai_upstream.requests) == 1

    retry = await post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    assert retry.status_code == 201
    assert retry.json() == body
    assert len(controlled_openai_upstream.requests) == 1


@pytest.mark.asyncio
async def test_running_replay_trace_rejects_openai_proxy_writes(
    client, controlled_openai_upstream, openai_upstream
):
    source_trace_id = "replay-running-proxy-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "运行期间禁止代理追加"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived

    proxy_response = await client.post(
        "/v1/chat/completions",
        headers={
            "X-AgentTrace-Id": replay_trace_id,
            "X-Upstream-Base": openai_upstream.base_url,
        },
        json={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "不应到达上游"}],
        },
    )
    controlled_openai_upstream.release.set()
    replayed = await replay_task

    assert proxy_response.status_code == 409
    assert openai_upstream.requests == []
    assert replayed.status_code == 201
    assert replayed.json()["replay_trace"]["span_count"] == 1
    assert replayed.json()["replay_span"]["content"] == "无工具重放回答"
    assert len(controlled_openai_upstream.requests) == 1


@pytest.mark.asyncio
async def test_replay_rejects_acronym_camelcase_credential_key(client, openai_upstream):
    source_trace_id = "replay-acronym-credential-source"
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [],
            "APIToken": "persisted-secret",
        },
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_replay_allows_official_prompt_cache_key_parameter(client, openai_upstream):
    source_trace_id = "replay-prompt-cache-key-source"
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [],
            "prompt_cache_key": "public-cache-prefix",
        },
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 201
    assert len(openai_upstream.requests) == 1
    assert openai_upstream.requests[0]["payload"]["prompt_cache_key"] == "public-cache-prefix"


@pytest.mark.asyncio
async def test_replay_reservation_creation_and_ingest_are_atomic(
    client, application, controlled_openai_upstream, monkeypatch
):
    import app.audit as audit_module

    source_trace_id = "replay-ingest-race-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "预留与写入必须互斥"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    original_guard = audit_module.ensure_trace_not_running_replay
    guard_passed = threading.Event()
    release_ingest = threading.Event()

    def paused_guard(session, trace_id):
        original_guard(session, trace_id)
        if trace_id == replay_trace_id:
            guard_passed.set()
            if not release_ingest.wait(5):
                raise RuntimeError("timed out waiting to release ingest")

    monkeypatch.setattr(audit_module, "ensure_trace_not_running_replay", paused_guard)
    forged = {
        "trace": {
            "id": replay_trace_id,
            "name": "RACE-FORGED",
            "source": "forged-source",
            "status": "completed",
        },
        "spans": [
            {
                "id": "race-forged-span",
                "trace_id": replay_trace_id,
                "sequence": -100,
                "kind": "llm",
                "name": "竞态伪造 LLM",
                "content": "RACE-FORGED-CONTENT",
                "status": "completed",
            }
        ],
    }
    ingest_task = asyncio.create_task(client.post("/api/traces/ingest", json=forged))
    checked = await asyncio.to_thread(guard_passed.wait, 5)
    if not checked:
        release_ingest.set()
        await ingest_task
    assert checked
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    # With a real write lock, Replay cannot reach upstream until ingest commits.
    upstream_before_ingest_commit = await asyncio.to_thread(
        controlled_openai_upstream.request_arrived.wait, 0.5
    )
    release_ingest.set()
    ingested = await ingest_task
    replayed = await replay_task

    assert upstream_before_ingest_commit is False
    assert ingested.status_code == 200
    assert replayed.status_code == 409
    assert controlled_openai_upstream.requests == []
    assert reservation_state(application, replay_trace_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["APITOKEN", "ACCESSTOKEN", "AUTHTOKEN", "SESSIONKEY"])
async def test_replay_rejects_compact_uppercase_credential_keys(
    client, openai_upstream, field
):
    source_trace_id = f"replay-compact-credential-{field.lower()}"
    await seed_source(
        client,
        source_trace_id,
        span_input={"model": "safe-model", "messages": [], field: "persisted-secret"},
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, source_trace_id)

    assert response.status_code == 409
    assert openai_upstream.requests == []


@pytest.mark.asyncio
async def test_running_replay_trace_rejects_mcp_before_upstream(
    client, controlled_openai_upstream, openai_upstream
):
    source_trace_id = "replay-running-mcp-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "运行期间禁止 MCP 写入"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived

    mcp_response = await client.post(
        "/mcp",
        headers={
            "X-AgentTrace-Id": replay_trace_id,
            "X-MCP-Upstream": openai_upstream.base_url + "/chat/completions",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    controlled_openai_upstream.release.set()
    replayed = await replay_task

    assert mcp_response.status_code == 409
    assert openai_upstream.requests == []
    assert replayed.status_code == 201
    assert replayed.json()["replay_trace"]["span_count"] == 1
    assert len(controlled_openai_upstream.requests) == 1


@pytest.mark.asyncio
async def test_running_replay_rejects_llm_check_and_reanalyze_writes(
    client, application, controlled_openai_upstream, monkeypatch
):
    source_trace_id = "replay-running-analysis-source"
    replay_trace_id = str(uuid4())
    await seed_source(
        client,
        source_trace_id,
        span_input={
            "model": "safe-model",
            "messages": [{"role": "user", "content": "运行期间禁止二次分析"}],
        },
    )
    await configure_default_upstream(client, controlled_openai_upstream)
    settings = application.state.settings
    settings.enable_llm_check = True
    settings.check_model_key = "synthetic-test-only"
    settings.check_model_name = "synthetic-check-model"
    llm_called = False

    async def fake_llm_check(trace, settings):
        nonlocal llm_called
        llm_called = True
        return []

    monkeypatch.setattr("app.api.run_llm_check", fake_llm_check)
    controlled_openai_upstream.release.clear()
    replay_task = asyncio.create_task(
        post_replay(client, source_trace_id, idempotency_key=replay_trace_id)
    )
    arrived = await asyncio.to_thread(controlled_openai_upstream.request_arrived.wait, 5)
    assert arrived

    reanalyzed = await client.post(f"/api/traces/{replay_trace_id}/reanalyze")
    llm_checked = await client.post(f"/api/traces/{replay_trace_id}/llm-check")
    controlled_openai_upstream.release.set()
    replayed = await replay_task

    assert reanalyzed.status_code == 409
    assert llm_checked.status_code == 409
    assert llm_called is False
    assert replayed.status_code == 201
    assert "llm_check" not in replayed.json()["replay_trace"]["metadata"]
    assert len(controlled_openai_upstream.requests) == 1
