import socket

import pytest
from sse_data import sse_payloads

pytest_plugins = ["upstream_fixtures"]


@pytest.mark.asyncio
async def test_openai_nonstream_real_upstream_persists_trace_and_forwards_auth(
    client, openai_upstream
):
    payload = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "查询北京天气"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "parameters": {"type": "object", "required": ["city"]},
                },
            }
        ],
    }
    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "proxy-nonstream",
            "Authorization": "Bearer local-secret",
            "X-Custom-Header": "forward-me",
            "X-Admin-Key": "must-not-reach-upstream",
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "明天 25°C"
    assert response.headers["X-AgentTrace-Id"] == "proxy-nonstream"
    upstream = openai_upstream.requests[0]
    assert upstream["payload"] == payload
    assert upstream["headers"]["authorization"] == "Bearer local-secret"
    assert upstream["headers"]["x-custom-header"] == "forward-me"
    assert "x-upstream-base" not in upstream["headers"]
    assert "x-agenttrace-id" not in upstream["headers"]
    assert "x-admin-key" not in upstream["headers"]

    detail = await client.get("/api/traces/proxy-nonstream")
    assert detail.status_code == 200
    body = detail.json()
    assert body["trace"]["status"] == "completed"
    assert body["trace"]["source"] == "openai-proxy"
    assert body["trace"]["total_tokens"] == 18
    assert body["trace"]["input"] == payload
    assert body["trace"]["output"]["id"] == "chatcmpl-local"
    assert {span["kind"] for span in body["spans"]} == {"user", "llm", "tool"}
    llm = next(span for span in body["spans"] if span["kind"] == "llm")
    tool = next(span for span in body["spans"] if span["kind"] == "tool")
    assert llm["reasoning"] == "查过天气"
    assert tool["tool_name"] == "weather"
    assert tool["tool_arguments"] == {"city": "北京"}
    assert tool["metadata"]["tool_schema"]["required"] == ["city"]
    assert "local-secret" not in detail.text
    assert "must-not-reach-upstream" not in detail.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "status_code", "content", "content_type", "trace_status"),
    [
        (
            "html-model",
            200,
            "<html><body>上游维护中</body></html>",
            "text/html; charset=utf-8",
            "completed",
        ),
        ("plain-model", 200, "纯文本结果", "text/plain; charset=utf-8", "completed"),
        ("empty-model", 200, "", "application/octet-stream", "completed"),
        (
            "denied-html-model",
            502,
            "<html>Bad Gateway</html>",
            "text/html; charset=utf-8",
            "failed",
        ),
    ],
)
async def test_openai_nonstream_non_json_is_transparent_and_persisted(
    client, openai_upstream, model, status_code, content, content_type, trace_status
):
    trace_id = f"proxy-{model}"
    response = await client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": []},
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": trace_id,
        },
    )

    assert response.status_code == status_code
    assert response.content == content.encode()
    assert response.headers["content-type"] == content_type

    detail = await client.get(f"/api/traces/{trace_id}")
    assert detail.status_code == 200
    trace = detail.json()["trace"]
    assert trace["status"] == trace_status
    assert trace["output"] == {"content": content}
    if status_code >= 400:
        assert trace["error"]["status_code"] == status_code
        assert trace["error"]["body"] == {"content": content}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b'{"model":', "请求体不是有效的 JSON"),
        (b"", "请求体不是有效的 JSON"),
        (b"[]", "请求体必须是 JSON 对象"),
        (b'"text"', "请求体必须是 JSON 对象"),
        (b"1", "请求体必须是 JSON 对象"),
        (b"null", "请求体必须是 JSON 对象"),
    ],
)
async def test_openai_rejects_invalid_json_body_with_chinese_422(client, body, detail):
    response = await client.post(
        "/v1/chat/completions", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": detail}


@pytest.mark.asyncio
async def test_openai_sse_real_upstream_preserves_bytes_and_aggregates_tool_calls(
    client, openai_upstream
):
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "stream-model",
            "stream": True,
            "messages": [{"role": "user", "content": "天气"}],
        },
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "proxy-stream",
            "Authorization": "Bearer stream-secret",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-agenttrace-id"] == "proxy-stream"
    assert response.content == b"".join(sse_payloads())

    detail = await client.get("/api/traces/proxy-stream")
    body = detail.json()
    assert body["trace"]["status"] == "completed"
    assert body["trace"]["total_tokens"] == 29
    llm = next(span for span in body["spans"] if span["kind"] == "llm")
    assert llm["content"] == "明天 25°C"
    assert llm["reasoning"] == "需要"
    tools = [span for span in body["spans"] if span["kind"] == "tool"]
    assert {tool["tool_name"] for tool in tools} == {"weather", "calendar"}
    weather = next(tool for tool in tools if tool["tool_name"] == "weather")
    assert weather["tool_arguments"] == {"city": "北京"}
    assert "stream-secret" not in detail.text


@pytest.mark.asyncio
async def test_openai_upstream_error_is_transparent_and_persists_failed_trace(
    client, openai_upstream
):
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "denied-model", "messages": []},
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "proxy-denied",
        },
    )

    assert response.status_code == 429
    assert response.json() == {"error": {"message": "上游限流"}}
    detail = await client.get("/api/traces/proxy-denied")
    assert detail.json()["trace"]["status"] == "failed"
    llm = next(span for span in detail.json()["spans"] if span["kind"] == "llm")
    assert llm["metadata"]["upstream_status"] == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_openai_network_failure_returns_502_after_persisting_trace(client, stream):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    trace_id = f"proxy-network-{stream}"
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "offline", "stream": stream, "messages": []},
        headers={
            "X-Upstream-Base": f"http://127.0.0.1:{port}/v1",
            "X-AgentTrace-Id": trace_id,
            "Authorization": "Bearer must-not-leak",
        },
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "上游网络错误"}
    assert response.headers["x-agenttrace-id"] == trace_id
    detail = await client.get(f"/api/traces/{trace_id}")
    assert detail.status_code == 200
    assert detail.json()["trace"]["status"] == "failed"
    assert detail.json()["trace"]["error"]["type"] == "ConnectError"
    assert "must-not-leak" not in detail.text


@pytest.mark.asyncio
async def test_openai_gzip_response_removes_stale_encoding_and_preserves_decoded_body(
    client, openai_upstream
):
    from upstream_fixtures import OPENAI_GZIP_BODY

    response = await client.post(
        "/v1/chat/completions",
        json={"model": "gzip-model", "messages": []},
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "proxy-gzip",
        },
    )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == OPENAI_GZIP_BODY
    assert response.json()["choices"][0]["message"]["content"] == "gzip ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_patch",
    [
        {"messages": None},
        {"messages": {}},
        {"messages": [None, 1, "bad"]},
        {"messages": [], "tools": None},
        {"messages": [], "tools": [None, 1, {"function": None}]},
    ],
)
async def test_openai_malformed_audit_payload_does_not_change_upstream_response(
    client, openai_upstream, payload_patch
):
    payload = {"model": "plain-model", "messages": []} | payload_patch
    response = await client.post(
        "/v1/chat/completions",
        json=payload,
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "proxy-audit-isolation",
        },
    )

    assert response.status_code == 200
    assert response.content == "纯文本结果".encode()
    assert (await client.get("/api/traces/proxy-audit-isolation")).status_code == 200
