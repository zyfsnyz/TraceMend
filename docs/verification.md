# TraceMend 验收记录

本文件记录仓库当前的本地实测结果，便于区分“已运行验证”和“仅完成实现”。历史日期段落保留当时快照；当前门禁以下方“当前自动化门禁”为准。

## 当前自动化门禁（2026-11-17）

本轮在 Windows 11 本机重新执行，覆盖 Replay Studio 后端接口、前端回放交互与接入指南变更：

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端单元/接口测试 | `D:/python/python.exe -m pytest -q` | **354 项通过** |
| 后端静态检查 | `D:/python/python.exe -m ruff check .` | `All checks passed!` |
| 前端单元/交互测试 | `npm run test -- --run` | **20 个测试文件、84 项测试通过** |
| 前端 ESLint | `npm run lint` | 通过 |
| 前端生产构建 | `npm run build` | 通过，1630 个模块完成转换 |

说明：

- 后端 354 项包含 Replay API 的幂等、状态码、预算/熔断、失败审计与 SSE 归一化等回归测试。
- 最终安全复核新增 RED → GREEN 回归：running 重放 Trace 的同 ID Ingest、DELETE、OpenAI/MCP 代理写入、reanalyze 与 llm-check 均返回 409；真实 paused-guard 竞态证明 `BEGIN IMMEDIATE` 让 Ingest 与预留创建只能有一个赢家。新增 6 项外部写锁心跳测试，覆盖 OpenAI、MCP、llm-check、reanalyze、DELETE 和 Replay reservation；`run_db()` 将 async 路由中的同步数据库锁等待放入工作线程，慢写者不再冻结事件循环，成功/失败快照语义保持不变。`APIToken` 与 `APITOKEN/ACCESSTOKEN/AUTHTOKEN/SESSIONKEY` 均被识别为凭据；官方非凭据参数 `prompt_cache_key` 可正常发往当前上游。终态后同键重试仍返回服务器保存的原始快照。
- `npm run build` 实际执行 `tsc -b && vite build`，因此同时覆盖 TypeScript 构建检查。
- 浏览器端到端验收已执行：真实 Chromium 中打开链路详情，通过时间线选中 LLM Span，修改 `temperature=0.8` 并确认付费后完成单 Span 重放；页面显示“对比状态：不同”、原始/重放输出与脱敏 Diff，结果态锁定参数和提交，只能通过“新实验”恢复。审计 API 确认仅生成 1 个 LLM Span、无工具执行、`parent_id` 正确、18 Token 计入预算。上游使用本地确定性 OpenAI-compatible mock，不调用付费外部模型；幂等伪造/篡改与 SSE 内联错误由自动化测试覆盖。

## 2026-11-11 Windows 11 本机验收

### 自动化测试

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端单元/接口测试 | `D:/python/python.exe -m pytest -q` | 116 项通过 |
| 后端静态检查 | `D:/python/python.exe -m ruff check app tests` | 通过 |
| 前端单元/交互测试 | `npm test -- --run` | 49 项通过 |
| 前端类型检查 | `npx tsc --noEmit` | 通过 |
| 前端 Lint | `npm run lint` | 通过 |
| 前端生产构建 | `npm run build` | 通过，1628 个模块完成转换 |

### 真实端到端脚本

执行：

```bash
python scripts/e2e_smoke.py --cleanup
```

实际通过 OpenAI 非流式代理、增量 SSE 流式代理、MCP `tools/call`、SQLite 落库、Markdown/PDF 下载和 Trace 清理。脚本使用仅监听 `127.0.0.1` 的本地模拟上游，不访问外网，不读取真实密钥。

### 浏览器操作

在 Chromium 中实际完成：

- 总览页指标、趋势和最近链路显示。
- 链路列表搜索“校史”，结果从 2 条缩小到 1 条。
- 风险中心默认展示有风险链路，空状态和筛选状态可切换。
- 详情页 DAG 渲染；点击 DAG 节点会同步单步回放进度。
- 添加 Trace 评论并标记为已解决。
- 下载 Markdown 与 PDF 报告。
- 接入指南显示当前同源代理地址与 OpenAI/MCP 示例。
- 启用真实 `ADMIN_KEY` 后首次访问自动弹出“管理密钥设置”；输入正确密钥后仪表盘、详情、评论和报告下载恢复正常，密钥只保存在该标签页的 `sessionStorage`。

### 独立审查修复

独立代码审查发现并在本轮修复：

- `/v1` 透明代理不再把调用方的 `X-Admin-Key` 转发给上游模型服务，并增加回归测试。
- 启用 `ADMIN_KEY` 后前端可验证、保存、清除管理密钥，所有 API 与报告下载自动附带请求头，避免面板整体 401。
- 报告下载文件名改为纯 ASCII 并带摘要，恶意或多字节 Trace ID 不再触发 `latin-1` 编码 500；前端遇到非法百分号编码的文件名时回退到安全文件名。
- Markdown 报告转义 Trace 名称、Span 内容、评论正文、评论作者和不可信 `span_id` 中的链接、图片与原始 HTML，避免打开报告时加载远程追踪像素。
- PDF 评论区使用纯文本格式而不是 Markdown 转义格式，避免出现字面反斜杠、`**` 和 `<br>`。
- 管理密钥只允许 ASCII 字符：后端在启动时拒绝非 ASCII 配置，前端在提交前直接提示，非 ASCII 请求头稳定返回 401 而不是 500。
- 前端敏感字段展示补充 `admin_key` / `X-Admin-Key` 脱敏。

### PDF 中文字体

