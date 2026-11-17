# TraceMend API 数据契约

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

`GET /api/settings` 返回连接和网关配置，密钥字段仅为 `default_upstream_key_configured/default_mcp_key_configured/check_model_key_configured` 布尔值。`PUT /api/settings` 接受 `SettingsPatch`；未提交或为 null 的密钥保持不变，空字符串清除密钥；其他字段显式为 null 时返回 422，校验失败不会写入部分配置。OpenAI Base URL 不允许 query/fragment；MCP URL 不允许 fragment 或常见凭据查询参数，请改用专用密钥字段。公开读取（`GET /api/settings`、`GET /api/gateway/routes`）返回的 URL 一律移除 userinfo（`user:pass@`）与 fragment；只有 `tenant/mode/region/project/namespace/api-version` 等明确用于展示的查询值原样保留，其余查询值默认替换为 `REDACTED`。历史遗留配置同样适用，任何密码、密钥或片段都不会回显。所有请求校验 422 响应仅保留错误位置、类型和消息，统一删除 Pydantic 的 `input` 与 `ctx`，避免回显请求体中的密钥。`POST /api/settings/test` body 为 `{"kind":"openai|check|mcp"}`，使用已保存配置测试；响应为 `{ok,kind,message,model?}`，不返回密钥。

`POST /api/gateway/routes` 创建路由，字段为 `name/match_model/target_model/upstream_base/api_key/priority/enabled/input_price_per_million/output_price_per_million`。读取时只有 `api_key_configured`，没有 `api_key` 或 `key_name`。PATCH 支持相同字段的可选更新；普通字段显式 null 返回 422，`api_key: null` 保持不变、空字符串清除；DELETE 删除路由；`POST /api/gateway/routes/{id}/reset` 重置熔断。

路由按有效模型（请求 model 或默认模型）执行 `fnmatch(model, match_model)`，并按 `priority/id` 排序。408、425、429、5xx 和网络错误可切换下一路由，其他 4xx（400/401/403/404 等）不重试；SSE 仅在完整结束后确认成功，下游取消或断开会记录未完成 Trace，但不计入上游失败熔断。每日 Token 预算按 UTC `[今日 00:00, 明日 00:00)` 内每次 LLM Span 的调用时间与 usage 统计，跨天复用 Trace ID 仍按当天调用计入，未来时间戳不计入今天；这是按已落库用量检查的软预算，不做并发预留，超限返回 429。显式 `X-Upstream-Base` 绕过路由、默认模型补全、预算与熔断状态，且不会注入服务端保存的默认密钥。熔断快照为 `{status,failures,open_remaining_seconds}`。

认证头契约：路由配置了 `api_key` 时，转发请求使用该密钥覆盖客户端 `Authorization`，并统一清除 `api-key`、`x-api-key`、Cookie、token/secret/signature/credential 等其他客户端凭据头。未配置密钥时只有首个候选路由可透传调用方凭据；重试到后续候选时一律清除这些凭据，备用路由必须配置独立 `api_key`，防止密钥被发送至另一个服务商。默认上游配置了 `DEFAULT_UPSTREAM_KEY` 时，仅当客户端未提供 `Authorization` 或使用保留占位 `api_key="agenttrace"` 才替换为服务端密钥，真实客户端密钥优先；占位替换只适用于默认上游，显式 `X-Upstream-Base` 直连始终按客户端原样透传。

## Replay（单步 LLM 重放）

`POST /api/traces/{trace_id}/spans/{span_id}/replay`

真实请求当前配置的上游模型，可能产生 Token 与费用；不是只读回放。只允许 `kind=llm` 的 Span，且仅重放该 Span 的一次 `chat/completions` 调用。

### 请求

| 头/字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `Idempotency-Key` | UUID 字符串 | 是 | 缺失或格式非法返回 422；规范化后作为重放 Trace 的 `id` |
| `confirm` | 布尔 | 是 | 必须为 JSON 字面量 `true`；缺失、`false`、`1` 或 `"true"` 均返回 422 |
| `overrides` | 对象 | 否 | 省略表示沿用历史请求；显式 `null` 返回 422 |
| `overrides.model` | 字符串 | 否 | 1–120 字符；参与当前路由匹配 |
| `overrides.temperature` | 数值 | 否 | 0–2 |
| `overrides.top_p` | 数值 | 否 | 0–1 |
| `overrides.seed` | 整数 | 否 | ≥ 0 |
| `overrides.max_tokens` | 整数 | 否 | ≥ 1 |

