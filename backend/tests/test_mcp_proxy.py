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
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b'{"jsonrpc":', "请求体不是有效的 JSON"),
        (b"", "请求体不是有效的 JSON"),
        (b"[]", "请求体必须是 JSON 对象"),
        (b'"text"', "请求体必须是 JSON 对象"),
        (b"1", "请求体必须是 JSON 对象"),
        (b"null", "请求体必须是 JSON 对象"),
    ],
)
async def test_mcp_validates_json_before_upstream_configuration(client, body, detail):
    response = await client.post(
        "/mcp", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.asyncio
async def test_mcp_without_upstream_returns_clear_error(client):
    response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 400
    assert "X-MCP-Upstream" in response.json()["detail"]
    assert "DEFAULT_MCP_UPSTREAM" in response.json()["detail"]


@pytest.mark.asyncio
async def test_mcp_gzip_response_removes_stale_encoding_and_preserves_decoded_body(
    client, mcp_upstream
):
    from mcp_upstream_fixture import MCP_GZIP_BODY

    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 77, "method": "gzip", "params": {}},
        headers={"X-MCP-Upstream": mcp_upstream.url, "X-AgentTrace-Id": "mcp-gzip"},
    )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == MCP_GZIP_BODY
    assert response.json()["result"] == {"content": "gzip ok"}


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [None, [], "bad", 1])
async def test_mcp_malformed_params_do_not_change_upstream_response(
    client, mcp_upstream, params
):
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 78, "method": "tools/call", "params": params},
        headers={
            "X-MCP-Upstream": mcp_upstream.url,
            "X-AgentTrace-Id": "mcp-audit-isolation",
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["content"][0]["text"] == "天气晴"
    assert (await client.get("/api/traces/mcp-audit-isolation")).status_code == 200


@pytest.mark.asyncio
async def test_mcp_standard_tool_error_saves_result_and_creates_finding(client, mcp_upstream):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 80,
            "method": "tools/call",
            "params": {"name": "standard-error", "arguments": {}},
        },
        headers={"X-MCP-Upstream": mcp_upstream.url, "X-AgentTrace-Id": "mcp-standard-error"},
    )

    assert response.status_code == 200
    detail = (await client.get("/api/traces/mcp-standard-error")).json()
    assert detail["trace"]["status"] == "failed"
    tool = next(span for span in detail["spans"] if span["kind"] == "tool")
    assert tool["status"] == "error"
    assert tool["error"] == response.json()["result"]
    assert tool["error"]["isError"] is True
    tool_errors = [
        finding for finding in detail["findings"] if finding["rule_code"] == "tool_error"
    ]
    assert len(tool_errors) == 1
    assert tool_errors[0]["span_id"] == tool["id"]


@pytest.mark.asyncio
async def test_mcp_null_top_level_error_is_not_a_failure(client, mcp_upstream):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/call",
            "params": {"name": "null-error", "arguments": {}},
        },
        headers={"X-MCP-Upstream": mcp_upstream.url, "X-AgentTrace-Id": "mcp-null-error"},
    )

    assert response.status_code == 200
    detail = (await client.get("/api/traces/mcp-null-error")).json()
    assert detail["trace"]["status"] == "completed"
    tool = next(span for span in detail["spans"] if span["kind"] == "tool")
    assert tool["status"] == "completed"
    assert tool["error"] is None
    assert not [finding for finding in detail["findings"] if finding["rule_code"] == "tool_error"]
