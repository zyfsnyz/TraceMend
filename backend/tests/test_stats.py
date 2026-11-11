from copy import deepcopy

import pytest
from factories import trace_payload


@pytest.mark.asyncio
async def test_stats_aggregate_audit_metrics(client):
    first = deepcopy(trace_payload("stats-1"))
    first["trace"].update(
        started_at="2026-11-11T08:00:00Z",
        ended_at="2026-11-11T08:00:02Z",
        total_tokens=15,
        prompt_tokens=10,
        completion_tokens=5,
    )
    first["spans"].append(
        {"id": "stats-tool-1", "sequence": 2, "kind": "tool", "tool_name": "search"}
    )
    second = deepcopy(trace_payload("stats-2"))
    second["trace"].update(
        status="failed",
        started_at="2026-11-10T08:00:00Z",
        ended_at="2026-11-10T08:00:01Z",
        total_tokens=5,
    )
    second["spans"] = [
        {
            "id": "stats-tool-2",
            "sequence": 0,
            "kind": "tool",
            "tool_name": "search",
            "status": "error",
            "error": "failed",
        }
    ]
    for span in second["spans"]:
        span.setdefault("name", "搜索")
    await client.post("/api/traces/ingest", json=first)
    await client.post("/api/traces/ingest", json=second)

    response = await client.get("/api/stats", params={"days": 30})

    assert response.status_code == 200
    stats = response.json()
    assert stats["total_traces"] == 2
    assert stats["completed_traces"] == 1
    assert stats["error_traces"] == 1
    assert stats["risk_traces"] == 1
    assert stats["total_findings"] == 1
    assert stats["total_tokens"] == 20
    assert stats["avg_duration_ms"] == 1500
    assert stats["tool_calls"] == 2
    assert stats["models"] == {"demo-model": 2}
    assert sum(day["count"] for day in stats["daily"]) == 2
