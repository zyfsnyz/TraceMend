import asyncio
import socket
import threading
import time
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI, Request

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
def controlled_openai_upstream():
    """Real local upstream with a test-controlled async gate and no tool calls."""
    application = FastAPI()
    requests = []
    request_arrived = threading.Event()
    release = threading.Event()
    release.set()

    @application.post("/v1/chat/completions")
    async def completions(request: Request):
        payload = await request.json()
        requests.append({"payload": payload, "headers": dict(request.headers)})
        request_arrived.set()
        await asyncio.to_thread(release.wait)
        return {
            "id": "chatcmpl-controlled",
            "object": "chat.completion",
            "model": payload.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "无工具重放回答",
                        "reasoning_content": "仅重放所选 LLM",
                        "tool_calls": [],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
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
        raise RuntimeError("受控本地上游启动失败")
    try:
        yield SimpleNamespace(
            base_url=f"http://127.0.0.1:{port}/v1",
            requests=requests,
            request_arrived=request_arrived,
            release=release,
        )
    finally:
        release.set()
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
