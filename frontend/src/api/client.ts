import { getAdminKey, UNAUTHORIZED_EVENT } from '../lib/adminKey';
import type {
  AppSettings, Comment, ConnectionTestResult, GatewayRoute, GatewayRouteInput,
  GatewayRouteUpdate, ReplaySpanRequest, ReplaySpanResult, SettingsUpdate, Stats, TraceDetail, TraceFilters, TraceListResponse,
} from './types';

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
  if (response.status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  return new ApiError(message, response.status);
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    const headers = new Headers(options.headers);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const adminKey = getAdminKey();
    if (adminKey) headers.set('X-Admin-Key', adminKey);
    response = await fetch(path, { ...options, headers });
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

export function replaySpan(
  traceId: string,
  spanId: string,
  payload: ReplaySpanRequest,
  idempotencyKey: string,
): Promise<ReplaySpanResult> {
  const path = `/api/traces/${encodeURIComponent(traceId)}/spans/${encodeURIComponent(spanId)}/replay`;
  return apiFetch<ReplaySpanResult>(path, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function getSettings(): Promise<AppSettings> {
  return apiFetch<AppSettings>('/api/settings');
}

export function saveSettings(payload: SettingsUpdate): Promise<AppSettings> {
  return apiFetch<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) });
}

export function testConnection(kind: 'openai' | 'check' | 'mcp'): Promise<ConnectionTestResult> {
  return apiFetch<ConnectionTestResult>('/api/settings/test', { method: 'POST', body: JSON.stringify({ kind }) });
}

export function getGatewayRoutes(): Promise<{ items: GatewayRoute[] }> {
  return apiFetch<{ items: GatewayRoute[] }>('/api/gateway/routes');
}

export function createGatewayRoute(payload: GatewayRouteInput): Promise<GatewayRoute> {
  return apiFetch<GatewayRoute>('/api/gateway/routes', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateGatewayRoute(id: string, payload: GatewayRouteUpdate): Promise<GatewayRoute> {
  return apiFetch<GatewayRoute>(`/api/gateway/routes/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
}

export function deleteGatewayRoute(id: string): Promise<void> {
  return apiFetch<void>(`/api/gateway/routes/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function resetGatewayRoute(id: string): Promise<GatewayRoute> {
  return apiFetch<GatewayRoute>(`/api/gateway/routes/${encodeURIComponent(id)}/reset`, { method: 'POST' });
}

export async function downloadReport(id: string, format: 'md' | 'pdf'): Promise<void> {
  let response: Response;
  const adminKey = getAdminKey();
  try {
    const url = `/api/traces/${encodeURIComponent(id)}/report.${format}`;
    response = adminKey ? await fetch(url, { headers: { 'X-Admin-Key': adminKey } }) : await fetch(url);
  } catch {
    throw new ApiError('无法连接审计服务，请检查后端是否启动。', 0);
  }
  if (!response.ok) throw await errorFromResponse(response);
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const matched = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(disposition)?.[1];
  const fallback = `AgentTrace-${id.slice(0, 8)}-report.${format}`;
  let filename = fallback;
  if (matched) {
    try {
      filename = decodeURIComponent(matched.trim());
    } catch {
      filename = fallback;
    }
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
