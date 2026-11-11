import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyButton, Modal } from './ui';

function DemoModal() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>打开对话框</button>
      {open && (
        <Modal title="删除链路" onClose={() => setOpen(false)}>
          <button>取消</button>
          <button>确认删除</button>
        </Modal>
      )}
    </>
  );
}

describe('通用交互细节', () => {
  afterEach(() => vi.restoreAllMocks());

  it('剪贴板失败时显示复制失败，不谎报成功', async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(() => false) });
    render(<CopyButton text="secret" />);
    await user.click(screen.getByRole('button', { name: '复制' }));
    expect(await screen.findByText('复制失败')).toBeInTheDocument();
    expect(screen.queryByText('已复制')).not.toBeInTheDocument();
  });

  it('对话框聚焦首个安全按钮、Tab 受限、Escape 关闭并恢复触发焦点', async () => {
    const user = userEvent.setup();
    render(<DemoModal />);
    const trigger = screen.getByRole('button', { name: '打开对话框' });
    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: '删除链路' });
    const close = screen.getByRole('button', { name: '关闭' });
    expect(close).toHaveFocus();

    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(screen.getByRole('button', { name: '确认删除' })).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
