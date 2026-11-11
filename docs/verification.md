# AgentTrace 验收记录

本文件记录仓库当前的本地实测结果，便于区分“已运行验证”和“仅完成实现”。

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
3. 通过 AgentTrace 代理发出一次真实的 `hermes chat` 请求。
4. 请求成功，AgentTrace 中生成一条 `completed` 的 Trace：3 个 Span、16,145 tokens；包含 system、user 和模型响应三条记录。
5. 同一轮集成测试也抓到了上游额度耗尽导致的 429 失败重试链路，证明失败请求会被如实记录。

二阶 LLM 幻觉检测依赖额外的检测模型凭据，本次未使用真实检测模型；未配置时的 409 行为有自动化测试覆盖。

### 未执行项

- 本机没有安装 Docker，未实际执行容器构建和 `docker compose up`。已验证 Compose YAML 可解析、服务端口映射为 `127.0.0.1`，但容器运行时仍需在安装 Docker 的环境复验。
- 未用真实二阶检测模型跑完整幻觉比对；需用户提供兼容的模型地址、模型名和密钥后验证。
