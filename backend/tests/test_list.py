from copy import deepcopy

import pytest
from factories import trace_payload


async def seed(client, trace_id, name, status, tags, started_at):
    payload = deepcopy(trace_payload(trace_id))
    payload["trace"].update(name=name, status=status, tags=tags, started_at=started_at)
    for span in payload["spans"]:
        old_id = span["id"]
        span["id"] = f"{trace_id}-{old_id}"
        if span.get("parent_span_id"):
            span["parent_span_id"] = f"{trace_id}-{span['parent_span_id']}"
    response = await client.post("/api/traces/ingest", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_filters_and_paginates_with_trace_summaries(client):
    await seed(client, "t1", "北京差旅", "completed", ["travel"], "2026-11-01T10:00:00Z")
    await seed(client, "t2", "上海差长", "failed", ["travel"], "2026-11-05T10:00:00Z")
    await seed(client, "t3", "天气问答", "completed", ["weather"], "2026-11-10T10:00:00Z")

    response = await client.get(
        "/api/traces",
        params={
            "q": "差",
            "status": "completed",
            "tag": "travel",
            "from": "2026-11-01T00:00:00Z",
            "to": "2026-11-03T00:00:00Z",
            "page": 1,
            "page_size": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["pages"] == 1
    assert body["items"][0]["id"] == "t1"
    assert body["items"][0]["span_count"] == 2
    assert body["items"][0]["risk_count"] == 0
    assert body["items"][0]["total_tokens"] == 0

    second_page = await client.get("/api/traces", params={"page": 2, "page_size": 2})
    assert second_page.json()["total"] == 3
    assert second_page.json()["pages"] == 2
    assert len(second_page.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_risk_filter_selects_only_traces_with_findings(client):
    payload = trace_payload("risky")
    payload["spans"].append(
        {
            "id": "broken-tool",
            "sequence": 2,
            "kind": "tool",
            "name": "搜索",
            "status": "error",
            "tool_name": "search",
            "error": {"message": "timeout"},
        }
    )
    await client.post("/api/traces/ingest", json=payload)
    clean_payload = trace_payload("clean")
    for span in clean_payload["spans"]:
        span["id"] = f"clean-{span['id']}"
        if span.get("parent_span_id"):
            span["parent_span_id"] = f"clean-{span['parent_span_id']}"
    await client.post("/api/traces/ingest", json=clean_payload)

    risky = await client.get("/api/traces", params={"risk": "any"})
    clean = await client.get("/api/traces", params={"risk": "none"})

    assert [item["id"] for item in risky.json()["items"]] == ["risky"]
    assert [item["id"] for item in clean.json()["items"]] == ["clean"]
