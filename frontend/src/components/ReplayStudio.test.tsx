import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { replaySpan } from '../api/client';
import type { Span, Trace } from '../api/types';
import { ReplayStudio } from './ReplayStudio';

vi.mock('../api/client', () => ({ replaySpan: vi.fn() }));

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

const switchedSpan: Span = {
  ...llmSpan,
  id: 'span-2',
  trace_id: 'trace-2',
  content: '第二条原始答案',
  output: { content: '第二条原始答案' },
};

const switchedResult: typeof completedResult = {
  replay_trace: {
    ...replayTrace,
    id: 'replay-trace-2',
    parent_id: 'trace-2',
    input: switchedSpan.input,
    output: { content: '新实验答案' },
    metadata: { replay: { source_trace_id: 'trace-2', source_span_id: 'span-2', mode: 'llm_span' } },
  },
  replay_span: {
    ...switchedSpan,
    id: 'replay-span-2',
    trace_id: 'replay-trace-2',
    content: '新实验答案',
    output: { content: '新实验答案' },
    reasoning: '新实验推理',
  },
  source_span: switchedSpan,
  comparison: {
    ...completedResult.comparison,
    content_similarity: 0.88,
    content_diff: ['--- 原始输出', '+++ 重放输出', '@@ -1 +1 @@', '-第二条原始答案', '+新实验答案'].join('\n'),
  },
};

function renderStudio(span: Span | null = llmSpan) {
  return render(<MemoryRouter><ReplayStudio traceId="trace-1" span={span} /></MemoryRouter>);
}

async function confirmReplay(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: /确认.*可能产生费用/ }));
}

async function ensureReplayConfirmed(user: ReturnType<typeof userEvent.setup>) {
  const confirmation = screen.getByRole('checkbox', { name: /确认.*可能产生费用/ });
  if (!confirmation.matches(':checked')) await user.click(confirmation);
  expect(confirmation).toBeChecked();
}

