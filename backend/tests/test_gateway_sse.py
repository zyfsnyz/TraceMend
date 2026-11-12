import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import anyio
import anyio.lowlevel
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.gateway import CircuitRegistry, UpstreamCandidate
from app.proxy import relay_sse


@pytest.fixture
def controlled_sse_upstream():
    application = FastAPI()
    release = threading.Event()
    requests = []

    @application.post("/v1/chat/completions")
    async def completions(request: Request):
        payload = await request.json()
        requests.append(payload)
        model = payload.get("model")

        async def generate():
            event = {"choices": [{"index": 0, "delta": {"content": "partial"}}]}
            yield f"data: {json.dumps(event)}\n\n".encode()
            if model == "incomplete-model":
                return
            if model == "interrupted-model":
                raise RuntimeError("controlled upstream interruption")
            await asyncio.to_thread(release.wait, 5)
            yield b"data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

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
        raise RuntimeError("本地上游启动失败")
    yield SimpleNamespace(
        base_url=f"http://127.0.0.1:{port}/v1", release=release, requests=requests
    )
    release.set()
    server.should_exit = True
    thread.join(timeout=10)


@pytest.mark.asyncio
async def test_sse_response_headers_do_not_record_success_before_done(
    client, application, controlled_sse_upstream
):
    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": controlled_sse_upstream.base_url,
            "gateway_failure_threshold": 2,
            "gateway_cooldown_seconds": 60,
        },
    )
    application.state.circuits.record_failure("default", 2, 60)
    body = json.dumps(
        {"model": "complete-model", "stream": True, "messages": []}
    ).encode()
    response_started = asyncio.Event()
    disconnected = asyncio.Event()
    messages = []
    request_delivered = False

    async def receive():
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            response_started.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"x-agenttrace-id", b"sse-header-timing"),
        ],
        "client": ("testserver", 123),
        "server": ("testserver", 80),
    }
    task = asyncio.create_task(application(scope, receive, send))
    try:
        await asyncio.wait_for(response_started.wait(), timeout=5)
        assert application.state.circuits.snapshot("default")["failures"] == 1
    finally:
        controlled_sse_upstream.release.set()
    await asyncio.wait_for(task, timeout=5)

    assert application.state.circuits.snapshot("default")["failures"] == 0
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert response_body.endswith(b"data: [DONE]\n\n")
    detail = await client.get("/api/traces/sse-header-timing")
    attempts = detail.json()["trace"]["metadata"]["gateway"]["attempts"]
    assert attempts[-1]["outcome"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["incomplete-model", "interrupted-model"])
async def test_incomplete_or_interrupted_sse_records_failure(
    model, client, application, controlled_sse_upstream
):
    await client.put(
        "/api/settings",
        json={
            "default_upstream_base": controlled_sse_upstream.base_url,
            "gateway_failure_threshold": 1,
            "gateway_cooldown_seconds": 60,
        },
    )
    trace_id = f"sse-final-{model}"
    response = await client.post(
        "/v1/chat/completions",
        json={"model": model, "stream": True, "messages": []},
        headers={"X-AgentTrace-Id": trace_id},
    )
    assert response.status_code == 200
    assert application.state.circuits.snapshot("default")["status"] == "open"

    detail = await client.get(f"/api/traces/{trace_id}")
    trace = detail.json()["trace"]
    assert trace["status"] == "failed"
    attempts = trace["metadata"]["gateway"]["attempts"]
    assert attempts[-1]["outcome"] == "error"
    assert attempts[-1]["status_code"] == 200


@pytest.mark.asyncio
async def test_explicit_sse_does_not_touch_default_circuit_or_gateway_metadata(
    client, application, controlled_sse_upstream
):
    await client.put(
        "/api/settings",
        json={"gateway_failure_threshold": 2, "gateway_cooldown_seconds": 60},
    )
    application.state.circuits.record_failure("default", 2, 60)
    controlled_sse_upstream.release.set()
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "complete-model", "stream": True, "messages": []},
        headers={
            "X-Upstream-Base": controlled_sse_upstream.base_url,
            "X-AgentTrace-Id": "explicit-sse",
        },
    )
    assert response.status_code == 200
    assert application.state.circuits.snapshot("default")["failures"] == 1
    detail = await client.get("/api/traces/explicit-sse")
    trace = detail.json()["trace"]
    assert trace["status"] == "completed"
    assert "gateway" not in trace["metadata"]


