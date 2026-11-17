import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Span } from '../api/types';
import { ReplayBar } from './ReplayBar';

const spans = ['s1', 's2', 's3'].map((id, index) => ({ id, name: `步骤 ${index + 1}` } as Span));

describe('回放控制交互', () => {
  it('下一步推进当前节点并同步外部选中状态', async () => {
    const user = userEvent.setup();
    const onActiveChange = vi.fn();
    render(<ReplayBar spans={spans} onActiveChange={onActiveChange} />);
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '下一步' }));
    expect(screen.getByText('2 / 3')).toBeInTheDocument();
    expect(onActiveChange).toHaveBeenLastCalledWith('s2');
    await user.click(screen.getByRole('button', { name: '上一步' }));
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    expect(onActiveChange).toHaveBeenLastCalledWith('s1');
  });

  it('外部从 DAG 或时间线选择节点时同步回放进度', () => {
    const onActiveChange = vi.fn();
    const { rerender } = render(<ReplayBar spans={spans} activeSpanId="s1" onActiveChange={onActiveChange} />);
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    rerender(<ReplayBar spans={spans} activeSpanId="s3" onActiveChange={onActiveChange} />);
    expect(screen.getByText('3 / 3')).toBeInTheDocument();
    expect(screen.getByText('步骤 3')).toBeInTheDocument();
  });

  it('可通过下拉框直接跳转到指定 Span', async () => {
    const user = userEvent.setup();
    const onActiveChange = vi.fn();
    render(<ReplayBar spans={spans} onActiveChange={onActiveChange} />);

    await user.selectOptions(screen.getByRole('combobox', { name: '跳转 Span' }), 's3');

    expect(screen.getByText('3 / 3')).toBeInTheDocument();
    expect(screen.getByText('步骤 3')).toBeInTheDocument();
    expect(onActiveChange).toHaveBeenLastCalledWith('s3');
  });

  it('Span 名称在下拉选项与当前节点标题中均脱敏', () => {
    const secretName = '节点 Bearer bar-name-secret 与 sk-bar-name-123456';
    render(<ReplayBar spans={[{ ...spans[0], name: secretName, sequence: 0 }]} onActiveChange={vi.fn()} />);

    const select = screen.getByRole('combobox', { name: '跳转 Span' });
    expect(select).toHaveTextContent('1. 节点 Bearer [已隐藏] 与 [已隐藏]');
    const currentName = screen.getByText('节点 Bearer [已隐藏] 与 [已隐藏]');
    expect(currentName).toHaveAttribute('title', '节点 Bearer [已隐藏] 与 [已隐藏]');
    expect(document.body).not.toHaveTextContent('bar-name-secret');
    expect(document.body).not.toHaveTextContent('sk-bar-name-123456');
  });
});