describe('ReplayStudio 真实重放交互', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(replaySpan).mockReset();
    vi.mocked(replaySpan).mockResolvedValue(completedResult);
  });

  it('非 LLM Span 只展示空状态，不展示真实重放表单', () => {
    renderStudio({ ...llmSpan, kind: 'tool', name: '搜索工具' });

    expect(screen.getByText('真实重放仅支持 LLM Span')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始真实重放' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('model')).not.toBeInTheDocument();
    expect(replaySpan).not.toHaveBeenCalled();
  });

  it('未选择 Span 时给出明确选择指引，不渲染真实重放表单或提交入口', () => {
    const { container } = renderStudio(null);

    expect(screen.getByText(/请选择.*LLM Span.*真实重放/)).toBeInTheDocument();
    expect(container.querySelector('form')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始真实重放' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /确认.*可能产生费用/ })).not.toBeInTheDocument();
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

  it('当前 Span 名称中的 Bearer 与 sk 凭证会脱敏', () => {
    renderStudio({ ...llmSpan, name: '生成 Bearer studio-name-secret 与 sk-studio-name-123456' });

    expect(screen.getByText('当前 Span：生成 Bearer [已隐藏] 与 [已隐藏]')).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('studio-name-secret');
    expect(document.body).not.toHaveTextContent('sk-studio-name-123456');
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
    }, expect.any(String)));
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
    }, expect.any(String)));
  });

  it('只编辑部分参数时仅发送这些字段，不夹带原始请求的未编辑默认值', async () => {
    const user = userEvent.setup();
    renderStudio({
      ...llmSpan,
      input: {
        model: 'original-model',
        messages: [{ role: 'user', content: '问题' }],
        temperature: 0.8,
        top_p: 1,
        seed: 99,
        max_tokens: 256,
      },
    });

    await user.type(screen.getByLabelText('temperature'), '0.25');
    await user.type(screen.getByLabelText('max_tokens'), '128');
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledWith('trace-1', 'span-1', {
      confirm: true,
      overrides: { temperature: 0.25, max_tokens: 128 },
    }, expect.any(String)));
    const payload = vi.mocked(replaySpan).mock.calls[0]?.[2];
    expect(payload?.overrides).toEqual({ temperature: 0.25, max_tokens: 128 });
    expect(payload?.overrides).not.toHaveProperty('model');
    expect(payload?.overrides).not.toHaveProperty('top_p');
    expect(payload?.overrides).not.toHaveProperty('seed');
  });

  it('请求待完成时禁止重复提交并显示重放进度', async () => {
    let resolveReplay!: (result: typeof completedResult) => void;
    vi.mocked(replaySpan).mockImplementationOnce(() => new Promise<typeof completedResult>((resolve) => {
      resolveReplay = resolve;
    }));
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    const submit = screen.getByRole('button', { name: '开始真实重放' });

    await user.click(submit);

    expect(replaySpan).toHaveBeenCalledTimes(1);
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toEqual(expect.any(String));
    expect(firstKey).toMatch(UUID_PATTERN);
    const pendingSubmit = screen.getByRole('button', { name: /重放中/ });
    expect(pendingSubmit).toBeDisabled();

    await user.click(pendingSubmit);

    expect(replaySpan).toHaveBeenCalledTimes(1);
    expect(pendingSubmit).toBeDisabled();
    expect(pendingSubmit).toHaveTextContent(/重放中/);

    resolveReplay(completedResult);
    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();
  });

  it('成功后通过新实验再次提交，并为新实验生成不同的有效 UUID', async () => {
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();
    expect(replaySpan).toHaveBeenCalledTimes(1);
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toMatch(UUID_PATTERN);

    await user.click(screen.getByRole('button', { name: '新实验' }));
    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();
    await ensureReplayConfirmed(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(secondKey).toEqual(expect.any(String));
    expect(secondKey).toMatch(UUID_PATTERN);
    expect(secondKey).not.toBe(firstKey);
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-1', 'span-1', {
      confirm: true,
      overrides: {},
    }, secondKey);
    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();
  });

  it('API 错误后的重试复用完全相同的 UUID 幂等键', async () => {
    vi.mocked(replaySpan)
      .mockRejectedValueOnce(new Error('上游服务暂时不可用'))
      .mockResolvedValueOnce(completedResult);
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);

    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('上游服务暂时不可用');
    expect(replaySpan).toHaveBeenCalledTimes(1);
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toEqual(expect.any(String));
    expect(firstKey).toMatch(UUID_PATTERN);

    const retry = screen.getByRole('button', { name: '开始真实重放' });
    expect(retry).toBeEnabled();
    await user.click(retry);

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const retryKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(retryKey).toEqual(expect.any(String));
    expect(retryKey).toMatch(UUID_PATTERN);
    expect(retryKey).toBe(firstKey);
    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();
  });

  it('结果出现后锁定参数与提交，必须新实验才能再次编辑', async () => {
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();

    expect(screen.getByLabelText('model')).toBeDisabled();
    expect(screen.getByLabelText('temperature')).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /确认.*可能产生费用/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '开始真实重放' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '新实验' }));

    expect(screen.getByLabelText('model')).toBeEnabled();
    expect(screen.getByLabelText('temperature')).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: /确认.*可能产生费用/ })).not.toBeChecked();
    expect(screen.getByRole('button', { name: '开始真实重放' })).toBeDisabled();
  });

  it('API 错误后点击新实验会清除错误并使用新的 UUID', async () => {
    vi.mocked(replaySpan)
      .mockRejectedValueOnce(new Error('上游服务暂时不可用'))
      .mockResolvedValueOnce(completedResult);
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('上游服务暂时不可用');
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toMatch(UUID_PATTERN);

    await user.click(screen.getByRole('button', { name: '新实验' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await ensureReplayConfirmed(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(secondKey).toMatch(UUID_PATTERN);
    expect(secondKey).not.toBe(firstKey);
  });

  it('API 错误后修改 override 会作为新请求使用新 UUID', async () => {
    vi.mocked(replaySpan)
      .mockRejectedValueOnce(new Error('历史请求无法安全重建'))
      .mockResolvedValueOnce(completedResult);
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('历史请求无法安全重建');
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toMatch(UUID_PATTERN);

    await user.type(screen.getByLabelText('temperature'), '0.7');
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(secondKey).toMatch(UUID_PATTERN);
    expect(secondKey).not.toBe(firstKey);
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-1', 'span-1', {
      confirm: true,
      overrides: { temperature: 0.7 },
    }, secondKey);
  });

  it('切换 traceId/spanId 后清除旧结果，下一次提交使用新 ID 和新的 UUID', async () => {
    const user = userEvent.setup();
    const { rerender } = renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByRole('heading', { name: '重放输出' })).toBeInTheDocument();
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toMatch(UUID_PATTERN);

    const nextSpan = { ...llmSpan, id: 'span-2', trace_id: 'trace-2', content: '第二条原始答案' };
    rerender(<MemoryRouter><ReplayStudio traceId="trace-2" span={nextSpan} /></MemoryRouter>);

    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();
    expect(screen.queryByText('重放答案')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看重放 Trace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await ensureReplayConfirmed(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(secondKey).toEqual(expect.any(String));
    expect(secondKey).toMatch(UUID_PATTERN);
    expect(secondKey).not.toBe(firstKey);
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-2', 'span-2', {
      confirm: true,
      overrides: {},
    }, secondKey);
  });

  it('切换 traceId/spanId 后清除旧 API 错误，下一次提交不复用旧幂等键', async () => {
    vi.mocked(replaySpan)
      .mockRejectedValueOnce(new Error('第一次重放失败'))
      .mockResolvedValueOnce(completedResult);
    const user = userEvent.setup();
    const { rerender } = renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('第一次重放失败');
    const firstKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(firstKey).toMatch(UUID_PATTERN);

    const nextSpan = { ...llmSpan, id: 'span-2', trace_id: 'trace-2' };
    rerender(<MemoryRouter><ReplayStudio traceId="trace-2" span={nextSpan} /></MemoryRouter>);

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('第一次重放失败')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();

    await ensureReplayConfirmed(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(secondKey).toEqual(expect.any(String));
    expect(secondKey).toMatch(UUID_PATTERN);
    expect(secondKey).not.toBe(firstKey);
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-2', 'span-2', {
      confirm: true,
      overrides: {},
    }, secondKey);
  });

  it('首个请求 pending 时切换 Span，旧请求 late resolve 不覆盖新结果', async () => {
    let resolveOld!: (result: typeof completedResult) => void;
    const oldRequest = new Promise<typeof completedResult>((resolve) => {
      resolveOld = resolve;
    });
    vi.mocked(replaySpan)
      .mockImplementationOnce(() => oldRequest)
      .mockResolvedValueOnce(switchedResult);
    const user = userEvent.setup();
    const { rerender } = renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(replaySpan).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /重放中/ })).toBeDisabled();
    const oldKey = vi.mocked(replaySpan).mock.calls[0]?.[3];
    expect(oldKey).toMatch(UUID_PATTERN);

    rerender(<MemoryRouter><ReplayStudio traceId="trace-2" span={switchedSpan} /></MemoryRouter>);

    expect(screen.queryByRole('button', { name: /重放中/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();
    expect(screen.queryByText('重放答案')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看重放 Trace' })).not.toBeInTheDocument();

    await ensureReplayConfirmed(user);
    const nextSubmit = screen.getByRole('button', { name: '开始真实重放' });
    expect(nextSubmit).toBeEnabled();
    await user.click(nextSubmit);

    await waitFor(() => expect(replaySpan).toHaveBeenCalledTimes(2));
    const nextKey = vi.mocked(replaySpan).mock.calls[1]?.[3];
    expect(nextKey).toEqual(expect.any(String));
    expect(nextKey).toMatch(UUID_PATTERN);
    expect(nextKey).not.toBe(oldKey);
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-2', 'span-2', {
      confirm: true,
      overrides: {},
    }, nextKey);
    expect(await screen.findByText('新实验答案')).toBeInTheDocument();
    expect(screen.getByText('内容相似度：88%')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看重放 Trace' })).toHaveAttribute('href', '/traces/replay-trace-2');

    await act(async () => {
      resolveOld(completedResult);
      await Promise.resolve();
    });

    expect(screen.getByText('新实验答案')).toBeInTheDocument();
    expect(screen.queryByText('重放答案')).not.toBeInTheDocument();
    expect(screen.queryByText('原始答案')).not.toBeInTheDocument();
    expect(screen.getByText('内容相似度：88%')).toBeInTheDocument();
    expect(screen.queryByText('内容相似度：42%')).not.toBeInTheDocument();
    const currentDiff = screen.getByText(/@@ -1 \+1 @@/);
    expect(currentDiff).toHaveTextContent('-第二条原始答案');
    expect(currentDiff).toHaveTextContent('+新实验答案');
    expect(currentDiff).not.toHaveTextContent('-原始答案');
    expect(currentDiff).not.toHaveTextContent('+重放答案');
    const traceLinks = screen.getAllByRole('link', { name: '查看重放 Trace' });
    expect(traceLinks).toHaveLength(1);
    expect(traceLinks[0]).toHaveAttribute('href', '/traces/replay-trace-2');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('首个请求 pending 时切换 Span，旧请求 late reject 不显示旧错误', async () => {
    let rejectOld!: (reason: Error) => void;
    const oldRequest = new Promise<typeof completedResult>((_resolve, reject) => {
      rejectOld = reject;
    });
    vi.mocked(replaySpan)
      .mockImplementationOnce(() => oldRequest)
      .mockResolvedValueOnce(switchedResult);
    const user = userEvent.setup();
    const { rerender } = renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(replaySpan).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /重放中/ })).toBeDisabled();
    rerender(<MemoryRouter><ReplayStudio traceId="trace-2" span={switchedSpan} /></MemoryRouter>);

    expect(screen.queryByRole('button', { name: /重放中/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await ensureReplayConfirmed(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));
    expect(await screen.findByText('新实验答案')).toBeInTheDocument();
    expect(replaySpan).toHaveBeenNthCalledWith(2, 'trace-2', 'span-2', {
      confirm: true,
      overrides: {},
    }, expect.any(String));

    await act(async () => {
      rejectOld(new Error('旧请求延迟失败'));
      await Promise.resolve();
    });

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('旧请求延迟失败')).not.toBeInTheDocument();
    expect(screen.getByText('新实验答案')).toBeInTheDocument();
    expect(screen.getByText('内容相似度：88%')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看重放 Trace' })).toHaveAttribute('href', '/traces/replay-trace-2');
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

  it('same 对比状态明确显示一致', async () => {
    vi.mocked(replaySpan).mockResolvedValue({
      ...completedResult,
      replay_trace: { ...replayTrace, output: { content: '原始答案' } },
      replay_span: {
        ...completedResult.replay_span,
        content: '原始答案',
        reasoning: '原始推理',
        output: { content: '原始答案' },
      },
      comparison: {
        ...completedResult.comparison,
        status: 'same' as const,
        content_equal: true,
        reasoning_equal: true,
        tool_calls_equal: true,
        content_similarity: 1,
        content_diff: '',
      },
    });
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByText('对比状态：一致')).toBeInTheDocument();
    expect(screen.getByText('内容一致')).toBeInTheDocument();
    expect(screen.getByText('推理一致')).toBeInTheDocument();
    expect(screen.getByText('内容相似度：100%')).toBeInTheDocument();
    expect(screen.queryByText('对比状态：不同')).not.toBeInTheDocument();
  });

  it('failed 的 201 结果展示脱敏上游错误与审计链接，而不是伪装成内容不同', async () => {
    const upstreamError = {
      status_code: 429,
      body: { error: { message: '上游限流 Bearer failed-secret' } },
    };
    vi.mocked(replaySpan).mockResolvedValue({
      ...completedResult,
      replay_trace: {
        ...replayTrace,
        id: 'replay-trace-failed',
        status: 'error',
        output: { error: upstreamError.body.error },
      },
      replay_span: {
        ...llmSpan,
        id: 'replay-span-failed',
        trace_id: 'replay-trace-failed',
        status: 'error',
        content: null,
        reasoning: null,
        output: { error: upstreamError.body.error },
        error: upstreamError,
      },
      comparison: {
        ...completedResult.comparison,
        status: 'failed' as const,
        content_equal: false,
        reasoning_equal: false,
        tool_calls_equal: false,
        content_similarity: 0,
        content_diff: '',
      },
    });
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByText('对比状态：重放失败')).toBeInTheDocument();
    expect(screen.queryByText('对比状态：不同')).not.toBeInTheDocument();
    expect(screen.getByText(/"status_code": 429/)).toBeInTheDocument();
    expect(screen.getByText(/上游限流 Bearer \[已隐藏\]/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('failed-secret');
    expect(screen.getByRole('link', { name: '查看重放 Trace' })).toHaveAttribute('href', '/traces/replay-trace-failed');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('原始内容、重放内容、推理和 diff 中的凭证全部经 UI 脱敏', async () => {
    const rawSecrets = [
      'source-content-secret',
      'sk-replay-content-123456',
      'sk-source-reasoning-123456',
      'replay-reasoning-secret',
      'diff-source-secret',
      'sk-diff-replay-123456',
    ];
    const sourceSpan: Span = {
      ...llmSpan,
      content: '原始答案 Bearer source-content-secret',
      reasoning: '原始推理 sk-source-reasoning-123456',
      output: { content: '原始答案 Bearer source-content-secret' },
    };
    const replayResultSpan: Span = {
      ...llmSpan,
      id: 'sensitive-replay-span',
      trace_id: replayTrace.id,
      content: '重放答案 sk-replay-content-123456',
      reasoning: '重放推理 Bearer replay-reasoning-secret',
      output: { content: '重放答案 sk-replay-content-123456' },
    };
    vi.mocked(replaySpan).mockResolvedValue({
      ...completedResult,
      replay_trace: { ...replayTrace, output: replayResultSpan.output },
      replay_span: replayResultSpan,
      source_span: sourceSpan,
      comparison: {
        ...completedResult.comparison,
        content_diff: [
          '--- 原始输出',
          '+++ 重放输出',
          '@@ -1 +1 @@',
          '-原始答案 Bearer diff-source-secret',
          '+重放答案 sk-diff-replay-123456',
        ].join('\n'),
      },
    });
    const user = userEvent.setup();
    renderStudio(sourceSpan);
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByText('原始答案 Bearer [已隐藏]')).toBeInTheDocument();
    expect(screen.getByText('重放答案 [已隐藏]')).toBeInTheDocument();
    expect(screen.getByText('原始推理 [已隐藏]')).toBeInTheDocument();
    expect(screen.getByText('重放推理 Bearer [已隐藏]')).toBeInTheDocument();
    const diff = screen.getByText(/@@ -1 \+1 @@/);
    expect(diff).toHaveTextContent('-原始答案 Bearer [已隐藏]');
    expect(diff).toHaveTextContent('+重放答案 [已隐藏]');
    expect(screen.getAllByText(/\[已隐藏\]/).length).toBeGreaterThanOrEqual(5);
    for (const secret of rawSecrets) {
      expect(document.body).not.toHaveTextContent(secret);
    }
  });

  it('API 错误中的 Bearer 与 sk 凭证同样脱敏且不泄露原文', async () => {
    vi.mocked(replaySpan).mockRejectedValue(
      new Error('上游返回 Bearer api-error-secret 与 sk-api-error-123456'),
    );
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('上游返回 Bearer [已隐藏] 与 [已隐藏]');
    expect(document.body).not.toHaveTextContent('api-error-secret');
    expect(document.body).not.toHaveTextContent('sk-api-error-123456');
  });

  it('API 拒绝时展示后端错误且不渲染伪造的重放结果', async () => {
    vi.mocked(replaySpan).mockRejectedValue(new Error('历史请求无法安全重建'));
    const user = userEvent.setup();
    renderStudio();
    await confirmReplay(user);
    await user.click(screen.getByRole('button', { name: '开始真实重放' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('历史请求无法安全重建');
    expect(screen.queryByRole('heading', { name: '重放输出' })).not.toBeInTheDocument();
    expect(screen.queryByText('重放答案')).not.toBeInTheDocument();
    expect(screen.queryByText('内容不同')).not.toBeInTheDocument();
    expect(screen.queryByText('推理不同')).not.toBeInTheDocument();
    expect(screen.queryByText(/内容相似度/)).not.toBeInTheDocument();
    expect(screen.queryByText(/@@ -1 \+1 @@/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看重放 Trace' })).not.toBeInTheDocument();
    expect(replaySpan).toHaveBeenCalledTimes(1);
  });
});
