import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RisksPage } from './RisksPage';
import { TracesPage } from './TracesPage';
import { getTrace, getTraces } from '../api/client';
import type { TraceListResponse } from '../api/types';

vi.mock('../api/client', () => ({ deleteTrace: vi.fn(), getTrace: vi.fn(), getTraces: vi.fn() }));

const emptyList: TraceListResponse = { items: [], total: 0, page: 1, page_size: 20, pages: 0 };

function renderAt(ui: React.ReactNode, route: string) {
  return render(<MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>);
}

describe('风险筛选后端契约', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTraces).mockResolvedValue(emptyList);
    vi.mocked(getTrace).mockResolvedValue({ trace: {} as never, spans: [], findings: [], comments: [] });
  });

  it('风险中心默认请求 risk=any', async () => {
    renderAt(<RisksPage />, '/risks');

    await waitFor(() => expect(getTraces).toHaveBeenCalledWith({ risk: 'any', q: '', page: 1, page_size: 20 }, expect.any(AbortSignal)));
  });

  it('风险中心从具体严重度切回全部风险时把 URL 与请求更新为 any', async () => {
    const user = userEvent.setup();
    renderAt(<RisksPage />, '/risks?risk=high');
    await waitFor(() => expect(getTraces).toHaveBeenCalledWith({ risk: 'high', q: '', page: 1, page_size: 20 }, expect.any(AbortSignal)));

    await user.click(screen.getByRole('button', { name: '全部风险' }));

    await waitFor(() => expect(getTraces).toHaveBeenLastCalledWith({ risk: 'any', q: '', page: 1, page_size: 20 }, expect.any(AbortSignal)));
    expect(screen.getByRole('button', { name: '全部风险' })).toHaveClass('bg-white');
  });

  it('链路列表的无风险筛选使用 risk=none，而不是布尔字符串', async () => {
    const user = userEvent.setup();
    renderAt(<TracesPage />, '/traces');
    await waitFor(() => expect(getTraces).toHaveBeenCalled());

    await user.selectOptions(screen.getByRole('combobox', { name: '风险筛选' }), 'none');

    await waitFor(() => expect(getTraces).toHaveBeenLastCalledWith(expect.objectContaining({ risk: 'none', page: 1 }), expect.any(AbortSignal)));
    expect(screen.getByRole('option', { name: '仅看有风险' })).toHaveValue('any');
  });
});
