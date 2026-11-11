import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, ApiError, getTraces } from './client';

afterEach(() => vi.unstubAllGlobals());

describe('API 错误处理', () => {
  it('保留后端中文 detail 与 HTTP 状态码', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '链路不存在' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    })));
    const error = await apiFetch('/api/traces/missing').catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: '链路不存在' });
  });

  it('后端无响应时给出可操作的连接错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(apiFetch('/api/stats')).rejects.toThrow('无法连接审计服务，请检查后端是否启动');
  });

  it('只序列化有效筛选参数并解析列表', async () => {
    const responseBody = { items: [], total: 0, page: 2, page_size: 10, pages: 0 };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(responseBody), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(getTraces({ q: '天气', status: '', risk: 'high', page: 2, page_size: 10 })).resolves.toEqual(responseBody);
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe('/api/traces');
    expect(Object.fromEntries(url.searchParams)).toEqual({ q: '天气', risk: 'high', page: '2', page_size: '10' });
  });
});