class FakeUpstream:
    def __init__(self, close_error=False):
        self.close_error = close_error
        self.close_attempted = False
        self.status_code = 200

    async def aiter_raw(self):
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.close_attempted = True
        if self.close_error:
            raise RuntimeError("upstream close failed")


class FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_source", ["persist", "upstream_close"])
async def test_relay_sse_always_closes_both_resources_during_finalization_failures(
    failure_source, monkeypatch
):
    upstream = FakeUpstream(close_error=failure_source == "upstream_close")
    client = FakeClient()

    def fail_persistence(*args, **kwargs):
        raise RuntimeError("persistence failed")

    monkeypatch.setattr(
        "app.proxy.persist_openai_trace",
        fail_persistence if failure_source == "persist" else lambda *args, **kwargs: None,
    )
    with pytest.raises(RuntimeError):
        async for _ in relay_sse(
            upstream,
            client,
            None,
            "sse-cleanup",
            {"model": "demo", "messages": []},
            datetime.now(UTC),
        ):
            pass

    assert upstream.close_attempted is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_sse_cancellation_after_done_is_not_recorded_as_success(monkeypatch):
    class CancelledUpstream(FakeUpstream):
        async def aiter_raw(self):
            yield b"data: [DONE]\n\n"
            raise asyncio.CancelledError()

    upstream = CancelledUpstream()
    client = FakeClient()
    circuits = CircuitRegistry()
    candidate = UpstreamCandidate("http://upstream.test/v1", "demo")
    settings = SimpleNamespace(
        gateway_failure_threshold=1,
        gateway_cooldown_seconds=60,
        default_input_price_per_million=0,
        default_output_price_per_million=0,
    )
    attempts = []
    monkeypatch.setattr("app.proxy.persist_openai_trace", lambda *args, **kwargs: None)

    with pytest.raises(asyncio.CancelledError):
        async for _ in relay_sse(
            upstream,
            client,
            None,
            "sse-cancelled",
            {"model": "demo", "messages": []},
            datetime.now(UTC),
            candidate,
            settings,
            "demo",
            attempts,
            circuits,
        ):
            pass

    assert circuits.snapshot("default")["status"] == "open"
    assert attempts[-1]["outcome"] == "error"
    assert upstream.close_attempted is True
    assert client.closed is True


class BlockingUpstream:
    def __init__(self, events):
        self.status_code = 200
        self.events = events
        self.close_started = False
        self.close_completed = False

    async def aiter_raw(self):
        event = {"choices": [{"index": 0, "delta": {"content": "partial"}}]}
        yield f"data: {json.dumps(event)}\n\n".encode()
        await anyio.Event().wait()

    async def aclose(self):
        self.close_started = True
        self.events.append("upstream-close-start")
        await anyio.lowlevel.checkpoint()
        await anyio.lowlevel.checkpoint()
        self.close_completed = True
        self.events.append("upstream-close-end")


class SlowClient:
    def __init__(self, events):
        self.events = events
        self.close_attempted = False
        self.closed = False

    async def aclose(self):
        self.close_attempted = True
        self.events.append("client-close-start")
        await anyio.lowlevel.checkpoint()
        await anyio.lowlevel.checkpoint()
        self.closed = True
        self.events.append("client-close-end")


@pytest.mark.asyncio
async def test_relay_sse_shield_completes_async_cleanup_after_consumer_cancellation(
    monkeypatch,
):
    events = []
    upstream = BlockingUpstream(events)
    client = SlowClient(events)
    delivered = anyio.Event()
    persisted = []
    monkeypatch.setattr(
        "app.proxy.persist_openai_trace",
        lambda *args, **kwargs: persisted.append(args),
    )

    async def consume():
        async for _ in relay_sse(
            upstream,
            client,
            None,
            "sse-disconnect-shield",
            {"model": "demo", "messages": []},
            datetime.now(UTC),
        ):
            delivered.set()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await delivered.wait()
        task_group.cancel_scope.cancel()

    assert events == [
        "upstream-close-start",
        "upstream-close-end",
        "client-close-start",
        "client-close-end",
    ]
    assert upstream.close_completed is True
    assert client.closed is True
    assert persisted
