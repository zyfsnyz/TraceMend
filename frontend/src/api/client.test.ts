import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, ApiError, createComment, createGatewayRoute, downloadReport, getSettings, getTraces, llmCheckTrace, patchComment, replaySpan, saveSettings } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  try { window.sessionStorage.removeItem('agenttrace.adminKey'); } catch { /* happy-dom 未提供 Storage 时忽略 */ }
});

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

describe('评论、LLM 检测与报告 API 契约', () => {
  const comment = { id: 'comment-1', span_id: null, author: '审计员', body: '结论', status: 'open', created_at: '', updated_at: '' };

  function mockJson(body: unknown, status = 200) {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('向 Trace 评论端点 POST 作者与内容', async () => {
    const fetchMock = mockJson(comment);
    await createComment('trace-1', null, '审计员', '结论');

    expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-1/comments', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ span_id: null, author: '审计员', body: '结论', status: 'open' }),
    }));
  });

  it('向 Span 评论端点 POST，并保留 span_id', async () => {
    const fetchMock = mockJson({ ...comment, span_id: 'span-1' });
    await createComment('trace-1', 'span-1', '审计员', '结论');

    expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-1/spans/span-1/comments', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ span_id: 'span-1', author: '审计员', body: '结论', status: 'open' }),
    }));
  });

  it('PATCH 评论 resolved 状态', async () => {
    const fetchMock = mockJson({ ...comment, status: 'resolved' });
    await patchComment('comment-1', 'resolved');

    expect(fetchMock).toHaveBeenCalledWith('/api/comments/comment-1', expect.objectContaining({
      method: 'PATCH', body: JSON.stringify({ status: 'resolved' }),
    }));
  });

  it('llm-check 的 409 响应保持失败并携带后端说明', async () => {
    mockJson({ detail: 'LLM 检测未配置' }, 409);

    await expect(llmCheckTrace('trace-1')).rejects.toMatchObject({
      name: 'ApiError', status: 409, message: 'LLM 检测未配置',
    });
  });

  it('从正确的 report 端点下载 Markdown 文件', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['# 审计报告']), {
      status: 200, headers: { 'Content-Disposition': 'attachment; filename="trace-audit.md"' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    await downloadReport('trace-1', 'md');

    expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-1/report.md');
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report');
  });

  it('开启管理鉴权时报告下载同样携带密钥', async () => {
    stubSessionStorage();
    window.sessionStorage.setItem('agenttrace.adminKey', 'local-admin');
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['# 审计报告']), {
      status: 200, headers: { 'Content-Disposition': 'attachment; filename="trace-audit.md"' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    await downloadReport('trace-1', 'md');

    expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-1/report.md', {
      headers: { 'X-Admin-Key': 'local-admin' },
    });
  });

  it('Content-Disposition 含非法百分号编码时仍完成下载并使用安全回退文件名', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new Blob(['# 审计报告']), {
      status: 200, headers: { 'Content-Disposition': 'attachment; filename="%E0%A4%A.md"' },
    })));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    let anchor: HTMLAnchorElement | undefined;
    const createElement = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation(((tag: string, options?: ElementCreationOptions) => {
      const element = createElement(tag, options);
      if (tag === 'a') anchor = element as HTMLAnchorElement;
      return element;
    }) as typeof document.createElement);

    await expect(downloadReport('trace-1', 'md')).resolves.toBeUndefined();

    expect(anchor?.download).toBe('TraceMend-trace-1-report.md');
  });
});

describe('单 Span 真实重放 API 契约', () => {
  it('POST 到嵌套 replay 端点，并发送确认、覆盖参数与 UUID 幂等键', async () => {
    const responseBody = {
      replay_trace: { id: 'replay-trace-1' },
      replay_span: { id: 'replay-span-1' },
      source_span: { id: 'span-1' },
      comparison: { status: 'different' },
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(responseBody), {
      status: 201, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const idempotencyKey = '6d53f47b-37d0-4b5f-b57f-e13bd68f2157';
    const payload = {
      confirm: true as const,
      overrides: { model: 'replay-model', temperature: 0.2, top_p: 0.9, seed: 42, max_tokens: 512 },
    };

    await expect(replaySpan('trace-1', 'span-1', payload, idempotencyKey)).resolves.toEqual(responseBody);

    expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace-1/spans/span-1/replay', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(payload),
    }));
    const options = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(options.headers).get('Idempotency-Key')).toBe(idempotencyKey);
  });
});

describe('管理密钥请求头', () => {
  it('保存的密钥随 API 请求发送，并在 401 时触发提示事件', async () => {
    stubSessionStorage();
    window.sessionStorage.setItem('agenttrace.adminKey', 'local-admin');
    const unauthorized = vi.fn();
    window.addEventListener('agenttrace:unauthorized', unauthorized);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '管理密钥无效' }), {
      status: 401, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiFetch('/api/traces')).rejects.toMatchObject({ status: 401 });
    expect(fetchMock.mock.calls[0][1].headers.get('X-Admin-Key')).toBe('local-admin');
    expect(unauthorized).toHaveBeenCalledTimes(1);
    window.removeEventListener('agenttrace:unauthorized', unauthorized);
  });
});

describe('网页设置与模型网关 API', () => {
  function mockJson(body: unknown, status = 200) {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('设置读取与保存使用正确端点，密钥只出现在用户主动提交的 PUT 中', async () => {
    const settings = { default_upstream_base: 'https://api.deepseek.com/v1' };
    const getMock = mockJson(settings);
    await getSettings();
    expect(getMock).toHaveBeenCalledWith('/api/settings', expect.anything());

    const putMock = mockJson(settings);
    await saveSettings({ default_upstream_base: 'https://api.deepseek.com/v1', default_upstream_key: 'new-secret' });
    const options = putMock.mock.calls[0][1];
    expect(putMock.mock.calls[0][0]).toBe('/api/settings');
    expect(options.method).toBe('PUT');
    expect(JSON.parse(options.body)).toEqual({ default_upstream_base: 'https://api.deepseek.com/v1', default_upstream_key: 'new-secret' });
  });

  it('创建路由使用写字段 api_key，并保留优先级与价格', async () => {
    const fetchMock = mockJson({ id: 'route-1' }, 201);
    const payload = {
      name: '主路由', match_model: 'gpt-*', target_model: 'deepseek-chat',
      upstream_base: 'https://api.deepseek.com/v1', api_key: 'route-secret', priority: 10,
      enabled: true, input_price_per_million: 0.5, output_price_per_million: 1.5,
    };
    await createGatewayRoute(payload);
    expect(fetchMock).toHaveBeenCalledWith('/api/gateway/routes', expect.objectContaining({
      method: 'POST', body: JSON.stringify(payload),
    }));
  });
});
