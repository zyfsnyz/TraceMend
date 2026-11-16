from copy import deepcopy
from uuid import uuid4

import pytest

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
    assert response.json()["replay_trace"]["metadata"]["replay"]["overrides"] == overrides


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
    assert replay_span["id"] in {span["id"] for span in replay_detail["spans"]}
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
async def test_replay_rejects_span_from_another_trace(client, openai_upstream):
    await seed_source(client, "replay-cross-source-a", span_id="span-in-a")
    await seed_source(client, "replay-cross-source-b", span_id="span-in-b")
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-cross-source-a", span_id="span-in-b")

    assert response.status_code == 404
    assert response.json() == {"detail": "该 Span 不属于当前链路"}
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
    failed_input = {"model": "denied-model", "messages": [{"role": "user", "content": "失败"}]}
    await seed_source(client, "replay-upstream-failure-source", span_input=failed_input)
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-upstream-failure-source")

    assert response.status_code == 201
    body = response.json()
    assert body["comparison"]["status"] == "failed"
    assert body["replay_trace"]["status"] == "failed"
    assert body["replay_span"]["status"] == "failed"
    assert body["replay_span"]["input"] == failed_input
    assert body["replay_span"]["output"] == {"error": {"message": "上游限流"}}
    assert body["replay_span"]["error"]["status_code"] == 429
    assert body["replay_trace"]["metadata"]["replay"]["comparison"]["status"] == "failed"
    replay_id = body["replay_trace"]["id"]
    persisted = (await client.get(f"/api/traces/{replay_id}")).json()
    assert persisted["trace"]["id"] == replay_id
    assert persisted["trace"]["status"] == "failed"
    failed_span = next(
        span for span in persisted["spans"] if span["id"] == body["replay_span"]["id"]
    )
    assert failed_span["error"]["status_code"] == 429


@pytest.mark.asyncio
async def test_replay_preserves_sse_mode_and_persists_normalized_stream_output(
    client, openai_upstream
):
    original_stream = stream_output()
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
        span_output=original_stream,
    )
    await configure_default_upstream(client, openai_upstream)

    response = await post_replay(client, "replay-sse-source")

    assert response.status_code == 201
    assert openai_upstream.requests[-1]["payload"] == stream_input
    body = response.json()
    assert body["replay_span"]["output"] == original_stream
    assert body["replay_span"]["content"] == "明天 25°C"
    assert body["replay_span"]["reasoning"] == "需要"
    assert body["replay_span"]["total_tokens"] == 29
    assert body["comparison"]["status"] == "same"
    assert body["comparison"]["content_equal"] is True
    assert body["comparison"]["reasoning_equal"] is True
    assert body["comparison"]["tool_calls_equal"] is True
    assert body["comparison"]["content_similarity"] == 1.0
