import { describe, expect, it } from 'vitest';
import type { Span, SpanKind } from '../api/types';
import { layoutSpans } from './dag';

export function makeSpan(id: string, sequence: number, parent_span_id: string | null = null, kind: SpanKind = 'llm'): Span {
  return {
    id, trace_id: 'trace-1', parent_span_id, sequence, kind, name: `步骤 ${id}`,
    status: 'completed', started_at: '2026-11-11T10:00:00Z', ended_at: null,
    duration_ms: 120, input: null, output: null, content: null, reasoning: null,
    tool_name: null, tool_arguments: null, tool_result: null,
    prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, error: null, metadata: {},
  };
}

describe('DAG 布局与连线', () => {
  it('按有效的 parent_span_id 连线并逐层布局', () => {
    const spans = [makeSpan('tool', 2, 'llm', 'tool'), makeSpan('root', 0, null, 'user'), makeSpan('llm', 1, 'root')];
    const result = layoutSpans(spans);
    expect(result.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ['root', 'llm'], ['llm', 'tool'],
    ]);
    expect(result.nodes.find((node) => node.id === 'root')?.position.x).toBe(0);
    expect(result.nodes.find((node) => node.id === 'tool')?.position.x).toBeGreaterThan(0);
  });

  it('完全没有父子关系时按 sequence 回退为时序连线', () => {
    const result = layoutSpans([makeSpan('b', 2), makeSpan('a', 1)]);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toMatchObject({ source: 'a', target: 'b' });
    expect(result.nodes[0].id).toBe('a');
  });

  it('忽略悬空父级与自连接，不产生伪造边', () => {
    const result = layoutSpans([makeSpan('a', 0, 'missing'), makeSpan('b', 1, 'b')]);
    expect(result.edges).toEqual([]);
    expect(result.nodes).toHaveLength(2);
  });
});
