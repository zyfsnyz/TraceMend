import pytest

payload = {
    "trace": {
        "id": "trace-report",
        "name": "差旅助手合规复盘",
        "source": "pytest",
        "status": "error",
        "model": "test-model",
        "input": {
            "messages": [{"role": "user", "content": "帮我查明天天气"}],
            "api_key": "sk-report-secret-should-never-appear",
        },
        "output": {"content": "明天晴，25 度。"},
        "total_tokens": 120,
    },
    "spans": [
        {
            "id": "span-report-tool",
            "trace_id": "trace-report",
            "sequence": 0,
            "kind": "tool",
            "name": "天气查询",
            "status": "error",
            "tool_name": "get_weather",
            "tool_arguments": {"city": "重庆"},
            "tool_result": {"error": "upstream timeout"},
            "error": "上游超时",
        },
        {
            "id": "span-report-llm",
            "trace_id": "trace-report",
            "parent_span_id": "span-report-tool",
            "sequence": 1,
            "kind": "llm",
            "name": "生成回答",
            "content": "明天晴，25 度。",
            "total_tokens": 120,
        },
    ],
}


@pytest.mark.asyncio
async def test_markdown_report_is_readable_and_redacted(client):
    seeded = await client.post("/api/traces/ingest", json=payload)
    assert seeded.status_code == 200
    assert seeded.json()["finding_count"] >= 1
    await client.post(
        "/api/traces/trace-report/comments",
        json={"author": "法务同学", "body": "请补充工具超时证据"},
    )

    response = await client.get("/api/traces/trace-report/report.md")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert "# AgentTrace 审计报告" in body
    assert "差旅助手合规复盘" in body
    assert "执行摘要" in body
    assert "工具调用异常" in body
    assert "法务同学" in body
    assert "请补充工具超时证据" in body
    assert "证据与局限" in body
    assert "不构成事实或法律结论" in body
    assert "get_weather" in body
    assert "sk-report-secret-should-never-appear" not in body


@pytest.mark.asyncio
async def test_pdf_report_is_real_pdf(client):
    await client.post("/api/traces/ingest", json=payload)
    response = await client.get("/api/traces/trace-report/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1000


@pytest.mark.asyncio
async def test_report_missing_trace_is_404(client):
    assert (await client.get("/api/traces/nope/report.md")).status_code == 404
    assert (await client.get("/api/traces/nope/report.pdf")).status_code == 404
