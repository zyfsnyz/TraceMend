from typing import cast

import anyio
import pytest
from fastapi import Request


class FakeRequest:
    def __init__(self, application, payload):
        self.app = application
        self.headers = {"X-Upstream-Base": "http://upstream.test/v1"}
        self.payload = payload

    async def json(self):
        return self.payload


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stream_send", "nonstream_post", "error_read"])
async def test_preheader_cancellation_shields_and_completes_resource_cleanup(
    application, monkeypatch, mode
):
    from app.proxy import chat_completions

    started = anyio.Event()
    closed = []

    class FakeUpstreamResponse:
        is_success = False
        status_code = 502
        headers = {}

        async def aread(self):
            started.set()
            await anyio.sleep_forever()

        async def aclose(self):
            closed.append("upstream-start")
            await anyio.lowlevel.checkpoint()
            closed.append("upstream-end")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, *args, **kwargs):
            return object()

        async def send(self, *args, **kwargs):
            started.set()
            await anyio.sleep_forever()

        async def post(self, *args, **kwargs):
            if mode == "error_read":
                return FakeUpstreamResponse()
            started.set()
            await anyio.sleep_forever()

        async def aclose(self):
            closed.append("client-start")
            await anyio.lowlevel.checkpoint()
            closed.append("client-end")

    monkeypatch.setattr("app.proxy.httpx.AsyncClient", FakeClient)
    request = cast(
        Request,
        FakeRequest(
            application,
            {"model": "cancel-model", "messages": [], "stream": mode == "stream_send"},
        ),
    )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(chat_completions, request)
        with anyio.fail_after(3):
            await started.wait()
        task_group.cancel_scope.cancel()

    expected = []
    if mode == "error_read":
        expected.extend(["upstream-start", "upstream-end"])
    expected.extend(["client-start", "client-end"])
    assert closed == expected
