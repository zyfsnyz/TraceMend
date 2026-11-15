from datetime import UTC, datetime, timedelta

import pytest

pytest_plugins = ["upstream_fixtures", "mcp_upstream_fixture"]


def as_datetime(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_openai_same_trace_id_appends_spans_and_aggregates_trace(client, openai_upstream):
    headers = {
        "X-Upstream-Base": openai_upstream.base_url,
        "X-AgentTrace-Id": "openai-append",
    }
    first_payload = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "first question"}],
        "response_content": "first answer",
    }
    second_payload = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "second question"}],
        "response_content": "second answer",
    }

    first_response = await client.post(
        "/v1/chat/completions", json=first_payload, headers=headers
    )
    assert first_response.status_code == 200
    first_detail = (await client.get("/api/traces/openai-append")).json()
    second_response = await client.post(
        "/v1/chat/completions", json=second_payload, headers=headers
    )
    assert second_response.status_code == 200
    detail = (await client.get("/api/traces/openai-append")).json()

    trace = detail["trace"]
    assert trace["prompt_tokens"] == 22
    assert trace["completion_tokens"] == 14
    assert trace["total_tokens"] == 36
    assert trace["output"]["choices"][0]["message"]["content"] == "second answer"
    assert trace["started_at"] == first_detail["trace"]["started_at"]
    assert as_datetime(trace["ended_at"]) >= as_datetime(first_detail["trace"]["ended_at"])
    assert trace["duration_ms"] == pytest.approx(
        (as_datetime(trace["ended_at"]) - as_datetime(trace["started_at"])).total_seconds()
        * 1000
    )

    spans = detail["spans"]
    old_ids = {span["id"] for span in first_detail["spans"]}
    assert old_ids < {span["id"] for span in spans}
    assert len({span["id"] for span in spans}) == 6
    assert [span["sequence"] for span in spans] == [0, 1, 2, 3, 4, 5]
    answers = [span["content"] for span in spans if span["kind"] == "llm"]
    assert answers == ["first answer", "second answer"]


@pytest.mark.asyncio
async def test_daily_usage_follows_each_invocation_when_trace_id_crosses_days(
    client, application, openai_upstream
):
    from app.models import Trace

    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "gateway_daily_token_budget": 1,
        },
    )
    headers = {"X-AgentTrace-Id": "openai-cross-day-budget"}
    first = await client.post(
        "/v1/chat/completions",
        json={"model": "cross-day-model", "messages": []},
        headers=headers,
    )
    assert first.status_code == 200

    yesterday = datetime.now(UTC) - timedelta(days=1)
    with application.state.session_factory() as session:
        trace = session.get(Trace, "openai-cross-day-budget")
        trace.started_at = yesterday
        trace.created_at = yesterday
        for span in trace.spans:
            span.started_at = yesterday
        session.commit()

    second = await client.post(
        "/v1/chat/completions",
        json={"model": "cross-day-model", "messages": []},
        headers=headers,
    )
    assert second.status_code == 200
    stats = (await client.get("/api/stats?days=30")).json()
    assert stats["today_tokens"] == 18

    from app.models import Span

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with application.state.session_factory() as session:
        session.add(
            Span(
                id="future-budget-span",
                trace_id="openai-cross-day-budget",
                sequence=99,
                kind="llm",
                name="未来时钟调用",
                started_at=tomorrow,
                total_tokens=999,
            )
        )
        session.commit()
    future_stats = (await client.get("/api/stats?days=30")).json()
    assert future_stats["today_tokens"] == 18

    before = len(openai_upstream.requests)
    third = await client.post(
        "/v1/chat/completions",
        json={"model": "cross-day-model", "messages": []},
        headers=headers,
    )
    assert third.status_code == 429
    assert len(openai_upstream.requests) == before


@pytest.mark.asyncio
async def test_openai_failed_invocation_remains_failed_after_later_success(
    client, openai_upstream
):
    headers = {
        "X-Upstream-Base": openai_upstream.base_url,
        "X-AgentTrace-Id": "openai-sticky-failure",
    }
    failed = await client.post(
        "/v1/chat/completions",
        json={"model": "denied-model", "messages": []},
        headers=headers,
    )
    succeeded = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-model", "messages": [], "response_content": "later answer"},
        headers=headers,
    )

    assert failed.status_code == 429
    assert succeeded.status_code == 200
    detail = (await client.get("/api/traces/openai-sticky-failure")).json()
    assert detail["trace"]["status"] == "failed"
    llm_spans = [span for span in detail["spans"] if span["kind"] == "llm"]
    assert [span["status"] for span in llm_spans] == ["failed", "completed"]
    assert llm_spans[0]["metadata"]["upstream_status"] == 429
    assert llm_spans[1]["content"] == "later answer"


@pytest.mark.asyncio
async def test_mcp_failed_call_remains_failed_after_later_success(client, mcp_upstream):
    headers = {"X-MCP-Upstream": mcp_upstream.url, "X-AgentTrace-Id": "mcp-sticky-failure"}
    failed_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "explode", "arguments": {}},
    }
    success_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "weather", "arguments": {"city": "北京"}},
    }

    assert (await client.post("/mcp", json=failed_payload, headers=headers)).status_code == 200
    first_detail = (await client.get("/api/traces/mcp-sticky-failure")).json()
    assert (await client.post("/mcp", json=success_payload, headers=headers)).status_code == 200
    detail = (await client.get("/api/traces/mcp-sticky-failure")).json()

    assert detail["trace"]["status"] == "failed"
    assert detail["trace"]["started_at"] == first_detail["trace"]["started_at"]
    assert as_datetime(detail["trace"]["ended_at"]) >= as_datetime(
        first_detail["trace"]["ended_at"]
    )
    tools = [span for span in detail["spans"] if span["kind"] == "tool"]
    assert [span["status"] for span in tools] == ["error", "completed"]
    assert tools[0]["error"]["code"] == -32000
    assert detail["trace"]["output"]["result"]["content"][0]["text"] == "天气晴"
