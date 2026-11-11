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

  it('按归一化键名隐藏嵌套对象和数组中的常见凭证变体', () => {
    const secrets = {
      OPENAI_API_KEY: 'openai-key-value',
      token: 'token-value',
      apiToken: 'api-token-value',
      'ACCESS-TOKEN': 'access-token-value',
      refresh_token: 'refresh-token-value',
      clientSecret: 'client-secret-value',
      userPassword: 'password-value',
      passwd: 'passwd-value',
      authorization: 'authorization-value',
      Cookie: 'cookie-value',
      credentials: 'credential-value',
      'PRIVATE-KEY': 'private-key-value',
    };
    const result = sanitizeSensitive({ nested: [{ request: secrets }], prompt_tokens: 42, total_tokens: 42 });

    expect(result).toEqual({
      nested: [{ request: Object.fromEntries(Object.keys(secrets).map((key) => [key, '[已隐藏]'])) }],
      prompt_tokens: 42,
      total_tokens: 42,
    });
    expect(JSON.stringify(result)).not.toMatch(/openai-key-value|token-value|secret-value|password-value|cookie-value|private-key-value/);
  });

  it('脱敏嵌入字符串的 Bearer 与 sk 凭证，同时保留普通中文文本', () => {
    const chinese = '这是普通中文文本：今天天气很好，token 是普通单词。';
    const result = sanitizeSensitive({
      messages: [
        { content: '请求头为 Bearer embedded-bearer-token，请检查。' },
        ['凭证 sk-proj-abc123456789 不要展示', chinese],
      ],
    });
    const serialized = JSON.stringify(result);

    expect(serialized).not.toContain('embedded-bearer-token');
    expect(serialized).not.toContain('sk-proj-abc123456789');
    expect(serialized).toContain('Bearer [已隐藏]');
    expect(serialized).toContain(chinese);
    expect(stringifySafe('Bearer top-level-secret')).toBe('Bearer [已隐藏]');
  });
});
