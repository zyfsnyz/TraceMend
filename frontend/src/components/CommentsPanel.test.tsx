import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CommentsPanel } from './CommentsPanel';
import { createComment, patchComment } from '../api/client';
import type { Comment } from '../api/types';

vi.mock('../api/client', () => ({ createComment: vi.fn(), patchComment: vi.fn() }));

const created: Comment = {
  id: 'comment-1', span_id: 'span-1', author: '审计员', body: '请复核这个工具调用', status: 'open',
  created_at: '2026-11-11T10:00:00Z', updated_at: '2026-11-11T10:00:00Z',
};

describe('CommentsPanel 交互', () => {
  afterEach(() => vi.unstubAllGlobals());

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn(), clear: vi.fn() });
    localStorage.clear();
    vi.mocked(createComment).mockResolvedValue(created);
    vi.mocked(patchComment).mockResolvedValue({ ...created, status: 'resolved' });
  });

  it('真实填写并提交 Span 评论，调用正确 API 后刷新', async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(<CommentsPanel traceId="trace-1" selectedSpanId="span-1" comments={[]} onChanged={onChanged} />);

    await user.type(screen.getByRole('textbox', { name: '评论作者' }), '审计员');
    await user.selectOptions(screen.getByRole('combobox', { name: '评论关联范围' }), 'span');
    await user.type(screen.getByRole('textbox', { name: '评论内容' }), '请复核这个工具调用');
    await user.click(screen.getByRole('button', { name: '发表评论' }));

    await waitFor(() => expect(createComment).toHaveBeenCalledWith('trace-1', 'span-1', '审计员', '请复核这个工具调用'));
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('textbox', { name: '评论内容' })).toHaveValue('');
  });
});
