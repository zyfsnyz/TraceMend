# AgentTrace Frontend

中文 Agent 审计溯源工作台。React 18、TypeScript、Vite、Tailwind CSS、React Router 与 `@xyflow/react`。

## 启动

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 3000
```

开发服务器将 `/api`、`/v1` 代理到 `http://127.0.0.1:8000`。生产构建使用同源 API，不包含演示链路或真实密钥。若后端设置了 `ADMIN_KEY`，页面收到 401 会自动打开“管理密钥设置”，密钥只能是 ASCII 字符，且只保存在当前标签页的 `sessionStorage`（存储被禁用时仅保存在内存），可随时清除。

## 验证

```bash
npm run lint
npx tsc --noEmit
npm test -- --run
npm run build
```

## 路由

- `/`：近 30 天指标、每日趋势、模型分布与最近链路。
- `/traces`：搜索、状态/风险/标签/日期筛选、分页、删除。
- `/traces/:traceId`：React Flow DAG、时间线、单步回放、Span 原始载荷、风险、评论、重检与报告导出。
- `/risks`：通过真实风险筛选展示风险链路。
- `/guide`：OpenAI SDK、LangChain、上游请求头、MCP 与自定义 Ingest 接入示例。

## 代码结构

```text
src/
  api/         API 类型、请求与错误处理
  components/  布局、DAG、回放、时间线、风险、评论与通用 UI
  lib/         格式化、DAG 布局、回放状态机、敏感字段处理
  pages/       总览、链路、详情、风险中心、接入指南
  test/        Testing Library 测试环境
```

测试使用 Vitest、Testing Library 与 happy-dom。锁定文件中的 Rollup 平台二进制为 optional dependency，可由 `npm ci` 在 Windows、Linux、Alpine 等环境选择对应平台。
