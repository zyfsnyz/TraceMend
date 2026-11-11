import pytest
from factories import trace_payload


@pytest.mark.asyncio
async def test_detail_sorts_spans_and_delete_removes_trace(client):
    payload = trace_payload()
    payload["spans"].reverse()
    await client.post("/api/traces/ingest", json=payload)

    detail = await client.get("/api/traces/trace-1")
    assert [span["sequence"] for span in detail.json()["spans"]] == [0, 1]

    deleted = await client.delete("/api/traces/trace-1")
    assert deleted.status_code == 204
    assert (await client.get("/api/traces/trace-1")).status_code == 404
    assert (await client.delete("/api/traces/trace-1")).status_code == 404
