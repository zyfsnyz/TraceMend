import { describe, expect, it } from 'vitest';
import config from '../../vite.config';

describe('Vite 开发代理', () => {
  it('默认只监听回环地址，避免把审计面板暴露到局域网', () => {
    expect(config.server?.host).toBe('127.0.0.1');
  });

  it('将同源 MCP JSON-RPC 请求转发到审计后端', () => {
    expect(config.server?.proxy?.['/mcp']).toMatchObject({
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
    });
  });
});
