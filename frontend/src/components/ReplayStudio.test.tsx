import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { replaySpan } from '../api/client';
import type { Span, Trace } from '../api/types';
import { ReplayStudio } from './ReplayStudio';

vi.mock('../api/client', () => ({ replaySpan: vi.fn() }));

const llmSpan: Span = {
  id: 'span-1',
  trace_id: 'trace-1',
  parent_span_id: null,
  sequence: 0,
  kind: 'llm',
  name: '生成答案',
  status: 'completed',
  started_at: '2026-11-11T10:00:00Z',
  ended_at: '2026-11-11T10:00:01Z',
  duration_ms: 1000,
  input: { model: 'original-model', messages: [{ role: 'user', content: '问题' }] },
  output: { content: '原始答案' },
  content: '原始答案',
  reasoning: '原始推理',
  tool_name: null,
  tool_arguments: null,
  tool_result: null,
  prompt_tokens: 10,
  completion_tokens: 5,
  total_tokens: 15,
  error: null,
  metadata: {},
};

const replayTrace: Trace & { parent_id: string | null } = {
  id: 'replay-trace-1',
  parent_id: 'trace-1',
  name: '生成答案 · 重放',
  source: 'replay',
  status: 'completed',
  model: 'replay-model',
  input: llmSpan.input,
  output: { content: '重放答案' },
  started_at: '2026-11-11T10:01:00Z',
  ended_at: '2026-11-11T10:01:01Z',
  duration_ms: 1000,
  prompt_tokens: 11,
  completion_tokens: 6,
  total_tokens: 17,
  estimated_cost_usd: 0.001,
  tags: [],
  metadata: { replay: { source_trace_id: 'trace-1', source_span_id: 'span-1', mode: 'llm_span' } },
  error: null,
  risk_count: 0,
  span_count: 1,
};

const completedResult = {
  replay_trace: replayTrace,
  replay_span: { ...llmSpan, id: 'replay-span-1', trace_id: replayTrace.id, content: '重放答案', reasoning: '重放推理' },
  source_span: llmSpan,
  comparison: {
    status: 'different' as const,
    content_equal: false,
    reasoning_equal: false,
    tool_calls_equal: true,
    content_similarity: 0.42,
    content_diff: ['--- 原始输出', '+++ 重放输出', '@@ -1 +1 @@', '-原始答案', '+重放答案'].join('\n'),
    attribution_note: '单次一致或差异均不能单独证明模型随机性或幻觉成因；请结合多次实验与事实评估。',
  },
};

function renderStudio(span: Span | null = llmSpan) {
  return render(<MemoryRouter><ReplayStudio traceId="trace-1" span={span} /></MemoryRouter>);
}

async function confirmReplay(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: /确认.*可能产生费用/ }));
}

describe('ReplayStudio 真实重放交互', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(replaySpan).mockResolvedValue(completedResult);
  });

  it('非 LLM Span 只展示空状态，不展示真实重放表单', () => {
    renderStudio({ ...llmSpan, kind: 'tool', name: '搜索工具' });

    expect(screen.getByText('真实重放仅支持 LLM Span')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始真实重放' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('model')).not.toBeInTheDocument();
    expect(replaySpan).not.toHaveBeenCalled();
  });

  it('明确区分零 Token 只读播放与可能付费的真实重放，并说明工具和链路边界', () => {
    renderStudio();

    expect(screen.getByText(/只读播放器.*0 Token/)).toBeInTheDocument();
    expect(screen.getByText(/真实重放会请求上游模型.*可能产生费用/)).toBeInTheDocument();
    expect(screen.getByText(/不(会)?执行工具/)).toBeInTheDocument();
    expect(screen.getByText(/不(会)?恢复完整链路/)).toBeInTheDocument();
  });

  it('必须确认费用风险后才能提交，未编辑时发送空 overrides', async () => {
    const user = userEvent.setup();
    renderStudio();
    const submit = screen.getByRole('button', { name: '开始真实重放' });

    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(replaySpan).not.toHaveBeenCalled();

    await confirmReplay(user);
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() => expect(replaySpan).toHaveBeenCalledWith('trace-1', 'span-1', {
      confirm: true,
      overrides: {},
    }));
  });

  it('只发送用户编辑的 model、temperature、top_p、seed、max_tokens 数字或文本覆盖值', async () => {
    const user = userEvent.setup();
    renderStudio();

    await user.type(screen.getByLabelText('model'), 'replay-model');
    await user.type(screen.getByLabelText('temperature'), '0.2');
    await user.type(screen.getByLabelText('top_p'), '0.9');
    await user.type(screen.getByLabelText('seed'), '42');
    await user.type(screen.getByLabelText('max_tokens'), '512');
    await confirmReplay(user);
    const submit = screen.getByRole('button', { name: '开始真实重放' });
    const form = submit.closest('form');
    const invalidFields = Array.from(form?.querySelectorAll('input') ?? [])
      .filter((input) => !input.checkValidity())
      .map((input) => ({ label: input.labels?.[0]?.textContent, value: input.value }));
    expect(invalidFields).toEqual([]);
    await user.click(submit);

    await waitFor(() => expect(replaySpan).toHaveBeenCalledWith('trace-1', 'span-1', {
      confirm: true,
      overrides: {
        model: 'replay-model',
        temperature: 0.2,
        top_p: 0.9,
        seed: 42,
        max_tokens: 512,
      },
    }));
  });

  it('完成后展示原始与重放输出、相等性、相似度、diff、归因提示及新 Trace 链接', async () => {
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByRole('heading', { name: '原始输出' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '重放输出' })).toBeInTheDocument();
    expect(screen.getByText('原始答案')).toBeInTheDocument();
    expect(screen.getByText('重放答案')).toBeInTheDocument();
    expect(screen.getByText('内容不同')).toBeInTheDocument();
    expect(screen.getByText('推理不同')).toBeInTheDocument();
    expect(screen.getByText('工具调用一致')).toBeInTheDocument();
    expect(screen.getByText('内容相似度：42%')).toBeInTheDocument();
    const diff = screen.getByText(/@@ -1 \+1 @@/);
    expect(diff).toHaveTextContent('--- 原始输出');
    expect(diff).toHaveTextContent('+++ 重放输出');
    expect(diff).toHaveTextContent('-原始答案');
    expect(diff).toHaveTextContent('+重放答案');
    expect(screen.getByText(completedResult.comparison.attribution_note)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看重放 Trace' })).toHaveAttribute('href', '/traces/replay-trace-1');
  });

  it('API 拒绝时展示后端错误且不渲染伪造的重放结果', async () => {
    vi.mocked(replaySpan).mockRejectedValue(new Error('历史请求无法安全重建'));
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('历史请求无法安全重建');
    expect(screen.queryByRole('link', { name: '查看重放 Trace' })).not.toBeInTheDocument();
    expect(replaySpan).toHaveBeenCalledTimes(1);
  });
});
