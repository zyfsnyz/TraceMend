# AgentTrace

AgentTrace 是一个中文、本地优先的 Agent 审计工作台：通过透明代理接入 OpenAI 兼容接口与 MCP，也接收任意 Agent 主动上报的 Trace，将一次任务中的提示词、模型输出、工具调用、工具返回、Token、耗时和错误串成可追溯链路，并提供风险证据、DAG、时间线回放、评论和报告导出。

**AgentTrace 不是大模型，也不替代 Agent 或工具。** 它是代理与审计层。默认即可独立使用免费的确定性规则检测；二阶 LLM 检测默认关闭，只有显式配置并调用对应接口时才可能请求外部检测模型。

## 功能

- **OpenAI 兼容代理**：`/v1/chat/completions` 非流式与 SSE 流式转发，保留上游状态码和响应，记录消息、工具调用、内容、Token、模型和异常。
- **MCP 代理**：转发 JSON-RPC，记录 `initialize`、`tools/list`、`tools/call` 及工具结果或错误。
- **框架无关接入**：自定义 Agent 可直接 `POST /api/traces/ingest`，无需安装 AgentTrace SDK。
- **免费规则检测**：工具异常、空工具结果、参数/schema 问题、同名同参数重复调用、声明来源但无来源证据、工具证据未支撑的数字、明确状态冲突。
- **审计工作台**：搜索和筛选、链路 DAG、时间线与单步回放、原始输入/输出、Finding 证据、评论与解决状态。
- **报告**：导出中文 Markdown 和 PDF 审计报告。
- **可选二阶 LLM 检测**：与免费规则分来源保存，不参与默认启动和自动分析。

风险 Finding 表示“需要复核的证据”，不代表系统断言内容一定错误；尤其是“未支撑数字”规则。

## 架构

```text
Agent / OpenAI SDK / LangChain / MCP Client
                    │
                    ▼
        FastAPI 透明代理与 REST API
          ├─ Trace / Span 归一化
          ├─ 免费规则（默认独立可用）
          ├─ 二阶 LLM 检测（默认关闭）
          └─ SQLAlchemy + SQLite 本地持久化
                    │
                    ▼
       React + TypeScript 中文审计工作台
       DAG / 时间线 / Finding / 评论 / 报告
```

生产部署中，Nginx 提供前端静态文件和 SPA fallback，并将 `/api/`、`/v1/`、`/mcp` 反向代理到后端；SSE 路径关闭代理缓冲。后端应用启动时自动建表，无需另行执行迁移命令。

## 快速开始

### Docker Compose

前置条件：Docker Engine 与 Docker Compose v2。

```bash
cp .env.example .env
# 按需编辑 .env；仅本地审计和免费规则不需要任何 API key
docker compose up --build
```

- 审计工作台：<http://127.0.0.1:3000>
- 后端 API：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/health>

Compose 默认仅将端口绑定到 `127.0.0.1`。如需对外提供服务，必须显式修改 `ports` 绑定，并务必同时设置强 `ADMIN_KEY`、在入口启用 TLS、在可信网关或网络层配置上游地址白名单及代理客户端访问控制。**应用尚未实现上游白名单**，不要将未鉴权代理直接暴露到公网。

前端 Nginx 通过 `client_max_body_size 20m` 将经前端代理的请求体上限设为 20 MiB，超限请求会由 Nginx 拒绝；直接访问后端端口不经过这一前端代理限制。

SQLite 文件持久化在宿主机 `./data/agenttrace.db`。前端等待后端健康后启动。停止服务：

```bash
docker compose down
```

`down` 不删除 `./data`。构建只复制后端应用、前端源码和必要构建配置，不复制 `.env`、`data/` 或 `node_modules/`。

### 本地开发

要求 Python 3.11+ 和 Node.js 20+。分别开两个终端：

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 3000
```

本地默认数据库为 `sqlite:///./data/agenttrace.db`，路径相对于后端工作目录；请先创建 `backend/data/`（或设置自己的 `DB_URL`）。也可通过环境变量加载根目录 `.env` 中的配置，不要把密钥写进源码。

### 写入演示数据

后端启动后执行，脚本只使用 Python 标准库：

```bash
python scripts/seed_demo.py
```

脚本通过真实 `POST /api/traces/ingest` 创建两条带 `demo` 标签的数据：

1. 天气与旅行工具链：用户问题、两轮 LLM 规划、天气和路线工具、结构化工具返回及最终回答。
2. 虚构的“普津汉大学”校史问答：三次相同参数检索、空返回、无来源引用及多个未支撑数字，用于观察风险证据。