请求体顶层只接受 `confirm` 与 `overrides`；`overrides` 内只接受上述五个字段。未知字段、显式 `null` 覆盖值或类型/范围错误均返回 422，且 422 不包含 Pydantic 的 `input/ctx`，不会回显请求体中的敏感值。`overrides` 只覆盖这五个采样/模型参数，不能指定历史 URL、请求头、凭据、`messages`、`tools` 或 `stream`。

历史请求的恢复规则：

- 优先使用所选 LLM Span 自身的 `input`（必须含 `messages` 列表）。
- 若 Span 没有独立输入，仅当原 Trace 中恰好只有一个 LLM Span 时，才从 `trace.input` 无歧义回退；多个 LLM Span、缺失输入或 `messages` 不是列表均返回 409。
- 重建后的历史请求若含 `authorization`、`api_key`、`token`、`secret` 等凭据类**顶层字段**，返回 409，禁止把历史凭据发往当前上游。字段识别覆盖 `APIToken` 等缩写驼峰形式，以及 `APITOKEN`、`ACCESSTOKEN`、`AUTHTOKEN`、`SESSIONKEY` 等全大写紧凑形式；OpenAI 官方非凭据参数 `prompt_cache_key` 明确允许重放。

重放不会复用历史请求中的 URL 或客户端 `Authorization`；它按覆盖后的 `model`（未覆盖时按 Span/Trace 记录的有效模型）匹配**当前**启用的路由，并使用当前路由配置的密钥；没有匹配路由时使用当前默认上游配置。重放也不会修改原 Trace。

历史请求若为 `stream: true`，后端向上游发起 SSE，收集并归一化完整输出后写入同一个 LLM Span；重放接口对调用方始终返回 JSON，不做流式转发。历史请求为普通 JSON 时则使用非流式上游请求；`stream` 不能通过 `overrides` 切换。

### 响应（201）

```json
{
  "replay_trace": { "id": "幂等键 UUID", "parent_id": "原 trace_id", "source": "replay", "status": "completed" },
  "replay_span": { "kind": "llm", "status": "completed" },
  "source_span": { "id": "原 LLM span_id", "kind": "llm" },
  "comparison": {
    "status": "same",
    "content_equal": true,
    "reasoning_equal": true,
    "tool_calls_equal": true,
    "content_similarity": 1.0,
    "content_diff": "",
    "attribution_note": "单次一致或差异均不能单独证明模型随机性或幻觉成因；请结合多次实验与事实评估。"
  }
}
```

`replay_trace`、`replay_span`、`source_span` 使用与 `GET /api/traces/{id}` 相同的 Trace/Span 序列化字段。重放 Trace 的 `id` 等于规范化后的 `Idempotency-Key`，`parent_id` 指向原 Trace，`source` 为 `replay`，`metadata.replay` 记录 `source_trace_id/source_span_id/request_source/overrides/mode=llm_span/idempotency_key/request_fingerprint/comparison`。重放 Trace 只包含一个 LLM Span；不创建历史消息 Span，不创建或执行 tool Span。

`comparison.status`：

- `same`：`content_equal`、`reasoning_equal`、`tool_calls_equal` 均为 true。
- `different`：上游成功返回，但三项中至少一项不同。
- `failed`：本次上游尝试失败；此时无论三项比较结果如何都标记 `failed`。

`content_similarity` 为 0–1 的内容相似度；`content_diff` 是“原始输出 → 重放输出”的 unified diff，内容一致时为空字符串。字段名以本响应为准（即 `content_similarity`/`content_diff`，不是 `similarity`/`diff`）。`attribution_note` 固定提示单次结果不能单独归因。工具调用只参与比较，重放绝不会执行模型返回的 `tool_calls`，也不会自动驱动后续 Span 或恢复完整链路。

### 幂等

- 相同 `Idempotency-Key` + 相同原 Trace/Span + 相同 `confirm/overrides`：若服务端预占记录已有终态（`completed` 或 `failed`），直接返回创建时保存的完整响应快照，不再请求上游，也不重新读取可被改写的 Trace；即使原 Trace 后来被删除，缓存重试仍然有效。
- 幂等状态由服务端独占的 `replay_reservations` 表维护，Ingest 接口不能写入该表；返回快照保存在该表中，后续 Ingest 对重放 Trace 的改写不会改变同键重试结果。
- 相同键但请求体、原 Trace 或原 Span 改变：409。
- 相同键的重放仍在 `running`：409。
- 首次响应快照形成前，重放 Trace 处于服务端保护窗口：使用该 Trace ID 的 Ingest、DELETE、reanalyze、llm-check，以及携带同一 `X-AgentTrace-Id` 的 OpenAI/MCP 代理写入均返回 409；代理写入在上游请求前拦截。SQLite 下保护检查与写入使用 `BEGIN IMMEDIATE` 串行化，预留创建在同一写锁语义下完成，消除“检查后再创建预留”的 TOCTOU 竞争。OpenAI/MCP、llm-check 与 Replay 的 async 路径通过工作线程等待数据库锁并执行持久化，避免慢写者冻结事件循环；llm-check 不在外部模型调用期间持有写锁，写回前重新加锁检查。进入终态后，本地 Trace 的后续改写仍不会影响预占表中已保存的响应快照。
- 相同键已被任意非预留 Trace 占用（包括客户端完全照抄字段伪造的 `source=replay` 记录）：409，且不会调用上游。服务端不再把可写的 Trace 元数据当作重放真实性证明。

