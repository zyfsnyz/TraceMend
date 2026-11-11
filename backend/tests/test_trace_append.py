from datetime import datetime

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
