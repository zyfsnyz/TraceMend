import type { SpanKind } from '../api/types';

export const kindLabels: Record<SpanKind, string> = {
  user: '用户', llm: '模型', tool: '工具', tool_result: '工具返回', mcp: 'MCP', system: '系统',
};
