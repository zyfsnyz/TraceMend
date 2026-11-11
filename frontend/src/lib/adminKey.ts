const STORAGE_KEY = 'agenttrace.adminKey';
export const UNAUTHORIZED_EVENT = 'agenttrace:unauthorized';
let volatileKey = '';

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function getAdminKey(): string {
  try {
    const stored = session()?.getItem(STORAGE_KEY);
    if (stored) return stored;
  } catch { /* 读取被浏览器拒绝时回退到内存态 */ }
  return volatileKey;
}

export function setAdminKey(value: string): void {
  const key = value.trim();
  if (!key) {
    clearAdminKey();
    return;
  }
  volatileKey = key;
  try { session()?.setItem(STORAGE_KEY, key); } catch { /* 隐私模式禁用存储时保留内存态 Header */ }
}

export function clearAdminKey(): void {
  volatileKey = '';
  try { session()?.removeItem(STORAGE_KEY); } catch { /* storage 不可用时只需清理内存态 */ }
}
