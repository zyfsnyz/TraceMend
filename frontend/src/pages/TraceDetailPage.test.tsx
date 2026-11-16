import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { TraceDetailPage } from './TraceDetailPage';
import { getTrace } from '../api/client';
import type { TraceDetail } from '../api/types';

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  downloadReport: vi.fn(),
  getTrace: vi.fn(),
  llmCheckTrace: vi.fn(),
  reanalyzeTrace: vi.fn(),
  replaySpan: vi.fn(),
}));
vi.mock('../components/TraceDAG', () => ({ TraceDAG: () => <div>执行 DAG</div> }));
vi.mock('../components/ReplayBar', () => ({ ReplayBar: () => <div>回放</div> }));
vi.mock('../components/ReplayStudio', () => ({
  ReplayStudio: ({ traceId, span }: { traceId: string; span: TraceDetail['spans'][number] | null }) => (
    <div>真实重放面板：{traceId}/{span?.id ?? '未选择'}</div>
  ),
}));
vi.mock('../components/SpanInspector', () => ({ SpanInspector: () => <div>Span 详情</div> }));
vi.mock('../components/TraceTimeline', () => ({ TraceTimeline: () => <div>时间线</div> }));
vi.mock('../components/FindingsPanel', () => ({ FindingsPanel: () => <div>风险发现</div> }));
vi.mock('../components/CommentsPanel', () => ({ CommentsPanel: () => <div>评论</div> }));

const detail: TraceDetail = {
  trace: {
    id: 'trace-1', name: '订单链路', source: 'test', status: 'completed', model: 'gpt-4o-mini',
    input: { nested: { apiToken: 'trace-api-token', unmapped_input: '输入未映射字段' } },
    output: { unmapped_output: '输出未映射字段', authorization: 'Bearer trace-bearer-token' },
    started_at: '2026-11-11T10:00:00Z', ended_at: '2026-11-11T10:00:01Z', duration_ms: 1000,
    prompt_tokens: 2, completion_tokens: 3, total_tokens: 5, tags: [], metadata: {}, error: null,
    risk_count: 0, span_count: 0,
  },
  spans: [], findings: [], comments: [],
};

describe('TraceDetailPage 原始输入输出', () => {
  it('可展开 Trace 原始输入/输出并展示未映射字段，敏感值不泄露', async () => {
    const user = userEvent.setup();
    vi.mocked(getTrace).mockResolvedValue(detail);
    render(<MemoryRouter initialEntries={['/traces/trace-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes><Route path="/traces/:traceId" element={<TraceDetailPage />} /></Routes></MemoryRouter>);

    const summaryText = await screen.findByText('Trace 原始输入/输出');
    const details = summaryText.closest('details');
    expect(details).not.toHaveAttribute('open');
    await user.click(summaryText.closest('summary')!);

    expect(details).toHaveAttribute('open');
    expect(details).toHaveTextContent('"unmapped_input": "输入未映射字段"');
    expect(details).toHaveTextContent('"unmapped_output": "输出未映射字段"');
    expect(details).toHaveTextContent('"apiToken": "[已隐藏]"');
    expect(details).not.toHaveTextContent('trace-api-token');
    expect(details).not.toHaveTextContent('trace-bearer-token');
  });
});

describe('TraceDetailPage 真实重放集成', () => {
  it('把当前 traceId 与自动选中的 Span 传给 ReplayStudio', async () => {
    const span: TraceDetail['spans'][number] = {
      id: 'span-1', trace_id: 'trace-1', parent_span_id: null, sequence: 0, kind: 'llm', name: '生成答案',
      status: 'completed', started_at: '2026-11-11T10:00:00Z', ended_at: '2026-11-11T10:00:01Z', duration_ms: 1000,
      input: { model: 'gpt-4o-mini', messages: [] }, output: { content: '答案' }, content: '答案', reasoning: null,
      tool_name: null, tool_arguments: null, tool_result: null, prompt_tokens: 2, completion_tokens: 3, total_tokens: 5,
      error: null, metadata: {},
    };
    vi.mocked(getTrace).mockResolvedValue({
      ...detail,
      trace: { ...detail.trace, span_count: 1 },
      spans: [span],
    });

    render(<MemoryRouter initialEntries={['/traces/trace-1']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes><Route path="/traces/:traceId" element={<TraceDetailPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText('真实重放面板：trace-1/span-1')).toBeInTheDocument();
  });
});
