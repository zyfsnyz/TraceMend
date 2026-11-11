import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SpanInspector } from './SpanInspector';
import type { Span } from '../api/types';

const span = {
  id: 'span-1',
  trace_id: 'trace-1',
  parent_span_id: null,
  sequence: 0,
  kind: 'llm',
  name: '规划',
  status: 'completed',
  started_at: null,
  ended_at: null,
  duration_ms: 20,
  input: { messages: [{ role: 'user', content: '你好' }] },
  output: { content: '收到' },
  content: null,
  reasoning: null,
  tool_name: null,
  tool_arguments: null,
  tool_result: null,
  prompt_tokens: 1,
  completion_tokens: 1,
  total_tokens: 2,
  error: null,
  metadata: {},
  vendor_extension: '未单独映射的字段',
  OPENAI_API_KEY: 'raw-openai-key',
} as Span & { vendor_extension: string; OPENAI_API_KEY: string };

describe('SpanInspector 原始数据', () => {
  it('可展开完整 Span JSON，保留未映射字段并脱敏', async () => {
    const user = userEvent.setup();
    render(<SpanInspector span={span} />);

    const summary = screen.getByText('完整 Span 原始 JSON');
    const details = summary.closest('details');
    expect(details).not.toHaveAttribute('open');
    await user.click(summary);

    expect(details).toHaveAttribute('open');
    expect(details).toHaveTextContent('"vendor_extension": "未单独映射的字段"');
    expect(details).toHaveTextContent('"OPENAI_API_KEY": "[已隐藏]"');
    expect(details).not.toHaveTextContent('raw-openai-key');
  });
});
