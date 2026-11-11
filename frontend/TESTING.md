# 测试与 RED / GREEN 记录

> 当前最新汇总见 [`../docs/verification.md`](../docs/verification.md)。本文保留各轮修复记录，文中的测试数量是当时的历史值。

核心函数与关键交互均先创建测试并实际运行，确认因实现文件不存在而失败，再实现并运行至通过。

| 测试 | RED 实际结果 | GREEN 实际结果 |
| --- | --- | --- |
| `src/lib/format.test.ts` | `Failed to resolve import "./format"` | 3 tests passed |
| `src/lib/dag.test.ts` | `Failed to resolve import "./dag"` | 3 tests passed |
| `src/lib/replay.test.ts` | `Failed to resolve import "./replay"` | 4 tests passed |
| `src/api/client.test.ts` | `Failed to resolve import "./client"` | 3 tests passed |
| `src/lib/sensitive.test.ts` | `Failed to resolve import "./sensitive"` | 2 tests passed |
| `src/components/ReplayBar.test.tsx` | `Failed to resolve import "./ReplayBar"` | 1 test passed；下一步/上一步同步当前 Span |

覆盖：数字/耗时/时间格式化、父子 DAG 与 sequence 回退连线、悬空父级、自连接、回放边界/播放/暂停/进度、API detail 与网络错误、筛选序列化、递归敏感字段隐藏、关键回放组件交互。

## 最终实测

在 Windows 11 / Node.js 25.9.0 下执行：

- `npm ci`：成功，按锁文件安装 324 个包。
- `npm test -- --run`：6 个测试文件、16 项测试全部通过。
- `npm run lint`：通过，无错误与警告。
- `npx tsc --noEmit`：通过。
- `npm run build`：通过，Vite 完成 1626 个模块转换。
- `npm run dev -- --host 127.0.0.1 --port 3000`：首页与 `/guide` 实际返回 HTTP 200；开发服务器默认只监听回环地址；浏览器检查桌面及窄屏渲染。

## 真实 API 契约与原始数据修复（2026-11-11）

以下修复均先在原实现上运行新测试并确认 RED，再做最小修改至 GREEN；没有改动旧测试来迁就错误实现。

| 测试 | 旧实现上的 RED 证据 | 修复后的 GREEN 证据 |
| --- | --- | --- |
| `src/pages/OverviewPage.test.tsx` | 找不到 `gpt-4o-mini`，React 报告柱状图 `y/height` 为 `NaN` | 真实 `models: Record<string, number>` 与 `daily[].count` 渲染通过，SVG 无 `NaN/Infinity` |
| `src/pages/RiskFilters.test.tsx` | 风险中心实际请求 `risk=true`；链路列表没有 `none` 选项 | 3 项通过：默认/URL 切换使用 `any`，无风险请求使用 `none` |
| `src/pages/GuidePage.test.tsx` | 复制文本实际为 `http://localhost:3000/v1/mcp` | 展示与复制文本均为 `${origin}/mcp` |
| `src/lib/sensitive.test.ts` | `OPENAI_API_KEY`、`apiToken`、`token`、`userPassword` 原值可见，嵌入的 Bearer 原值可见 | 4 项通过；常见键变体、嵌套对象/数组、Bearer 与 `sk-...` 均脱敏，普通中文保持不变 |
| `src/components/SpanInspector.test.tsx` | 找不到“完整 Span 原始 JSON” | 可展开完整 JSON，未映射字段可见且密钥脱敏 |
| `src/pages/TraceDetailPage.test.tsx` | 找不到“Trace 原始输入/输出” | 可展开原始输入/输出，未映射字段可见且敏感值脱敏 |

API client 原有实现已经与评论 POST（Trace/Span）、PATCH `resolved`、409 错误传播和报告 URL 契约一致，因此这 5 项属于新增保护测试，首次运行即为 GREEN，没有伪造 RED 或修改生产代码。`CommentsPanel` 的真实 `userEvent` 提交测试首次运行暴露 Happy DOM 的 `localStorage` 不可用；在测试内补齐 Storage stub 后通过，同样无需修改生产代码。

### 本轮最终实测

- `npm test -- --run`：12 个测试文件、31 项测试全部通过。
- `npm run lint`：通过，无错误与警告。
- `npx tsc --noEmit`：通过。
- `npm run build`：通过，Vite 完成 1626 个模块转换。
