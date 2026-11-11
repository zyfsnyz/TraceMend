import pytest

pytest_plugins = ["mcp_upstream_fixture"]


@pytest.mark.asyncio
async def test_mcp_real_upstream_records_protocol_tool_calls_and_shared_trace(client, mcp_upstream):
    headers = {
        "X-MCP-Upstream": mcp_upstream.url,
        "X-AgentTrace-Id": "mcp-shared",
        "Authorization": "Bearer mcp-secret",
        "X-MCP-Client": "integration-test",
    }
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "weather", "arguments": {"city": "北京"}},
        },
    ]
    for payload in calls:
        response = await client.post("/mcp", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["id"] == payload["id"]
        assert response.headers["x-agenttrace-id"] == "mcp-shared"

    assert len(mcp_upstream.requests) == 3
    assert mcp_upstream.requests[0]["headers"]["authorization"] == "Bearer mcp-secret"
    assert mcp_upstream.requests[0]["headers"]["x-mcp-client"] == "integration-test"
    assert "x-mcp-upstream" not in mcp_upstream.requests[0]["headers"]
    assert "x-agenttrace-id" not in mcp_upstream.requests[0]["headers"]

    detail = await client.get("/api/traces/mcp-shared")
    body = detail.json()
    assert body["trace"]["source"] == "mcp-proxy"
    assert body["trace"]["status"] == "completed"
    protocol = [span for span in body["spans"] if span["kind"] == "mcp"]
    assert [span["input"]["method"] for span in protocol] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    tool = next(span for span in body["spans"] if span["kind"] == "tool")
    assert tool["tool_name"] == "weather"
    assert tool["tool_arguments"] == {"city": "北京"}
    assert tool["tool_result"]["content"][0]["text"] == "天气晴"
    assert "mcp-secret" not in detail.text


@pytest.mark.asyncio
async def test_mcp_json_rpc_error_marks_tool_failed(client, mcp_upstream):
    payload = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "explode", "arguments": {}},
    }
    response = await client.post(
        "/mcp/any/path",
        json=payload,
        headers={"X-MCP-Upstream": mcp_upstream.url, "X-AgentTrace-Id": "mcp-error"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["message"] == "工具爆炸"
    detail = await client.get("/api/traces/mcp-error")
    assert detail.json()["trace"]["status"] == "failed"
    tool = next(span for span in detail.json()["spans"] if span["kind"] == "tool")
    assert tool["status"] == "error"
    assert tool["error"]["code"] == -32000


@pytest.mark.asyncio
async def test_mcp_without_upstream_returns_clear_error(client):
    response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 400
    assert "X-MCP-Upstream" in response.json()["detail"]
    assert "DEFAULT_MCP_UPSTREAM" in response.json()["detail"]
