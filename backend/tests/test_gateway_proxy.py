import json

import httpx
import pytest
from sse_data import sse_payloads

pytest_plugins = ["upstream_fixtures"]


async def create_route(client, **overrides):
    payload = {
        "name": "默认路由",
        "match_model": "demo-*",
        "target_model": "routed-model",
        "upstream_base": "http://127.0.0.1:1/v1",
        "priority": 10,
        "enabled": True,
        "input_price_per_million": 0,
        "output_price_per_million": 0,
    }
    payload.update(overrides)
    response = await client.post("/api/gateway/routes", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_saved_default_model_and_key_simplify_agent_connection(
    client, openai_upstream
):
    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "default_upstream_model": "server-default-model",
            "default_upstream_key": "default-server-secret",
        },
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "你好"}]},
        headers={"X-AgentTrace-Id": "gateway-default-connection"},
    )
    assert response.status_code == 200
    upstream = openai_upstream.requests[-1]
    assert upstream["payload"]["model"] == "server-default-model"
    assert upstream["headers"]["authorization"] == "Bearer default-server-secret"
    detail = await client.get("/api/traces/gateway-default-connection")
    assert detail.json()["trace"]["model"] == "server-default-model"
    assert "default-server-secret" not in detail.text


@pytest.mark.asyncio
async def test_default_model_matches_wildcard_route(client, openai_upstream):
    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "default_upstream_model": "demo-default",
        },
    )
    route = await create_route(
        client,
        name="默认模型通配符路由",
        match_model="demo-*",
        target_model="routed-default-model",
        upstream_base=openai_upstream.base_url,
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"messages": []},
        headers={"X-AgentTrace-Id": "gateway-default-route"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == route["id"]
    assert openai_upstream.requests[-1]["payload"]["model"] == "routed-default-model"
    detail = await client.get("/api/traces/gateway-default-route")
    gateway = detail.json()["trace"]["metadata"]["gateway"]
    assert gateway["route_id"] == route["id"]
    assert gateway["target_model"] == "routed-default-model"


@pytest.mark.asyncio
async def test_route_rewrites_model_uses_route_key_and_estimates_cost(
    client, openai_upstream
):
    route = await create_route(
        client,
        name="付费模型",
        upstream_base=openai_upstream.base_url,
        target_model="routed-paid-model",
        api_key="route-server-secret",
        input_price_per_million=100,
        output_price_per_million=200,
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-smart", "messages": []},
        headers={"X-AgentTrace-Id": "gateway-routed"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == route["id"]
    upstream = openai_upstream.requests[-1]
    assert upstream["payload"]["model"] == "routed-paid-model"
    assert upstream["headers"]["authorization"] == "Bearer route-server-secret"
    detail = await client.get("/api/traces/gateway-routed")
    trace = detail.json()["trace"]
    gateway = trace["metadata"]["gateway"]
    assert gateway["requested_model"] == "demo-smart"
    assert gateway["route_id"] == route["id"]
    assert gateway["cost_usd"] == pytest.approx(0.0025)
    assert trace["estimated_cost_usd"] == pytest.approx(0.0025)
    assert "route-server-secret" not in detail.text


@pytest.mark.asyncio
async def test_retryable_failure_falls_back_and_opens_circuit(client, openai_upstream):
    first = await create_route(
        client,
        name="限流主路由",
        target_model="denied-model",
        upstream_base=openai_upstream.base_url,
        priority=1,
    )
    second = await create_route(
        client,
        name="可用备用路由",
        target_model="fallback-model",
        upstream_base=openai_upstream.base_url,
        priority=2,
    )
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-fallback", "messages": []},
        headers={"X-AgentTrace-Id": "gateway-fallback"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == second["id"]
    assert [item["payload"]["model"] for item in openai_upstream.requests] == [
        "denied-model",
        "fallback-model",
    ]
    detail = await client.get("/api/traces/gateway-fallback")
    attempts = detail.json()["trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[0]["status_code"] == 429
    assert attempts[-1]["outcome"] == "success"

    routes = (await client.get("/api/gateway/routes")).json()["items"]
    first_state = next(item for item in routes if item["id"] == first["id"])
    assert first_state["circuit"]["status"] == "open"

    before = len(openai_upstream.requests)
    again = await client.post(
        "/v1/chat/completions", json={"model": "demo-fallback", "messages": []}
    )
    assert again.status_code == 200
    assert len(openai_upstream.requests) == before + 1
    assert openai_upstream.requests[-1]["payload"]["model"] == "fallback-model"

    reset = await client.post(f"/api/gateway/routes/{first['id']}/reset")
    assert reset.status_code == 200
    assert reset.json()["circuit"]["status"] == "closed"


@pytest.mark.asyncio
async def test_error_body_read_http_error_uses_retry_circuit_and_cleanup_path(
    client, monkeypatch
):
    class FailingBody(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            raise httpx.ReadError("controlled body read failure")
            yield b""

        async def aclose(self):
            self.closed = True

    failing_body = FailingBody()

    async def handle(request):
        payload = json.loads(request.content)
        if payload["model"] == "read-failure-model":
            return httpx.Response(502, stream=failing_body)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"data: [DONE]\n\n"),
            headers={"Content-Type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handle)
    real_async_client = httpx.AsyncClient
    created_clients = []

    def client_factory(*args, **kwargs):
        upstream_client = real_async_client(transport=transport, timeout=60)
        created_clients.append(upstream_client)
        return upstream_client

    monkeypatch.setattr("app.proxy.httpx.AsyncClient", client_factory)
    first = await create_route(
        client,
        name="错误体读取失败路由",
        target_model="read-failure-model",
        upstream_base="http://first-upstream.test/v1",
        priority=1,
    )
    second = await create_route(
        client,
        name="错误体读取后备路由",
        target_model="healthy-model",
        upstream_base="http://second-upstream.test/v1",
        priority=2,
    )
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-read-error", "stream": True, "messages": []},
        headers={"X-AgentTrace-Id": "gateway-body-read-error"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == second["id"]
    assert failing_body.closed is True
    assert len(created_clients) == 2
    assert all(upstream_client.is_closed for upstream_client in created_clients)

    detail = await client.get("/api/traces/gateway-body-read-error")
    attempts = detail.json()["trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[0]["outcome"] == "retry"
    assert attempts[0]["error_type"] == "ReadError"
    assert attempts[1]["outcome"] == "success", attempts
    routes = (await client.get("/api/gateway/routes")).json()["items"]
    first_state = next(item for item in routes if item["id"] == first["id"])
    assert first_state["circuit"]["status"] == "open"


@pytest.mark.asyncio
async def test_error_body_read_and_upstream_close_failures_still_fall_back(
    client, monkeypatch
):
    class FailingBody(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            raise httpx.ReadError("controlled body read failure")
            yield b""

        async def aclose(self):
            self.closed = True
            raise RuntimeError("controlled upstream close failure")

    failing_body = FailingBody()

    async def handle(request):
        payload = json.loads(request.content)
        if payload["model"] == "read-failure-model":
            return httpx.Response(502, stream=failing_body)
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"data: [DONE]\n\n"),
            headers={"Content-Type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handle)
    real_async_client = httpx.AsyncClient
    created_clients = []

    def client_factory(*args, **kwargs):
        upstream_client = real_async_client(transport=transport, timeout=60)
        created_clients.append(upstream_client)
        return upstream_client

    monkeypatch.setattr("app.proxy.httpx.AsyncClient", client_factory)
    first = await create_route(
        client,
        name="错误体与关闭双失败路由",
        target_model="read-failure-model",
        upstream_base="http://first-upstream.test/v1",
        priority=1,
    )
    second = await create_route(
        client,
        name="关闭失败后的后备路由",
        target_model="healthy-model",
        upstream_base="http://second-upstream.test/v1",
        priority=2,
    )
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-close-error", "stream": True, "messages": []},
        headers={"X-AgentTrace-Id": "gateway-close-error"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == second["id"]
    assert failing_body.closed is True
    assert len(created_clients) == 2
    assert all(upstream_client.is_closed for upstream_client in created_clients)

    detail = await client.get("/api/traces/gateway-close-error")
    attempts = detail.json()["trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[0]["outcome"] == "retry"
    assert attempts[0]["error_type"] == "ReadError"
    assert attempts[1]["outcome"] == "success", attempts
    routes = (await client.get("/api/gateway/routes")).json()["items"]
    first_state = next(item for item in routes if item["id"] == first["id"])
    assert first_state["circuit"]["status"] == "open"


@pytest.mark.asyncio
async def test_daily_token_budget_blocks_before_calling_upstream(client, openai_upstream):
    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": openai_upstream.base_url,
            "gateway_daily_token_budget": 1,
        },
    )
    first = await client.post(
        "/v1/chat/completions",
        json={"model": "budget-model", "messages": []},
        headers={"X-AgentTrace-Id": "gateway-budget-first"},
    )
    assert first.status_code == 200
    before = len(openai_upstream.requests)
    blocked = await client.post(
        "/v1/chat/completions",
        json={"model": "budget-model", "messages": []},
        headers={"X-AgentTrace-Id": "gateway-budget-blocked"},
    )
    assert blocked.status_code == 429
    assert "预算" in blocked.json()["detail"]
    assert len(openai_upstream.requests) == before
    stats = (await client.get("/api/stats?days=30")).json()
    assert stats["today_tokens"] >= 18
    assert stats["daily_token_budget"] == 1
    explicit = await client.post(
        "/v1/chat/completions",
        json={"model": "budget-explicit", "messages": []},
        headers={"X-Upstream-Base": openai_upstream.base_url},
    )
    assert explicit.status_code == 200


@pytest.mark.asyncio
async def test_explicit_upstream_header_bypasses_web_routes(client, openai_upstream):
    await client.put("/api/settings", json={"default_upstream_key": "must-not-leak"})
    await create_route(
        client,
        target_model="should-not-be-used",
        upstream_base="http://127.0.0.1:1/v1",
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-explicit", "messages": []},
        headers={"X-Upstream-Base": openai_upstream.base_url},
    )
    assert response.status_code == 200
    assert openai_upstream.requests[-1]["payload"]["model"] == "demo-explicit"
    assert "authorization" not in openai_upstream.requests[-1]["headers"]


@pytest.mark.asyncio
async def test_explicit_upstream_preserves_missing_model_and_omits_gateway_metadata(
    client, openai_upstream
):
    await client.put(
        "/api/settings", json={"default_upstream_model": "must-not-be-injected"}
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"messages": []},
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "explicit-direct-semantics",
        },
    )
    assert response.status_code == 200
    assert "model" not in openai_upstream.requests[-1]["payload"]
    detail = await client.get("/api/traces/explicit-direct-semantics")
    trace = detail.json()["trace"]
    assert trace["model"] is None
    assert "gateway" not in trace["metadata"]


@pytest.mark.asyncio
async def test_explicit_upstream_bypasses_open_default_circuit_without_closing_it(
    client, application, openai_upstream
):
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    application.state.circuits.record_failure("default", 1, 60)
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-explicit", "messages": []},
        headers={"X-Upstream-Base": openai_upstream.base_url},
    )
    assert response.status_code == 200
    assert application.state.circuits.snapshot("default")["status"] == "open"


@pytest.mark.asyncio
async def test_explicit_upstream_failure_does_not_open_default_circuit_or_write_metadata(
    client, application, openai_upstream
):
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "denied-html-model", "messages": []},
        headers={
            "X-Upstream-Base": openai_upstream.base_url,
            "X-AgentTrace-Id": "explicit-failure",
        },
    )
    assert response.status_code == 502
    assert application.state.circuits.snapshot("default") == {
        "status": "closed",
        "failures": 0,
        "open_remaining_seconds": 0,
    }
    detail = await client.get("/api/traces/explicit-failure")
    assert "gateway" not in detail.json()["trace"]["metadata"]


@pytest.mark.asyncio
async def test_explicit_network_failure_does_not_touch_default_circuit_or_metadata(
    client, application
):
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 1, "gateway_cooldown_seconds": 60},
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-explicit", "messages": []},
        headers={
            "X-Upstream-Base": "http://127.0.0.1:1/v1",
            "X-AgentTrace-Id": "explicit-network-failure",
        },
    )
    assert response.status_code == 502
    assert application.state.circuits.snapshot("default")["failures"] == 0
    detail = await client.get("/api/traces/explicit-network-failure")
    assert "gateway" not in detail.json()["trace"]["metadata"]


@pytest.mark.asyncio
async def test_routed_sse_stream_preserves_bytes_and_records_gateway(client, openai_upstream):
    route = await create_route(
        client,
        name="流式路由",
        target_model="stream-model",
        upstream_base=openai_upstream.base_url,
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "demo-stream", "stream": True, "messages": []},
        headers={"X-AgentTrace-Id": "gateway-routed-stream"},
    )
    assert response.status_code == 200
    assert response.headers["x-agenttrace-route-id"] == route["id"]
    assert response.content == b"".join(sse_payloads())
    detail = await client.get("/api/traces/gateway-routed-stream")
    body = detail.json()
    assert body["trace"]["status"] == "completed"
    assert body["trace"]["metadata"]["gateway"]["route_id"] == route["id"]
    assert body["trace"]["total_tokens"] == 29
