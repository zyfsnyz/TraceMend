from copy import deepcopy

import pytest
from factories import trace_payload


async def ingest_spans(client, spans, output="最终回答"):
    payload = deepcopy(trace_payload("rules"))
    payload["trace"]["output"] = {"content": output}
    payload["spans"] = spans
    response = await client.post("/api/traces/ingest", json=payload)
    assert response.status_code == 200
    detail = await client.get("/api/traces/rules")
    return response.json(), detail.json()["findings"]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_value", [None, "", [], {}])
async def test_empty_tool_result_creates_finding_with_span_evidence(client, empty_value):
    _, findings = await ingest_spans(
        client,
        [
            {
                "id": "result-1",
                "sequence": 0,
                "kind": "tool_result",
                "name": "搜索结果",
                "tool_name": "search",
                "tool_result": empty_value,
            }
        ],
    )

    assert len(findings) == 1
    assert findings[0]["rule_code"] == "empty_tool_result"
    assert findings[0]["span_id"] == "result-1"
    assert findings[0]["evidence"]["span_id"] == "result-1"
    assert "空" in findings[0]["title"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "reason"),
    [('{"city":', "invalid_json"), ({}, "missing_required")],
)
async def test_invalid_arguments_use_tool_schema(client, arguments, reason):
    _, findings = await ingest_spans(
        client,
        [
            {
                "id": "call-1",
                "sequence": 0,
                "kind": "tool",
                "name": "天气查询",
                "tool_name": "weather",
                "tool_arguments": arguments,
                "metadata": {
                    "tool_schema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    }
                },
            }
        ],
    )

    assert len(findings) == 1
    assert findings[0]["rule_code"] == "invalid_arguments"
    assert findings[0]["evidence"]["reason"] == reason
    assert findings[0]["span_id"] == "call-1"


@pytest.mark.asyncio
async def test_schema_validation_ignores_valid_json_and_flags_only_bad_call(client):
    _, findings = await ingest_spans(
        client,
        [
            {
                "id": "valid-call",
                "sequence": 0,
                "kind": "tool",
                "tool_name": "weather",
                "tool_arguments": '{"city":"北京"}',
                "metadata": {"tool_schema": {"required": ["city"]}},
            },
            {
                "id": "bad-call",
                "sequence": 1,
                "kind": "tool",
                "tool_name": "weather",
                "tool_arguments": "{bad",
                "metadata": {"tool_schema": {"required": ["city"]}},
            },
        ],
    )

    assert len(findings) == 1
    assert findings[0]["span_id"] == "bad-call"


@pytest.mark.asyncio
async def test_repeated_tool_call_requires_three_identical_calls(client):
    spans = [
        {
            "id": f"repeat-{index}",
            "sequence": index,
            "kind": "tool",
            "tool_name": "search",
            "tool_arguments": {"query": "北京天气", "limit": 3},
        }
        for index in range(2)
    ]
    _, findings = await ingest_spans(client, spans)
    assert findings == []

    spans.append(
        {
            "id": "repeat-2",
            "sequence": 2,
            "kind": "tool",
            "tool_name": "search",
            "tool_arguments": '{"limit":3,"query":"北京天气"}',
        }
    )
    _, findings = await ingest_spans(client, spans)

    assert len(findings) == 1
    assert findings[0]["rule_code"] == "repeated_tool_call"
    assert findings[0]["evidence"]["count"] == 3
    assert findings[0]["evidence"]["span_ids"] == ["repeat-0", "repeat-1", "repeat-2"]


@pytest.mark.asyncio
async def test_missing_citation_only_flags_claims_without_any_source(client):
    spans = [
        {
            "id": "answer",
            "sequence": 0,
            "kind": "llm",
            "content": "根据研究表明，该结论可靠。",
        }
    ]
    _, findings = await ingest_spans(client, spans, "根据研究表明，该结论可靠。")
    assert [finding["rule_code"] for finding in findings] == ["missing_citation"]
    assert findings[0]["span_id"] == "answer"

    spans.insert(
        0,
        {
            "id": "evidence",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": {"source": "https://example.test/research", "text": "研究原文"},
        },
    )
    _, findings = await ingest_spans(client, spans, "根据研究表明，该结论可靠。")
    assert findings == []