### 重试、预算与熔断

重放复用轻量网关的当前路由与重试策略：按 `priority/created_at/id` 顺序尝试候选，网络错误或 408/425/429/5xx 可切换下一候选；其他 4xx 立即停止。若 SSE 已开始返回字节，则不会透明切换到另一候选，失败仍按该次重放记录，并按失败计入当前候选路由的熔断。

每日 Token 预算按 UTC 当日已落库用量做软预算检查，超限返回 429；不做并发预留。初始检查发现所有匹配候选路由均处于熔断冷却期时返回 503，不会进入上游调用。

### 状态码

| 状态码 | 场景 |
|---|---|
| 201 | 重放完成、上游尝试失败、或命中相同键的已保存终态结果 |
| 404 | 原 Trace 不存在；Span 不存在或不属于该 Trace（跨 Trace 同样 404） |
| 409 | Span 不是 `llm`；历史输入缺失/有歧义/含凭据；幂等键被占用、请求体变化或仍在执行 |
| 422 | `Idempotency-Key` 缺失/非 UUID；`confirm` 非字面量 true；未知字段；`overrides` 非法 |
| 429 | 每日 Token 预算已用尽 |
| 503 | 所有匹配候选路由均处于熔断冷却期 |

**上游失败不是 502：** 一旦已进入重放执行，网络错误、上游 4xx/5xx、SSE HTTP 错误或流未正常结束，接口仍返回 201；`replay_trace.status` 与 `replay_span.status` 为 `failed`，`comparison.status` 为 `failed`，错误写入 `replay_trace.error/replay_span.error`，尝试与状态码记录在 `replay_trace.metadata.gateway.attempts`（失败 Span 的 `metadata.upstream_status` 也会保留）。相同幂等键重试会返回同一份失败审计结果，而不是自动再次请求上游。

### 脱敏与安全边界

- 重放接口返回的是审计原文；前端的输出、推理和 diff 展示会隐藏常见密钥与 `Bearer`/`sk-` 字符串，Markdown/PDF 报告导出也会脱敏。原始数据库和 API 仍应按敏感日志保护。
- 重放拒绝历史请求顶层的凭据类字段，但不会回溯清洗已经落库的任意文本；不要把密钥写入 prompt、工具参数或输出。
- 重放只使用当前保存的网关路由/密钥，不接受调用方指定上游 URL，也不会恢复原客户端凭据。
- 工具调用只比较、不执行；重放不会产生消息、写库、工单或其他工具副作用。

### curl 示例

下面的 UUID 与 `ADMIN_KEY` 均为占位值，请替换为实际 Trace/Span ID；启用管理鉴权时从环境变量读取密钥，不要把真实密钥写进命令历史或文档。

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/traces/11111111-1111-4111-8111-111111111111/spans/22222222-2222-4222-8222-222222222222/replay" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 33333333-3333-4333-8333-333333333333" \
  -H "X-Admin-Key: ${ADMIN_KEY}" \
  -d '{
    "confirm": true,
    "overrides": {
      "temperature": 0.2,
      "top_p": 0.9,
      "seed": 42,
      "max_tokens": 256
    }
  }'
```

未启用 `ADMIN_KEY` 时可省略 `X-Admin-Key` 头。重复执行同一命令会命中同一幂等键；若需要新的实验，请更换 `Idempotency-Key`。

## 评论

POST body：`{"span_id":null,"author":"测试同学","body":"这里需要复核","status":"open"}`。PATCH 可更新 `body/status`。

## 错误

统一 HTTP 状态码与 `{"detail":"中文可读错误"}`；404 不存在，409 冲突（如 LLM 检测未启用/未配置、Replay 输入或幂等冲突），422 输入无效，502 上游网络错误。代理自身的上游业务响应保持原状态码和原 body。Replay 单步重放是明确例外：进入执行后的上游失败返回 201 并保存 failed 审计记录，详见上文。