下载的 PDF 通过 `pdftotext -enc UTF-8` 能正确提取中文标题和正文；文件内已注册并嵌入 Noto Sans SC 字体子集。视觉排版仍建议在目标 PDF 阅读器中人工抽查。

### Hermes 实机接入

使用隔离 profile，未修改默认 profile：

1. 克隆现有凭据配置。
2. 将克隆 profile 的 OpenAI 兼容 `base_url` 指向 `http://127.0.0.1:8000/v1`。
3. 通过 TraceMend 代理发出一次真实的 `hermes chat` 请求。
4. 请求成功，TraceMend 中生成一条 `completed` 的 Trace：3 个 Span、16,145 tokens；包含 system、user 和模型响应三条记录。
5. 同一轮集成测试也抓到了上游额度耗尽导致的 429 失败重试链路，证明失败请求会被如实记录。

### 二阶 LLM 真实模型验收（2026-11-12）

使用现有 **Agnes** 检测服务，配置为 `CHECK_MODEL_BASE=https://apihub.agnes-ai.com/v1`、`CHECK_MODEL_NAME=agnes-3.0-flash`；密钥只写入被 Git 忽略的本地 `.env`，未打印或提交。

1. 受控矛盾 Trace `llm-live-agnes-001`：工具明确返回“仓库缺货，订单未发货”，最终回答却声称已发货并编造物流单号。`POST /api/traces/{id}/llm-check` 返回 HTTP 200，`llm_finding_count=1`。
2. Agnes 生成一条高风险 Finding：`事实冲突 / 工具返回失败但回答声称成功`，正确定位到 `llm-live-answer` Span，并引用工具返回与最终回答作为证据。
3. 真实 Hermes Trace `8563ae08-2309-4e1e-9040-369581172ff5` 同样完成检测，HTTP 200、`llm_check.status=completed`；模型按保守策略返回 0 条 Finding。
4. 浏览器详情页已实际显示 `llm` 来源、严重度、标题、解释和证据；Markdown 与 PDF 报告均包含该 LLM Finding，PDF 通过 `pdftotext` 复核。
5. 检测响应与 Trace 详情中未出现 `Authorization` 或 `CHECK_MODEL_KEY`。本地证据保存在被 Git 忽略的 `artifacts/llm-live-verification.json`、`artifacts/llm-live-check.png` 和对应报告中。

### 网页配置与轻量网关验收（2026-11-12）

- 后端 `pytest`：**132 项通过**；`ruff check .` 通过。
- 前端：**18 个测试文件、53 项测试通过**；ESLint、TypeScript 类型检查通过；生产构建通过，1629 个模块完成转换。
- 浏览器实际保存默认模型 `agnes-3.0-flash`，刷新后配置仍存在；创建“浏览器验收路由”，完成停用、重新启用和删除，验收路由已清理。
- 使用已保存的 Agnes 检测模型配置执行真实“保存并测试连接”，页面返回“模型连接成功：agnes-3.0-flash”；未读取或输出密钥。
- 总览显示估算成本与设置入口；接入指南显示“先配置连接”、简化 SDK 示例和同源 `/mcp` 地址。1440px 视口无横向溢出。
- 新增回归验证：默认上游与 MCP 密钥不会发送给显式自定义上游；显式上游绕过每日预算；路由 SSE 原字节透传并记录路由与 Token；保存配置不会回显密钥；Compose 持久配置覆盖初始配置。
- 修改后的 Compose YAML 已重新解析通过；`netstat` 确认当前 3000/8000 服务均只监听 `127.0.0.1`。
- 再次执行 `scripts/e2e_smoke.py --cleanup`，非流式、SSE、MCP、SQLite 落库、Markdown/PDF 和清理全部通过。

### 最终安全复核与数据清洗（2026-11-15）

- 针对独立审查发现的问题增加 RED → GREEN 回归：422 不再通过 `detail.input/ctx` 回显密钥；公开 URL 默认遮蔽未明确允许的查询值；跨上游重试和路由密钥注入会统一清理 Authorization、api-key、x-api-key、Cookie 及其他客户端凭据头。
- 响应头前取消会在受保护的清理作用域内关闭上游响应与客户端；下游取消 SSE 记录未完成 Trace，但不误触健康上游熔断。
- 每日 Token 预算和 `today_tokens` 改按 UTC `[今日 00:00, 明日 00:00)` 内每次 LLM Span 的调用时间统计，覆盖跨天复用 Trace ID 和未来时间戳边界。
- 接入示例使用 `your-configured-model` 占位，curl 可省略 `model`；路由编辑可显式清除密钥，价格小数输入不会阻止表单提交。
- 后端实测 **213 项通过**，`ruff check .` 通过；前端 **18 个文件、56 项通过**，ESLint、TypeScript 和生产构建通过，1629 个模块完成转换；`git diff --check` 通过。
- 已删除本机数据库中的 7 条演示/验收 Trace 及其关联数据，复核 `traces/spans/findings/comments/gateway_routes` 均为 0，SQLite `integrity_check=ok`。真实 `.env`、数据库、报告和缓存均由 Git 忽略，不纳入提交。

### 未执行项

- 本机没有安装 Docker，未实际执行容器构建和 `docker compose up`。已验证 Compose YAML 可解析、服务端口映射为 `127.0.0.1`，但容器运行时仍需在安装 Docker 的环境复验。
- Replay Studio 的浏览器端到端操作在该历史节点尚未执行；随后已使用本地确定性 mock 完成 ReplayBar 零 Token 播放、付费确认、单步重放、输出对比与结果态锁定，见上方“当前自动化门禁”。
