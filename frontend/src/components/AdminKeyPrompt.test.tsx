import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AdminKeyPrompt } from './AdminKeyPrompt';

function stubSessionStorage() {
  const data = new Map<string, string>();
  Object.defineProperty(window, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => data.set(key, value),
      removeItem: (key: string) => data.delete(key),
    },
  });
  return data;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('管理密钥提示', () => {
  it('验证通过的密钥只保存到当前标签页，并触发页面重载', async () => {
    const storage = stubSessionStorage();
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}', {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<AdminKeyPrompt open onClose={() => {}} onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText('输入后端 .env 中的 ADMIN_KEY'), 'local-admin');
    await user.click(screen.getByRole('button', { name: '验证并保存' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(storage.get('agenttrace.adminKey')).toBe('local-admin');
    expect(fetchMock).toHaveBeenCalledWith('/api/traces?page_size=1', expect.objectContaining({
      headers: { 'X-Admin-Key': 'local-admin' },
    }));
  });

  it('密钥错误时保持提示并展示后端信息', async () => {
    const storage = stubSessionStorage();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '管理密钥无效' }), {
      status: 401, headers: { 'Content-Type': 'application/json' },
    })));
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<AdminKeyPrompt open onClose={() => {}} onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText('输入后端 .env 中的 ADMIN_KEY'), 'wrong');
    await user.click(screen.getByRole('button', { name: '验证并保存' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('管理密钥无效');
    expect(onSaved).not.toHaveBeenCalled();
    expect(storage.has('agenttrace.adminKey')).toBe(false);
  });

  it('非 ASCII 密钥在前端直接拒绝，不发出请求', async () => {
    stubSessionStorage();
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<AdminKeyPrompt open onClose={() => {}} onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText('输入后端 .env 中的 ADMIN_KEY'), '测试密钥');
    await user.click(screen.getByRole('button', { name: '验证并保存' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('只能使用 ASCII');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
