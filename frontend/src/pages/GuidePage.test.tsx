import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { GuidePage } from './GuidePage';

describe('GuidePage MCP 接入地址', () => {
  it('展示并复制同源 /mcp 地址，而不是 /v1/mcp', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    render(<GuidePage />);

    const card = screen.getByRole('heading', { name: 'MCP 工具代理' }).closest('section');
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole('button', { name: '复制代码' }));

    const mcpUrl = `${window.location.origin}/mcp`;
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`"url": "${mcpUrl}"`));
    expect(writeText.mock.calls[0][0]).not.toContain('/v1/mcp');
    expect(card).toHaveTextContent(`"url": "${mcpUrl}"`);
  });
});
