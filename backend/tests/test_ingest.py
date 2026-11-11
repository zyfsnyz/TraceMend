import pytest
from factories import trace_payload, updated_payload


@pytest.mark.asyncio
async def test_ingest_upserts_trace_and_replaces_spans_idempotently(client):
    first = await client.post("/api/traces/ingest", json=trace_payload())
    assert first.status_code == 200
    assert first.json() == {"trace_id": "trace-1", "span_count": 2, "finding_count": 0}

    second = await client.post("/api/traces/ingest", json=updated_payload())
    assert second.status_code == 200
    assert second.json()["span_count"] == 1

    detail = await client.get("/api/traces/trace-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["trace"]["name"] == "更新后的助手"
    assert body["trace"]["tags"] == ["updated"]
    assert body["trace"]["duration_ms"] == 2000
    assert body["trace"]["metadata"] == {"tenant": "local"}
    assert [span["id"] for span in body["spans"]] == ["span-user"]
    assert body["trace"]["span_count"] == 1
    assert body["trace"]["risk_count"] == 0


@pytest.mark.asyncio
async def test_ingest_rejects_unknown_span_kind(client):
    payload = trace_payload()
    payload["spans"][0]["kind"] = "not-a-span-kind"

    response = await client.post("/api/traces/ingest", json=payload)

    assert response.status_code == 422