演示数据通过 API 写入，不是前端硬编码。固定 UUID 使重复执行按 ingest 幂等规则 upsert。若设置了 `ADMIN_KEY`，脚本会读取同名环境变量。

## 配置

复制 `.env.example` 后按环境修改：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DEFAULT_UPSTREAM_BASE` | `https://api.openai.com/v1` | OpenAI 兼容代理的默认上游 |
| `DEFAULT_MCP_UPSTREAM` | 空 | 默认完整 MCP URL；也可逐请求覆盖 |
| `DB_URL` | `sqlite:///./data/agenttrace.db` | SQLAlchemy 数据库 URL；Compose 固定使用 `/data` |
| `ENABLE_LLM_CHECK` | `false` | 是否允许二阶 LLM 检测，默认关闭 |
| `CHECK_MODEL_BASE` | `https://api.openai.com/v1` | 可选检测模型的 OpenAI 兼容 base URL |
| `CHECK_MODEL_KEY` | 空 | 可选检测模型密钥 |
| `CHECK_MODEL_NAME` | `gpt-4o-mini` | 可选检测模型名称 |
| `ADMIN_KEY` | 空 | 设置后，除健康检查外的 `/api/*` 需要 `X-Admin-Key` |

`.env` 被 Git 忽略，`.env.example` 不含真实密钥。

## 接入

### OpenAI Python SDK：只改 `base_url`

将 SDK 的 `base_url` 指向 AgentTrace，并用 `X-Upstream-Base` 选择真实上游；业务代码、模型名和消息结构不变。API key 会先发送给所配置的 AgentTrace，再转发给上游，因此只应使用可信实例；连接远程实例必须使用 TLS（`https`）。

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://127.0.0.1:8000/v1",
    default_headers={"X-Upstream-Base": "https://api.openai.com/v1"},
)
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "你好"}],
)
print(response.choices[0].message.content)
```

可运行完整示例（会请求真实上游并产生相应费用）：

```bash
python -m pip install openai
read -rsp '真实上游 API key: ' OPENAI_API_KEY; export OPENAI_API_KEY; echo
python examples/openai_demo.py
```

也可通过请求头 `X-AgentTrace-Id` 指定 UUID，以串联调用；响应头同名返回 Trace ID。未设置 `X-Upstream-Base` 时使用 `DEFAULT_UPSTREAM_BASE`。

### LangChain：只改 `base_url`

```python
import os
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o-mini",
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="http://127.0.0.1:8000/v1",
    default_headers={"X-Upstream-Base": "https://api.openai.com/v1"},
)
print(llm.invoke("你好").content)
```

流式调用仍使用 LangChain 原有的 `.stream(...)`；AgentTrace 在转发 SSE 的同时记录链路。

### MCP：`X-MCP-Upstream`

把 MCP 客户端的 POST 地址改为 `http://127.0.0.1:8000/mcp`，添加完整上游 URL：

```http
X-MCP-Upstream: http://127.0.0.1:9000/mcp
```

标准库示例：

```bash
export MCP_UPSTREAM_URL='http://127.0.0.1:9000/mcp'
export MCP_TOOL_NAME='get_weather'
python examples/mcp_client.py
```

未提供请求头时使用 `DEFAULT_MCP_UPSTREAM`。`/mcp/{path}` 用于需要额外路径的 MCP 服务。

### 自定义 Agent：主动 ingest

上报一个 Trace 及其有序 spans 即可，字段详见 [`docs/api-contract.md`](docs/api-contract.md)：

```bash
curl -X POST http://127.0.0.1:8000/api/traces/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "trace": {
      "name": "我的 Agent",
      "source": "custom-agent",
      "status": "completed",
      "input": {"messages": [{"role": "user", "content": "查询天气"}]},
      "output": {"content": "明天 25°C"},
      "started_at": "2026-11-11T10:00:00Z",
      "ended_at": "2026-11-11T10:00:02Z",
      "tags": ["my-agent"]
    },
    "spans": [{
      "sequence": 0,
      "kind": "user",
      "name": "用户提问",
      "status": "completed",
      "content": "查询天气"
    }]
  }'
```

`user`、`llm`、`tool`、`tool_result` 等 span 使用 `parent_span_id` 组成 DAG；工具返回的 `parent_span_id` 指向对应工具调用。可附 `tool_schema`、`tool_arguments`、`tool_result`、Token 与 metadata。

## 数据隐私与安全边界

