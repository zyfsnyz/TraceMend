# AgentTrace API 数据契约

所有时间使用 ISO 8601 UTC；ID 为字符串 UUID；JSON 字段可包含字符串、数字、对象、数组或 null。

## Ingest

`POST /api/traces/ingest`

```json
{
  "trace": {
    "id": "optional-uuid",
    "name": "差旅助手",
    "source": "custom-agent",
    "status": "completed",
    "model": "demo-model",
    "input": {"messages": [{"role": "user", "content": "查询天气"}]},
    "output": {"content": "明天 25°C"},
    "started_at": "2026-11-11T10:00:00Z",
    "ended_at": "2026-11-11T10:00:02Z",
    "tags": ["demo"],
    "metadata": {}
  },
  "spans": [{
    "id": "optional-uuid",
    "parent_span_id": null,
    "sequence": 0,
    "kind": "user",
    "name": "用户提问",
    "status": "completed",
    "input": {"content": "查询天气"},
    "content": "查询天气"
  }]
}
```

Span kind：`user | llm | tool | tool_result | system | mcp`。工具调用使用 `tool_name`、`tool_arguments`；工具返回使用 `tool_result`，并通过 `parent_span_id` 指向调用 Span。可在 metadata 中提供 `tool_schema`（JSON Schema）和 `upstream_status`。

响应：`{"trace_id":"...","span_count":3,"finding_count":1}`。

## Detail

`GET /api/traces/{id}` 返回 `{trace, spans, findings, comments}`。`trace` 额外包含 `risk_count`、`span_count`。Finding 含 `id/span_id/source/rule_code/severity/category/title/explanation/evidence/created_at`；Comment 含 `id/span_id/author/body/status/created_at/updated_at`。

## List / Stats

列表响应：`{"items":[trace],"total":0,"page":1,"page_size":20,"pages":0}`。统计响应包含 `total_traces/completed_traces/error_traces/risk_traces/total_findings/total_tokens/avg_duration_ms/tool_calls/models/daily`。

## 评论

POST body：`{"span_id":null,"author":"测试同学","body":"这里需要复核","status":"open"}`。PATCH 可更新 `body/status`。

## 错误

统一 HTTP 状态码与 `{"detail":"中文可读错误"}`；404 不存在，409 LLM 检测未启用/未配置，422 输入无效，502 上游网络错误。代理自身的上游业务响应保持原状态码和原 body。
