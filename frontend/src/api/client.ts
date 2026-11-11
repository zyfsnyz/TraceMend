import type { Comment, Stats, TraceDetail, TraceFilters, TraceListResponse } from './types';

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let message = `请求失败（${response.status}）`;
  try {
    const body = await response.json() as { detail?: string | { msg?: string }[] };
    if (typeof body.detail === 'string') message = body.detail;
    else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join('；') || message;
  } catch { /* 非 JSON 错误使用状态码说明 */ }
  return new ApiError(message, response.status);
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError('无法连接审计服务，请检查后端是否启动。', 0);
  }
  if (!response.ok) throw await errorFromResponse(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function getStats(days = 30): Promise<Stats> {
  return apiFetch<Stats>(`/api/stats?days=${days}`);
}

export function getTraces(filters: TraceFilters = {}, signal?: AbortSignal): Promise<TraceListResponse> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim() !== '') params.set(key, String(value));
  });
  const query = params.toString();
  return apiFetch<TraceListResponse>(`/api/traces${query ? `?${query}` : ''}`, { signal });
}

export function getTrace(id: string, signal?: AbortSignal): Promise<TraceDetail> {
  return apiFetch<TraceDetail>(`/api/traces/${encodeURIComponent(id)}`, { signal });
}

export function deleteTrace(id: string): Promise<void> {
  return apiFetch<void>(`/api/traces/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function createComment(traceId: string, spanId: string | null, author: string, body: string): Promise<Comment> {
  const path = spanId
    ? `/api/traces/${encodeURIComponent(traceId)}/spans/${encodeURIComponent(spanId)}/comments`
    : `/api/traces/${encodeURIComponent(traceId)}/comments`;
  return apiFetch<Comment>(path, { method: 'POST', body: JSON.stringify({ span_id: spanId, author, body, status: 'open' }) });
}

export function patchComment(id: string, status: 'open' | 'resolved'): Promise<Comment> {
  return apiFetch<Comment>(`/api/comments/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status }) });
}

export function reanalyzeTrace(id: string): Promise<unknown> {
  return apiFetch(`/api/traces/${encodeURIComponent(id)}/reanalyze`, { method: 'POST' });
}

export function llmCheckTrace(id: string): Promise<unknown> {
  return apiFetch(`/api/traces/${encodeURIComponent(id)}/llm-check`, { method: 'POST' });
}

export async function downloadReport(id: string, format: 'md' | 'pdf'): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`/api/traces/${encodeURIComponent(id)}/report.${format}`);
  } catch {
    throw new ApiError('无法连接审计服务，请检查后端是否启动。', 0);
  }
  if (!response.ok) throw await errorFromResponse(response);
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const matched = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(disposition)?.[1];
  const filename = matched ? decodeURIComponent(matched.trim()) : `AgentTrace-${id.slice(0, 8)}-report.${format}`;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
