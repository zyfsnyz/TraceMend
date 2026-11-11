import { describe, expect, it } from 'vitest';
import config from '../../vite.config';

describe('Vite 开发代理', () => {
  it('将同源 MCP JSON-RPC 请求转发到审计后端', () => {
    expect(config.server?.proxy?.['/mcp']).toMatchObject({
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
    });
  });
});
