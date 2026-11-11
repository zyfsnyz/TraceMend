# 测试与 RED / GREEN 记录

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
- `npm run dev -- --host 0.0.0.0 --port 3000`：首页与 `/guide` 实际返回 HTTP 200；浏览器检查桌面及窄屏渲染。
