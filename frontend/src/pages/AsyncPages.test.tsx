import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RisksPage } from './RisksPage';
import { TracesPage } from './TracesPage';
import { getTrace, getTraces } from '../api/client';
import type { Finding, Trace, TraceDetail, TraceListResponse } from '../api/types';

vi.mock('../api/client', () => ({ deleteTrace: vi.fn(), getTrace: vi.fn(), getTraces: vi.fn() }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function makeTrace(id: string, name: string): Trace {
  return {
    id, name, source: 'test', status: 'completed', model: 'test-model', input: null, output: null,
    started_at: '2026-11-11T10:00:00Z', ended_at: null, duration_ms: 10,
    prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, tags: [], metadata: {}, error: null,
    risk_count: 1, span_count: 0,
  };
}

function makeList(trace: Trace): TraceListResponse {
  return { items: [trace], total: 1, page: 1, page_size: 20, pages: 1 };
}

function makeFinding(severity: string): Finding {
  return {
    id: `finding-${severity}`, span_id: null, source: 'rule', rule_code: 'test', severity,
    category: 'test', title: severity, explanation: '', evidence: null, created_at: '',
  };
}

function makeDetail(trace: Trace, severity = 'high'): TraceDetail {
  return { trace, spans: [], findings: [makeFinding(severity)], comments: [] };
}

function renderAt(ui: React.ReactNode, route: string) {
  return render(<MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>);
}

describe('异步请求只接受最新一代结果', () => {
  beforeEach(() => vi.clearAllMocks());

  it('TracesPage：A 慢 B 快且 A 最后完成时保留 B，并不恢复 loading', async () => {
    const user = userEvent.setup();
    const slowA = deferred<TraceListResponse>();
    const fastB = deferred<TraceListResponse>();
    vi.mocked(getTraces).mockReturnValueOnce(slowA.promise).mockReturnValueOnce(fastB.promise);
    renderAt(<TracesPage />, '/traces');
    await waitFor(() => expect(getTraces).toHaveBeenCalledTimes(1));

    await user.selectOptions(screen.getByRole('combobox', { name: '风险筛选' }), 'high');
    await waitFor(() => expect(getTraces).toHaveBeenCalledTimes(2));
    await act(async () => { fastB.resolve(makeList(makeTrace('b', '快速 B'))); await fastB.promise; });
    expect(await screen.findByText('快速 B')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();

    await act(async () => { slowA.resolve(makeList(makeTrace('a', '慢速 A'))); await slowA.promise; });
    expect(screen.getByText('快速 B')).toBeInTheDocument();
    expect(screen.queryByText('慢速 A')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();
  });

  it('RisksPage：旧一代多个详情最后完成时不覆盖新一代列表与风险徽标', async () => {
    const user = userEvent.setup();
    const oldDetailA = deferred<TraceDetail>();
    const oldDetailB = deferred<TraceDetail>();
    const sharedA = makeTrace('shared', '慢速 A');
    const sharedB = makeTrace('shared', '快速 B');
    vi.mocked(getTraces)
      .mockResolvedValueOnce({ ...makeList(sharedA), items: [sharedA, makeTrace('a-2', '旧 A2')], total: 2 })
      .mockResolvedValueOnce(makeList(sharedB));
    vi.mocked(getTrace).mockImplementation((id) => {
      if (id === 'shared' && vi.mocked(getTraces).mock.calls.length === 1) return oldDetailA.promise;
      if (id === 'a-2') return oldDetailB.promise;
      return Promise.resolve(makeDetail(sharedB, 'high'));
    });
    renderAt(<RisksPage />, '/risks');
    await waitFor(() => expect(getTrace).toHaveBeenCalledTimes(2));

    await user.click(screen.getByRole('button', { name: '高风险' }));
    const link = await screen.findByRole('link', { name: '快速 B' });
    const row = link.closest('div.flex.flex-wrap.items-center.gap-4')!;
    expect(within(row as HTMLElement).getByText('高风险')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();

    await act(async () => {
      oldDetailA.resolve(makeDetail(sharedA, 'low'));
      oldDetailB.resolve(makeDetail(makeTrace('a-2', '旧 A2'), 'critical'));
      await Promise.all([oldDetailA.promise, oldDetailB.promise]);
    });
    expect(screen.getByRole('link', { name: '快速 B' })).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('高风险')).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText('低风险')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeEnabled();
  });
});
