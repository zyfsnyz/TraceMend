import { describe, expect, it } from 'vitest';
import { sanitizeSensitive, stringifySafe } from './sensitive';

describe('敏感字段展示保护', () => {
  it('递归隐藏密钥、令牌和请求头，但不隐藏 token 用量', () => {
    const result = sanitizeSensitive({
      api_key: 'sk-live-value', Authorization: 'Bearer value', prompt_tokens: 42,
      nested: [{ password: '123456', harmless: '可见' }],
    });
    expect(result).toEqual({
      api_key: '[已隐藏]', Authorization: '[已隐藏]', prompt_tokens: 42,
      nested: [{ password: '[已隐藏]', harmless: '可见' }],
    });
  });

  it('原始 JSON 中也不泄露敏感值', () => {
    expect(stringifySafe({ upstream: { 'X-Api-Key': 'very-secret' } })).not.toContain('very-secret');
    expect(stringifySafe(undefined)).toBe('暂无数据');
  });
});
