import type { JsonValue } from '../api/types';

const SENSITIVE_KEY = /^(?:api[-_]?key|authorization|proxy-authorization|password|passwd|secret|client[-_]?secret|access[-_]?token|refresh[-_]?token|id[-_]?token|credential(?:s)?|cookie|set-cookie|session[-_]?key|private[-_]?key|x-api-key)$/i;

export function sanitizeSensitive(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeSensitive);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key, SENSITIVE_KEY.test(key) ? '[已隐藏]' : sanitizeSensitive(item),
    ]));
  }
  return value;
}

export function stringifySafe(value: JsonValue | Record<string, unknown> | undefined): string {
  if (value === undefined || value === null) return '暂无数据';
  if (typeof value === 'string') return value;
  return JSON.stringify(sanitizeSensitive(value), null, 2);
}
