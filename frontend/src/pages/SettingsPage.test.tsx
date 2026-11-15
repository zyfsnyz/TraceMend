import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import type { AppSettings, GatewayRoute } from '../api/types';

const api = vi.hoisted(() => ({
  getSettings: vi.fn(), saveSettings: vi.fn(), testConnection: vi.fn(),
  getGatewayRoutes: vi.fn(), createGatewayRoute: vi.fn(), updateGatewayRoute: vi.fn(),
  deleteGatewayRoute: vi.fn(), resetGatewayRoute: vi.fn(),
}));

vi.mock('../api/client', () => api);

const settings: AppSettings = {
  default_upstream_base: 'https://api.deepseek.com/v1', default_upstream_model: 'deepseek-chat',
  default_upstream_key_configured: false, default_mcp_upstream: '', default_mcp_key_configured: false,
  enable_llm_check: true, check_model_base: 'https://apihub.agnes-ai.com/v1',
  check_model_key_configured: true, check_model_name: 'agnes-3.0-flash', check_model_timeout: 60,
  gateway_daily_token_budget: 100000, gateway_failure_threshold: 2, gateway_cooldown_seconds: 30,
  default_input_price_per_million: 0, default_output_price_per_million: 0,
};

const route: GatewayRoute = {
  id: 'route-1', name: 'Agnes 主路由', match_model: 'agnes-*', target_model: 'agnes-3.0-flash',
  upstream_base: 'https://apihub.agnes-ai.com/v1', api_key_configured: true, priority: 10,
  enabled: true, input_price_per_million: 0.2, output_price_per_million: 0.6,
  circuit: { status: 'closed', failures: 0, open_remaining_seconds: 0 },
};

afterEach(() => { vi.clearAllMocks(); });

describe('网页设置中心', () => {
  it('加载非敏感配置，保存时只附加主动输入的新密钥', async () => {
    api.getSettings.mockResolvedValue(settings);
    api.getGatewayRoutes.mockResolvedValue({ items: [route] });
    api.saveSettings.mockResolvedValue({ ...settings, default_upstream_key_configured: true });
    const user = userEvent.setup();
    render(<SettingsPage />);

    expect(await screen.findByDisplayValue('deepseek-chat')).toBeInTheDocument();
    expect(screen.getByText('Agnes 主路由')).toBeInTheDocument();
    expect(screen.getByText('已配置')).toBeInTheDocument();
    await user.type(screen.getByLabelText('默认上游 API Key'), 'new-secret');
    await user.click(screen.getByRole('button', { name: '保存全部设置' }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    const payload = api.saveSettings.mock.calls[0][0];
    expect(payload.default_upstream_key).toBe('new-secret');
    expect(payload.check_model_key).toBeUndefined();
    expect(payload.default_mcp_key).toBeUndefined();
    expect(payload.default_upstream_model).toBe('deepseek-chat');
  });

  it('通过网页表单创建模型路由，不需要手编辑 JSON 或环境变量', async () => {
    api.getSettings.mockResolvedValue(settings);
    api.getGatewayRoutes.mockResolvedValue({ items: [] });
    api.createGatewayRoute.mockResolvedValue(route);
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByDisplayValue('deepseek-chat');

    await user.click(screen.getByRole('button', { name: '添加路由规则' }));
    const editor = screen.getByTestId('route-editor');
    await user.type(within(editor).getByLabelText('规则名称'), 'Agnes 主路由');
    await user.type(within(editor).getByLabelText('请求模型匹配', { exact: false }), 'agnes-*');
    await user.type(within(editor).getByLabelText('目标模型'), 'agnes-3.0-flash');
    await user.clear(within(editor).getByLabelText('路由上游 Base URL'));
    await user.type(within(editor).getByLabelText('路由上游 Base URL'), 'https://apihub.agnes-ai.com/v1');
    await user.click(within(editor).getByRole('button', { name: '创建规则' }));

    await waitFor(() => expect(api.createGatewayRoute).toHaveBeenCalled());
    expect(api.createGatewayRoute.mock.calls[0][0]).toMatchObject({
      name: 'Agnes 主路由', match_model: 'agnes-*', target_model: 'agnes-3.0-flash',
    });
  });

  it('编辑路由时可显式清除已保存密钥，而不是把留空当作保留', async () => {
    api.getSettings.mockResolvedValue(settings);
    api.getGatewayRoutes.mockResolvedValue({ items: [route] });
    api.updateGatewayRoute.mockResolvedValue({ ...route, api_key_configured: false });
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByDisplayValue('deepseek-chat');

    await user.click(screen.getByRole('button', { name: '编辑路由 Agnes 主路由' }));
    const editor = screen.getByTestId('route-editor');
    await user.click(within(editor).getByRole('button', { name: '清除已保存密钥' }));
    expect(within(editor).getByText('已选择清除密钥')).toBeInTheDocument();
    const invalidFields = Array.from(editor.querySelectorAll('input'))
      .filter((input) => !input.checkValidity())
      .map((input) => ({ label: input.labels?.[0]?.textContent, value: input.value, message: input.validationMessage }));
    expect(invalidFields).toEqual([]);
    await user.click(within(editor).getByRole('button', { name: '保存规则' }));

    await waitFor(() => expect(api.updateGatewayRoute).toHaveBeenCalled());
    expect(api.updateGatewayRoute.mock.calls[0][0]).toBe('route-1');
    expect(api.updateGatewayRoute.mock.calls[0][1].api_key).toBe('');
  });
});
