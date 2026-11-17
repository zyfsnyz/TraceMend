# TraceMend Implementation Plan

> **For Hermes:** Use subagent-driven-development and test-driven-development. Execute and verify every task; do not substitute mock claims for real execution.

**Goal:** 交付一个轻量、中文、本地优先的 Agent 审计平台，能够透明代理 OpenAI 兼容接口与 MCP，保存完整链路，检测风险，展示 DAG/回放，并导出可读审计报告。

**Architecture:** FastAPI 负责透明代理、Trace 归一化、规则/可选 LLM 检测与 REST API；SQLAlchemy + SQLite 本地持久化。React + TypeScript + React Flow 提供中文审计工作台。后端测试使用真实解析器与本地模拟上游，前端测试覆盖状态与交互，最终用浏览器和隔离 Hermes 实例做端到端验证。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, httpx, Pydantic, pytest, ReportLab；React 18, TypeScript, Vite, @xyflow/react, TailwindCSS, Vitest；Docker Compose。

---

## 契约与目录

项目根：`E:/工作夹/AgentTrace`。后端代码只在 `backend/`，前端只在 `frontend/`，文档/部署在根目录。密钥只放 `.env`，`.env` 不入库；日志、报告不得输出 Authorization 或检测模型密钥。

核心 REST：
- `GET /api/health`：服务/数据库状态与版本。
- `GET /api/traces?q=&status=&risk=&tag=&from=&to=&page=&page_size=`：分页列表，含 span/token/risk 汇总。
- `GET /api/traces/{id}`：Trace + 按 sequence 排序的 spans + findings + comments。
- `DELETE /api/traces/{id}`：删除本地审计记录。
- `POST /api/traces/ingest`：框架无关 Trace/Span 批量接入，幂等 upsert，完成后运行规则检测。
- `POST /api/traces/{id}/spans/{span_id}/comments` 与 `POST /api/traces/{id}/comments`：标注/评论。
- `PATCH /api/comments/{id}`：修改标注状态/内容。
- `POST /api/traces/{id}/reanalyze`：重新运行免费规则。
- `POST /api/traces/{id}/llm-check`：仅显式触发且服务已配置时执行；禁用/未配置返回明确 409，不泄露密钥。
- `GET /api/traces/{id}/report.md` / `report.pdf`：下载中文报告。
- `GET /api/stats?days=30`：Trace、错误、风险、Token、耗时、模型分布。

代理：
- `POST /v1/chat/completions`：透明转发除 Host/Content-Length 等 hop-by-hop 外的请求头，支持非流式和 SSE 流式，上游状态码/响应体原样返回；以 `X-AgentTrace-Id` 返回 Trace ID，允许请求头指定 ID；`X-Upstream-Base` 覆盖默认上游。必须捕获 messages、模型、工具定义、assistant content、tool_calls、usage、错误、耗时；流式连接中断也保留失败 Trace。不得将上游 API key 写入数据库。
- `POST /mcp` 与 `POST /mcp/{path}`：`X-MCP-Upstream` 指定完整 MCP URL（或默认配置），透明转发 JSON-RPC；initialize/tools/list 记录协议 Span，tools/call 记录 tool 参数和 result/error，支持同名 `X-AgentTrace-Id` 串联。
- API 管理鉴权可选：设置 `ADMIN_KEY` 后 `/api/*` 要求 `X-Admin-Key`，健康检查除外；代理 Authorization 始终属于上游。

数据：Trace(id, parent_id, name, source, status, model, input, output, started_at, ended_at, duration_ms, prompt_tokens, completion_tokens, total_tokens, tags JSON, metadata JSON, error, created_at)；Span(id, trace_id, parent_span_id, sequence, kind, name, status, started_at, ended_at, duration_ms, input/output JSON, content, reasoning, tool_name, tool_arguments JSON, tool_result JSON, token usage, error, metadata JSON)；Finding(id, trace_id, span_id, source, rule_code, severity, category, title, explanation, evidence JSON, created_at)；Comment(id, trace_id, span_id nullable, author, body, status open/resolved, created_at, updated_at)。SQLite 外键与级联删除开启。

风险规则（保守、证据可追溯、中文解释）：工具异常；空工具结果；基于 tools schema 的必填参数/JSON 参数错误；同名同参数调用达到 3 次；声明引用/来源但链路无 URL/来源字段；最终答案含工具证据中找不到的数字时标记“未支撑数字”（不是断言一定幻觉）；最终文本与工具明确布尔值/状态字段冲突时标记事实冲突。二阶 LLM 结果与规则结果分 source 保存，重新分析只替换同 source 旧结果。

## Task 1 — 项目与测试基础设施
创建 `.gitignore`、`backend/requirements.txt`、`backend/requirements-dev.txt`、`backend/pyproject.toml`、前端 Vite 配置和测试配置。先写配置/健康检查失败测试，再实现最小应用工厂与数据库初始化。初始化 git；本地身份仅用于本仓库。

## Task 2 — 持久化与归一化（TDD）
依次以失败测试驱动：Trace/Span/Finding/Comment 模型与级联；ingest 验证/幂等；列表筛选分页；详情排序；统计。测试必须用临时 SQLite，不能碰开发数据库。

## Task 3 — 免费风险检测（TDD）
逐条 RED→GREEN 实现工具错误、空结果、参数 schema、重复调用、引用缺失、未支撑数字、明确状态冲突。覆盖无证据时不误报、重复检测不重复插入、Finding 证据定位 span。

## Task 4 — OpenAI 与 MCP 代理（TDD + 本地上游）
用本地模拟上游验证非流式、SSE 多 chunk tool_calls、上游 4xx/5xx、网络失败、动态上游、header 不泄露。验证 MCP initialize/list/call/error。必须实际启动 HTTP 服务验证流式字节不被缓冲修改，而非只测辅助函数。

## Task 5 — 评论、报告与可选 LLM 检测（TDD）
实现评论/解决状态、Markdown 和有效 PDF（中文、风险证据、时间线、Token、免责声明）；LLM 检测默认关闭，配置缺失 409，使用结构化 JSON 输出并对模型异常落审计状态但不改原响应。密钥不得进日志。

## Task 6 — 中文审计工作台
实现总览指标、Trace 搜索/状态/风险筛选、详情 DAG（节点状态/类型/耗时）、时间线与单步播放/暂停/进度、原始 prompt/tool 参数/返回查看、Finding 证据高亮、评论与解决、MD/PDF 下载、接入指南。API 失败/空态/加载态完整；使用语义色与可访问按钮，不伪造数据。Vitest 测试工具函数和关键交互，TypeScript 与生产构建必须通过。

## Task 7 — 交付与演示
提供 Dockerfiles、Compose、`.env.example`、启动脚本、中文 README、OpenAI/LangChain/MCP/任意 Agent ingest 示例。`scripts/seed_demo.py` 通过真实 ingest API 创建一条含工具链与风险的演示 Trace，不在 UI 硬编码演示数据。

## Task 8 — 验收
运行完整 pytest、Ruff、Vitest、tsc、前端构建；独立 spec + quality 审查并修复。启动真实服务，用浏览器完成列表→筛选→DAG→单步回放→评论→重新检测→MD/PDF 下载并保存截图/报告。以隔离 Hermes profile 经代理执行一次真实请求，确认 Trace 的流式内容/Token/模型落库；恢复/保留原默认配置不受影响。最后才请求用户二阶检测模型的 base_url、model、API key。
