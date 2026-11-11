import re

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
    assert b"/FontFile2" in response.content


@pytest.mark.asyncio
async def test_report_missing_trace_is_404(client):
    assert (await client.get("/api/traces/nope/report.md")).status_code == 404
    assert (await client.get("/api/traces/nope/report.pdf")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("report_format", ["md", "pdf"])
async def test_report_download_filename_is_ascii_for_non_ascii_trace_id(client, report_format):
    from copy import deepcopy
    from urllib.parse import quote

    tricky_id = '审计"报告\\行'
    malicious = deepcopy(payload)
    malicious["trace"]["id"] = tricky_id
    for span in malicious["spans"]:
        span["trace_id"] = tricky_id
        span["id"] = f"{tricky_id}-{span['id']}"
        if span.get("parent_span_id"):
            span["parent_span_id"] = f"{tricky_id}-{span['parent_span_id']}"
    assert (await client.post("/api/traces/ingest", json=malicious)).status_code == 200

    response = await client.get(f"/api/traces/{quote(tricky_id, safe='')}/report.{report_format}")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    encoded = disposition.encode("latin-1")
    assert re.fullmatch(
        r'attachment; filename="agenttrace-[A-Za-z0-9._-]+\.(?:md|pdf)"', disposition
    )
    assert encoded


@pytest.mark.asyncio
async def test_markdown_report_does_not_render_injected_links_or_images(client):
    from copy import deepcopy

    malicious = deepcopy(payload)
    attack = (
        "![追踪像素](https://evil.example/pixel.png) "
        "[点我](https://evil.example) <img src=https://evil.example/x>"
    )
    malicious["trace"]["name"] = attack
    malicious["spans"][0]["content"] = attack
    await client.post("/api/traces/ingest", json=malicious)

    response = await client.get("/api/traces/trace-report/report.md")
    body = response.text

    assert response.status_code == 200
    assert "![追踪像素](" not in body
    assert "](https://evil.example)" not in body
    assert "<img" not in body
    assert "追踪像素" in body


@pytest.mark.asyncio
async def test_markdown_report_escapes_comments_and_untrusted_span_ids(client):
    from copy import deepcopy

    attack = "![pixel](https://evil.example/p.png) <img src=x onerror=alert(1)>"
    malicious = deepcopy(payload)
    malicious["spans"].append(
        {
            "id": attack,
            "trace_id": "trace-report",
            "sequence": 2,
            "kind": "tool",
            "name": "注入 Span",
            "status": "completed",
        }
    )
    assert (await client.post("/api/traces/ingest", json=malicious)).status_code == 200
    comment = await client.post(
        "/api/traces/trace-report/comments",
        json={"author": "审计员", "body": attack, "span_id": attack},
    )
    assert comment.status_code == 201

    response = await client.get("/api/traces/trace-report/report.md")
    body = response.text

    assert response.status_code == 200
    assert "![pixel](" not in body
    assert "<img src=x" not in body
    assert "pixel" in body


@pytest.mark.asyncio
async def test_pdf_report_keeps_comment_text_plain_without_markdown_escape_artifacts(client):
    from io import BytesIO

    from pypdf import PdfReader

    await client.post("/api/traces/ingest", json=payload)
    comment = await client.post(
        "/api/traces/trace-report/comments",
        json={"author": "法务同学", "body": "第一行\n**重点**"},
    )
    assert comment.status_code == 201

    response = await client.get("/api/traces/trace-report/report.pdf")
    assert response.status_code == 200
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages
    )

    assert "第一行" in text
    assert "重点" in text
    assert "\\*\\*" not in text
    assert "<br>" not in text
