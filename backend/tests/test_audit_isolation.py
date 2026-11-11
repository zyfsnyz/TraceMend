import json
from datetime import UTC, datetime

import pytest

from app.proxy import SSEAccumulator, relay_sse

MALFORMED_EVENTS = [
    None,
    [],
    "not-an-object",
    {"choices": None},
    {"choices": {}},
    {"choices": [None, 1, "bad"]},
    {"choices": [{"index": 0, "delta": []}]},
    {"choices": [{"index": 0, "delta": {"content": {}, "reasoning_content": []}}]},
    {"choices": [{"index": 0, "delta": {"tool_calls": {}}}]},
    {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        None,
                        {"index": 0, "function": []},
                        {"index": "bad", "function": {"name": {}, "arguments": []}},
                    ]
                },
            }
        ]
    },
]


@pytest.mark.parametrize("event", MALFORMED_EVENTS)
def test_sse_accumulator_normalizes_malformed_audit_events(event):
    accumulator = SSEAccumulator()
    raw = (
        f"data: {json.dumps(event)}\n\n".encode()
        + b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
        + b"data: [DONE]\n\n"
    )

    accumulator.feed(raw)
    accumulator.finish()
    output = accumulator.output({"model": "demo"})

    assert accumulator.done is True
    assert isinstance(output["choices"], list)
    assert output["choices"][0]["message"]["content"] == "ok"
    assert isinstance(output["choices"][0]["message"]["tool_calls"], list)


class FakeUpstream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def aiter_raw(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_malformed_sse_never_changes_raw_chunks_or_blocks_done(monkeypatch):
    chunks = [
        f"data: {json.dumps(event)}\n\n".encode() for event in MALFORMED_EVENTS
    ] + [b"data: [DONE]\n\n"]
    upstream = FakeUpstream(chunks)
    client = FakeClient()
    monkeypatch.setattr("app.proxy.persist_openai_trace", lambda *args, **kwargs: None)

    relayed = [
        chunk
        async for chunk in relay_sse(
            upstream,
            client,
            None,
            "malformed-stream",
            {"model": "demo", "messages": []},
            datetime.now(UTC),
        )
    ]

    assert relayed == chunks
    assert upstream.closed is True
    assert client.closed is True