@pytest.mark.asyncio
async def test_unsupported_number_is_conservative_and_ignores_supported_values(client):
    spans = [
        {
            "id": "weather-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": {"temperature": 25, "text": "明天 25°C"},
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": "明天 25°C"},
    ]
    _, findings = await ingest_spans(client, spans, "明天 25°C")
    assert findings == []

    spans[1]["content"] = "明天 99°C"
    _, findings = await ingest_spans(client, spans, "明天 99°C")

    assert len(findings) == 1
    assert findings[0]["rule_code"] == "unsupported_number"
    assert findings[0]["severity"] in {"low", "medium"}
    assert findings[0]["evidence"]["numbers"] == ["99"]
    assert findings[0]["span_id"] == "answer"


@pytest.mark.asyncio
async def test_fact_conflict_requires_explicit_boolean_contradiction(client):
    spans = [
        {
            "id": "inventory-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": {"available": True},
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": "该门票不可用。"},
    ]
    _, findings = await ingest_spans(client, spans, "该门票不可用。")
    assert len(findings) == 1
    assert findings[0]["rule_code"] == "fact_conflict"
    assert findings[0]["evidence"]["field"] == "available"
    assert findings[0]["evidence"]["expected"] is True

    spans[1]["content"] = "该门票可用。"
    _, findings = await ingest_spans(client, spans, "该门票可用。")
    assert findings == []

    spans[0]["tool_result"] = {"available": False}
    _, findings = await ingest_spans(client, spans, "该门票可用。")
    assert [finding["rule_code"] for finding in findings] == ["fact_conflict"]


@pytest.mark.asyncio
async def test_fact_conflict_does_not_cross_wire_multiple_boolean_fields(client):
    spans = [
        {
            "id": "booking-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": {"available": True, "success": False},
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": "门票可用，但预订失败。"},
    ]

    _, findings = await ingest_spans(client, spans, "门票可用，但预订失败。")

    assert findings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_result", "answer"),
    [
        ({"success": True}, "操作失败。"),
        ({"success": False}, "操作成功。"),
        ({"status": "failed"}, "操作成功。"),
        ({"status": "error"}, "操作已成功。"),
        ({"status": "success"}, "操作失败。"),
        ({"status": "completed"}, "操作已失败。"),
        ({"status": "succeeded"}, "操作失败。"),
    ],
)
async def test_fact_conflict_matches_single_operation_outcome(client, tool_result, answer):
    spans = [
        {
            "id": "operation-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": tool_result,
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": answer},
    ]

    _, findings = await ingest_spans(client, spans, answer)

    assert [finding["rule_code"] for finding in findings] == ["fact_conflict"]
    assert findings[0]["evidence"]["expected"] == next(iter(tool_result.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_result", "answer"),
    [
        ({"success": True}, "操作未失败。"),
        ({"success": True}, "操作没有失败。"),
        ({"success": True}, "操作成功，但操作失败。"),
        ({"status": "success"}, "操作未失败。"),
        ({"status": "completed"}, "操作没有失败。"),
    ],
)
async def test_fact_conflict_ignores_negated_failure_and_mixed_claims(
    client, tool_result, answer
):
    spans = [
        {
            "id": "operation-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": tool_result,
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": answer},
    ]

    _, findings = await ingest_spans(client, spans, answer)

    assert findings == []


@pytest.mark.asyncio
async def test_fact_conflict_recognizes_explicit_status_values(client):
    spans = [
        {
            "id": "order-result",
            "sequence": 0,
            "kind": "tool_result",
            "tool_result": {"status": "cancelled"},
        },
        {"id": "answer", "sequence": 1, "kind": "llm", "content": "订单已完成。"},
    ]
    _, findings = await ingest_spans(client, spans, "订单已完成。")
    assert [finding["rule_code"] for finding in findings] == ["fact_conflict"]
    assert findings[0]["evidence"]["expected"] == "cancelled"

    spans[1]["content"] = "订单已取消。"
    _, findings = await ingest_spans(client, spans, "订单已取消。")
    assert findings == []


@pytest.mark.asyncio
async def test_reanalyze_replaces_only_rule_findings(client, tmp_path):
    await ingest_spans(
        client,
        [
            {
                "id": "broken",
                "sequence": 0,
                "kind": "tool",
                "status": "error",
                "error": "timeout",
            }
        ],
    )
    from app.db import make_engine, make_session_factory
    from app.models import Finding

    factory = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    with factory() as session:
        session.add(
            Finding(
                id="llm-review",
                trace_id="rules",
                source="llm",
                rule_code="custom_review",
                severity="low",
                category="复核",
                title="二阶复核",
                explanation="保留此结果",
            )
        )
        session.commit()

    first = await client.post("/api/traces/rules/reanalyze")
    second = await client.post("/api/traces/rules/reanalyze")
    assert first.status_code == second.status_code == 200
    findings = (await client.get("/api/traces/rules")).json()["findings"]
    assert len(findings) == 2
    assert {finding["source"] for finding in findings} == {"rule", "llm"}
    assert sum(finding["id"] == "llm-review" for finding in findings) == 1