- Trace、prompt、模型输出、工具参数、工具结果、Finding 和评论默认保存在本地 SQLite；这些数据本身可能含敏感信息，请按日志级别保护 `./data` 和导出报告。
- 上游 `Authorization` 只用于当次代理转发，不作为审计内容写入数据库；代理 API key 与 `CHECK_MODEL_KEY` 不得提交到 Git。
- `X-Upstream-Base` 和 `X-MCP-Upstream` 能把数据发送到指定服务。只允许可信客户端访问代理，部署到共享环境时应增加网络访问控制、TLS 和上游地址白名单。
- `ADMIN_KEY` 保护管理 API，不是上游 API key，也不替代公网身份认证方案。
- 免费规则在本地运行，不需要外网。只有显式启用并调用二阶 LLM 检测时，检测所需内容才会发往配置的检测模型服务。
- `DELETE /api/traces/{id}` 删除本地审计记录；宿主机备份、已导出报告和上游服务商的数据需分别管理。

## API 一览

管理 API 在设置 `ADMIN_KEY` 后需要请求头 `X-Admin-Key`（健康检查除外）。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/health` | 服务、数据库状态与版本 |
| `GET /api/traces?q=&status=&risk=&tag=&from=&to=&page=&page_size=` | 分页、搜索和筛选 Trace |
| `GET /api/traces/{id}` | Trace、按 sequence 排序的 spans、findings、comments |
| `DELETE /api/traces/{id}` | 删除本地 Trace 及关联记录 |
| `POST /api/traces/ingest` | 框架无关 Trace/Span 批量接入，幂等 upsert 并运行规则 |
| `POST /api/traces/{id}/spans/{span_id}/comments` | 针对 Span 添加评论/标注 |
| `POST /api/traces/{id}/comments` | 针对 Trace 添加评论/标注 |
| `PATCH /api/comments/{id}` | 更新评论内容或 `open/resolved` 状态 |
| `POST /api/traces/{id}/reanalyze` | 重新运行免费规则 |
| `POST /api/traces/{id}/llm-check` | 显式执行可选二阶 LLM 检测；未启用/未配置返回 409 |
| `GET /api/traces/{id}/report.md` | 下载 Markdown 报告 |
| `GET /api/traces/{id}/report.pdf` | 下载 PDF 报告 |
| `GET /api/stats?days=30` | Trace、错误、风险、Token、耗时和模型统计 |
| `POST /v1/chat/completions` | OpenAI 兼容非流式/SSE 透明代理 |
| `POST /mcp`、`POST /mcp/{path}` | MCP JSON-RPC 透明代理 |

## 测试与真实全流程验收

后端和前端测试：

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
```

```bash
cd frontend
npm test -- --run
npm run typecheck
npm run build
```

检查部署/演示脚本语法：

```bash
python -m py_compile scripts/seed_demo.py scripts/e2e_smoke.py examples/openai_demo.py examples/mcp_client.py
```

后端已在 8000 端口运行时，执行真实端到端验收：

```bash
python scripts/e2e_smoke.py
```

该脚本会启动两个仅监听 `127.0.0.1` 随机端口的本地 HTTP 模拟上游，**不访问外网、不需要 key**，实际完成：

1. OpenAI 非流式代理及响应标记、`X-AgentTrace-Id` 检查。
2. OpenAI SSE 流式代理：上游发出首个含标记的 `data` chunk 后暂停，客户端必须在放行及上游响应结束前逐行收到它，再检查完整内容与 `[DONE]`。
3. MCP `tools/call` 代理及工具返回检查。
4. 轮询详情 API，断言 spans、内容和 usage 已落库，并检查 MCP 工具名、参数和结果。
5. 下载 Markdown/PDF，检查 Markdown 标题与 PDF `%PDF` 魔数，保存到 `artifacts/`。

默认保留三条验收 Trace 供界面查看；无论验收成功或失败都要尝试删除本次已创建的 Trace，请使用：

```bash
python scripts/e2e_smoke.py --cleanup
```

如启用管理鉴权，先设置 `ADMIN_KEY` 环境变量。脚本任一步失败都会以非零状态退出；`--cleanup` 会尝试清理全部本次 Trace，清理失败不会掩盖原始验收异常。

## 路线图

- 更多 Agent 框架的一键适配与 OpenTelemetry 导入。
- 可配置上游白名单、保留周期和审计数据脱敏策略。
- 更细的规则配置、误报反馈与团队权限。
- 更多报告模板和外部工单系统集成。

路线图是后续方向，不表示这些能力已经完成。

## License

MIT License

Copyright (c) 2026 AgentTrace contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the “Software”), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
