import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearAdminKey, getAdminKey, setAdminKey } from './adminKey';

afterEach(() => {
  vi.restoreAllMocks();
  try { clearAdminKey(); } catch { /* storage 不可用时由实现处理 */ }
});

describe('管理密钥存储', () => {
  it('浏览器禁用 sessionStorage 时仍可用内存态完成当前页面鉴权', () => {
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() { throw new DOMException('Access denied', 'SecurityError'); },
    });

    setAdminKey('memory-only-key');

    expect(getAdminKey()).toBe('memory-only-key');
    clearAdminKey();
    expect(getAdminKey()).toBe('');
  });
});
