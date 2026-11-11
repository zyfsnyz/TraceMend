import pytest

payload = {
    "trace": {
        "id": "trace-comment",
        "name": "客服工单审计",
        "source": "pytest",
        "status": "completed",
        "input": {"messages": [{"role": "user", "content": "查订单"}]},
        "output": {"content": "订单已查询"},
    },
    "spans": [
        {
            "id": "span-comment",
            "trace_id": "trace-comment",
            "sequence": 0,
            "kind": "tool",
            "name": "查询订单",
            "tool_name": "query_order",
            "tool_arguments": {"order_id": "A-1"},
            "tool_result": {"status": "completed"},
        }
    ],
}


async def seed(client):
    response = await client.post("/api/traces/ingest", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_comment_on_trace_and_span_then_resolve(client):
    await seed(client)

    trace_comment = await client.post(
        "/api/traces/trace-comment/comments",
        json={"span_id": None, "author": "测试同学", "body": "复核整体链路", "status": "open"},
    )
    assert trace_comment.status_code == 201
    trace_body = trace_comment.json()
    assert trace_body["trace_id"] == "trace-comment"
    assert trace_body["span_id"] is None
    assert trace_body["status"] == "open"

    span_comment = await client.post(
        "/api/traces/trace-comment/spans/span-comment/comments",
        json={"author": "产品同学", "body": "工具返回是否符合预期？"},
    )
    assert span_comment.status_code == 201
    span_body = span_comment.json()
    assert span_body["span_id"] == "span-comment"
    assert span_body["status"] == "open"

    patched = await client.patch(
        f"/api/comments/{trace_body['id']}",
        json={"status": "resolved", "body": "已核实，无事实冲突", "author": "复核人"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "resolved"
    assert patched.json()["body"] == "已核实，无事实冲突"
    assert patched.json()["author"] == "复核人"

    detail = await client.get("/api/traces/trace-comment")
    assert detail.status_code == 200
    comments = detail.json()["comments"]
    assert len(comments) == 2
    assert {item["id"] for item in comments} == {trace_body["id"], span_body["id"]}


@pytest.mark.asyncio
async def test_comment_validation_and_ownership(client):
    await seed(client)
    await client.post(
        "/api/traces/ingest",
        json={
            "trace": {"id": "another-trace", "name": "另一条链路"},
            "spans": [
                {
                    "id": "another-span",
                    "trace_id": "another-trace",
                    "kind": "llm",
                    "name": "回答",
                    "content": "完成",
                }
            ],
        },
    )

    assert (
        await client.post("/api/traces/missing/comments", json={"author": "甲", "body": "评论"})
    ).status_code == 404
    assert (
        await client.post(
            "/api/traces/trace-comment/spans/missing/comments",
            json={"author": "甲", "body": "评论"},
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/traces/trace-comment/spans/another-span/comments",
            json={"author": "甲", "body": "评论"},
        )
    ).status_code == 404
    assert (
        await client.post(
            "/api/traces/trace-comment/comments", json={"author": " ", "body": "评论"}
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/traces/trace-comment/comments",
            json={"author": "甲", "body": "评论", "status": "done"},
        )
    ).status_code == 422
    missing_patch = await client.patch("/api/comments/missing", json={"status": "open"})
    assert missing_patch.status_code == 404
