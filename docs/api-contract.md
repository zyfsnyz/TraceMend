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

列表响应：`{"items":[trace],"total":0,"page":1,"page_size":20,"pages":0}`。统计响应包含 `total_traces/completed_traces/error_traces/risk_traces/total_findings/total_tokens/avg_duration_ms/tool_calls/models/daily`，以及 `total_cost_usd/today_tokens/daily_token_budget/active_routes/open_circuits`。Trace 可包含 `estimated_cost_usd`，网关详情位于 `trace.metadata.gateway`。

## 连接设置与网关

`GET /api/settings` 返回连接和网关配置，密钥字段仅为 `default_upstream_key_configured/default_mcp_key_configured/check_model_key_configured` 布尔值。`PUT /api/settings` 接受 `SettingsPatch`；未提交或为 null 的密钥保持不变，空字符串清除密钥；其他字段显式为 null 时返回 422，校验失败不会写入部分配置。OpenAI Base URL 不允许 query/fragment；MCP URL 不允许 fragment 或常见凭据查询参数，请改用专用密钥字段。公开读取（`GET /api/settings`、`GET /api/gateway/routes`）返回的 URL 一律移除 userinfo（`user:pass@`）与 fragment，敏感查询参数值替换为 `REDACTED`，非敏感查询参数（如 `tenant=cn`）原样保留；历史遗留配置同样适用，任何密码、密钥或片段都不会回显。`POST /api/settings/test` body 为 `{"kind":"openai|check|mcp"}`，使用已保存配置测试；响应为 `{ok,kind,message,model?}`，不返回密钥。

`POST /api/gateway/routes` 创建路由，字段为 `name/match_model/target_model/upstream_base/api_key/priority/enabled/input_price_per_million/output_price_per_million`。读取时只有 `api_key_configured`，没有 `api_key` 或 `key_name`。PATCH 支持相同字段的可选更新；普通字段显式 null 返回 422，`api_key: null` 保持不变、空字符串清除；DELETE 删除路由；`POST /api/gateway/routes/{id}/reset` 重置熔断。

路由按有效模型（请求 model 或默认模型）执行 `fnmatch(model, match_model)`，并按 `priority/id` 排序。408、425、429、5xx 和网络错误可切换下一路由，其他 4xx（400/401/403/404 等）不重试；SSE 仅在完整结束后确认成功。每日 Token 预算为按已落库用量检查的软预算，不做并发预留，超限返回 429。显式 `X-Upstream-Base` 绕过路由、默认模型补全、预算与熔断状态，且不会注入服务端保存的默认密钥。熔断快照为 `{status,failures,open_remaining_seconds}`。

`Authorization` 契约：路由配置了 `api_key` 时，转发请求使用该密钥并覆盖客户端 `Authorization`；路由未配置密钥时，原样透传客户端 `Authorization`。默认上游配置了 `DEFAULT_UPSTREAM_KEY` 时，仅当客户端未提供 `Authorization` 或使用保留占位 `api_key="agenttrace"` 才替换为服务端密钥，真实客户端密钥优先；占位替换只适用于默认上游，显式 `X-Upstream-Base` 直连始终按客户端原样透传。

## 评论

POST body：`{"span_id":null,"author":"测试同学","body":"这里需要复核","status":"open"}`。PATCH 可更新 `body/status`。

## 错误

统一 HTTP 状态码与 `{"detail":"中文可读错误"}`；404 不存在，409 LLM 检测未启用/未配置，422 输入无效，502 上游网络错误。代理自身的上游业务响应保持原状态码和原 body。
