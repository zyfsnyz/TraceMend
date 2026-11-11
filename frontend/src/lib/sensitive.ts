import type { JsonValue } from '../api/types';

const REDACTED = '[已隐藏]';
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const SK_TOKEN = /\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b/g;

function isSensitiveKey(key: string): boolean {
  const normalized = key.replace(/[^a-z0-9]/gi, '').toLowerCase();
  return normalized.includes('apikey')
    || normalized === 'token'
    || normalized.endsWith('token')
    || normalized.includes('secret')
    || normalized.includes('password')
    || normalized.includes('passwd')
    || normalized.includes('authorization')
    || normalized.includes('cookie')
    || normalized.includes('credential')
    || normalized.includes('privatekey')
    || normalized.endsWith('sessionkey');
}

function sanitizeString(value: string): string {
  return value.replace(BEARER_TOKEN, `Bearer ${REDACTED}`).replace(SK_TOKEN, REDACTED);
}

export function sanitizeSensitive(value: unknown): unknown {
  if (typeof value === 'string') return sanitizeString(value);
  if (Array.isArray(value)) return value.map(sanitizeSensitive);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key, isSensitiveKey(key) ? REDACTED : sanitizeSensitive(item),
    ]));
  }
  return value;
}

export function stringifySafe(value: JsonValue | Record<string, unknown> | undefined): string {
  if (value === undefined || value === null) return '暂无数据';
  if (typeof value === 'string') return sanitizeString(value);
  return JSON.stringify(sanitizeSensitive(value), null, 2);
}
