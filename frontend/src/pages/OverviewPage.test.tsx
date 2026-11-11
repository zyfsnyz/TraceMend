import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OverviewPage } from './OverviewPage';
import { getStats, getTraces } from '../api/client';
import type { Stats, TraceListResponse } from '../api/types';

vi.mock('../api/client', () => ({ getStats: vi.fn(), getTraces: vi.fn() }));

const stats: Stats = {
  total_traces: 7,
  completed_traces: 5,
  error_traces: 2,
  risk_traces: 3,
  total_findings: 4,
  total_tokens: 1200,
  avg_duration_ms: 250,
  tool_calls: 6,
  models: { 'gpt-4o-mini': 5, 'claude-sonnet': 2 },
  daily: [
    { date: '2026-11-10', count: 3 },
    { date: '2026-11-11', count: 4 },
  ],
};
const recent: TraceListResponse = { items: [], total: 0, page: 1, page_size: 6, pages: 0 };

describe('OverviewPage 真实统计契约', () => {
  beforeEach(() => {
    vi.mocked(getStats).mockResolvedValue(stats);
    vi.mocked(getTraces).mockResolvedValue(recent);
  });

  it('展示 Record 模型分布与每日 count，图表坐标不包含 NaN', async () => {
    render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><OverviewPage /></MemoryRouter>);

    const modelSection = (await screen.findByText('gpt-4o-mini')).closest('section');
    expect(modelSection).not.toBeNull();
    expect(within(modelSection!).getByText('claude-sonnet')).toBeInTheDocument();
    expect(within(modelSection!).getByText('5')).toBeInTheDocument();
    expect(within(modelSection!).getByText('2')).toBeInTheDocument();

    const trend = screen.getByRole('img', { name: '每日链路数趋势图' });
    expect(trend).toHaveTextContent('2026-11-10：3 条链路');
    expect(trend).toHaveTextContent('2026-11-11：4 条链路');
    expect(trend.outerHTML).not.toMatch(/NaN|Infinity/);
  });
});
