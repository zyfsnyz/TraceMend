import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  Activity, AlertCircle, ArrowRight, Bot, CheckCircle2, CircleDollarSign,
  KeyRound, Network, Plus, PlugZap, RefreshCw, RotateCcw, Route, Save, ServerCog,
  Settings2, ShieldCheck, Trash2, Wrench, X,
} from 'lucide-react';
import {
  createGatewayRoute, deleteGatewayRoute, getGatewayRoutes, getSettings,
  resetGatewayRoute, saveSettings, testConnection, updateGatewayRoute,
} from '../api/client';
import type {
  AppSettings, ConnectionTestResult, GatewayRoute, GatewayRouteInput, SettingsUpdate,
} from '../api/types';
import { CopyButton, EmptyState, Spinner } from '../components/ui';

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="block"><span className="label">{label}</span>{children}{hint && <span className="mt-1.5 block text-[11px] leading-4 text-slate-400">{hint}</span>}</label>;
}

function SecretField({ label, configured, value, onChange, onClear }: {
  label: string; configured: boolean; value: string; onChange: (value: string) => void; onClear: () => void;
}) {
  return <div>
    <div className="mb-1.5 flex items-center justify-between gap-2"><span className="text-xs font-medium text-slate-600">{label}</span><span className={`badge ${configured ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-slate-200 bg-slate-50 text-slate-400'}`}>{configured ? '已配置' : '未配置'}</span></div>
    <input aria-label={label} className="input w-full font-mono" type="password" autoComplete="new-password" spellCheck={false} value={value} onChange={(event) => onChange(event.target.value)} placeholder={configured ? '留空保持不变，输入新值则替换' : '输入密钥，仅保存到本地 .env'} />
    {configured && <button type="button" className="mt-1.5 text-[11px] text-slate-500 hover:text-red-600" onClick={onClear}>清除已保存密钥</button>}
  </div>;
}

function SectionHeader({ icon: Icon, title, description, action }: {
  icon: typeof ServerCog; title: string; description: string; action?: ReactNode;
}) {
  return <div className="panel-header"><div className="flex items-start gap-3"><span className="mt-0.5 rounded-lg border border-slate-200 bg-slate-50 p-2 text-slate-500"><Icon size={17} /></span><div><h2 className="panel-title">{title}</h2><p className="mt-1 text-[11px] leading-4 text-slate-500">{description}</p></div></div>{action}</div>;
}

function emptyRoute(base: string): GatewayRouteInput {
  return {
    name: '', match_model: '', target_model: '', upstream_base: base, api_key: '',
    priority: 100, enabled: true, input_price_per_million: 0, output_price_per_million: 0,
  };
}

export function SettingsPage() {
  const [form, setForm] = useState<AppSettings | null>(null);
  const [routes, setRoutes] = useState<GatewayRoute[]>([]);
  const [secrets, setSecrets] = useState({ default_upstream_key: '', default_mcp_key: '', check_model_key: '' });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState('');
  const [routeBusy, setRouteBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [routeForm, setRouteForm] = useState<GatewayRouteInput>(emptyRoute(''));
  const [clearRouteKey, setClearRouteKey] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [settingsData, routeData] = await Promise.all([getSettings(), getGatewayRoutes()]);
      setForm(settingsData); setRoutes(routeData.items);
      setRouteForm((current) => ({ ...current, upstream_base: current.upstream_base || settingsData.default_upstream_base }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : '未知错误'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const update = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setForm((current) => current ? { ...current, [key]: value } : current);
  };
  const updateSecret = (key: keyof typeof secrets, value: string) => setSecrets((current) => ({ ...current, [key]: value }));

  const persist = useCallback(async (): Promise<AppSettings | null> => {
    if (!form) return null;
    const payload: SettingsUpdate = {
      default_upstream_base: form.default_upstream_base,
      default_upstream_model: form.default_upstream_model,
      default_mcp_upstream: form.default_mcp_upstream,
      enable_llm_check: form.enable_llm_check,
      check_model_base: form.check_model_base,
      check_model_name: form.check_model_name,
      check_model_timeout: form.check_model_timeout,
      gateway_daily_token_budget: form.gateway_daily_token_budget,
      gateway_failure_threshold: form.gateway_failure_threshold,
      gateway_cooldown_seconds: form.gateway_cooldown_seconds,
      default_input_price_per_million: form.default_input_price_per_million,
      default_output_price_per_million: form.default_output_price_per_million,
    };
    if (secrets.default_upstream_key) payload.default_upstream_key = secrets.default_upstream_key;
    if (secrets.default_mcp_key) payload.default_mcp_key = secrets.default_mcp_key;
    if (secrets.check_model_key) payload.check_model_key = secrets.check_model_key;
    const saved = await saveSettings(payload);
    setForm(saved); setSecrets({ default_upstream_key: '', default_mcp_key: '', check_model_key: '' });
    return saved;
  }, [form, secrets]);

  const handleSave = async () => {
    setSaving(true); setNotice(null);
    try { await persist(); setNotice({ ok: true, text: '设置已保存并立即生效，无需重启后端。' }); }
    catch (reason) { setNotice({ ok: false, text: reason instanceof Error ? reason.message : '保存失败' }); }
    finally { setSaving(false); }
  };

  const handleTest = async (kind: 'openai' | 'check' | 'mcp') => {
    setTesting(kind); setNotice(null);
    try {
      await persist();
      const result: ConnectionTestResult = await testConnection(kind);
      setNotice({ ok: true, text: `${result.message}${result.model ? `：${result.model}` : ''}` });
    } catch (reason) { setNotice({ ok: false, text: reason instanceof Error ? reason.message : '连接测试失败' }); }
    finally { setTesting(''); }
  };

  const clearSecret = async (field: keyof typeof secrets) => {
    setSaving(true); setNotice(null);
    try {
      const saved = await saveSettings({ [field]: '' });
      setForm(saved); setNotice({ ok: true, text: '已清除本地密钥配置。' });
    } catch (reason) { setNotice({ ok: false, text: reason instanceof Error ? reason.message : '清除失败' }); }
    finally { setSaving(false); }
  };

  const openEditor = (route?: GatewayRoute) => {
    if (!form) return;
    setEditingId(route?.id ?? null);
    setClearRouteKey(false);
    setRouteForm(route ? {
      name: route.name, match_model: route.match_model, target_model: route.target_model,
      upstream_base: route.upstream_base, api_key: '', priority: route.priority, enabled: route.enabled,
      input_price_per_million: route.input_price_per_million, output_price_per_million: route.output_price_per_million,
    } : emptyRoute(form.default_upstream_base));
    setEditorOpen(true); setNotice(null);
  };

  const submitRoute = async () => {
    setRouteBusy('editor'); setNotice(null);
    try {
      const payload = { ...routeForm };
      if (editingId && clearRouteKey && !payload.api_key) payload.api_key = '';
      else if (!payload.api_key) delete payload.api_key;
      if (editingId) await updateGatewayRoute(editingId, payload);
      else await createGatewayRoute(payload);
      setEditorOpen(false); setEditingId(null);
      setNotice({ ok: true, text: editingId ? '路由规则已更新。' : '路由规则已创建，新请求会按优先级匹配。' });
      await load();
    } catch (reason) { setNotice({ ok: false, text: reason instanceof Error ? reason.message : '路由保存失败' }); }
    finally { setRouteBusy(''); }
  };

  const mutateRoute = async (route: GatewayRoute, action: 'toggle' | 'reset' | 'delete') => {
    setRouteBusy(route.id); setNotice(null);
    try {
      if (action === 'toggle') await updateGatewayRoute(route.id, { enabled: !route.enabled });
      else if (action === 'reset') await resetGatewayRoute(route.id);
      else {
        if (!window.confirm(`确定删除路由“${route.name}”吗？本地 Trace 不受影响。`)) return;
        await deleteGatewayRoute(route.id);
      }
      setNotice({ ok: true, text: action === 'reset' ? '熔断器已复位。' : action === 'delete' ? '路由已删除。' : '路由状态已更新。' });
      await load();
    } catch (reason) { setNotice({ ok: false, text: reason instanceof Error ? reason.message : '操作失败' }); }
    finally { setRouteBusy(''); }
  };

  const origin = window.location.origin;
  const editingRouteHasKey = routes.find((route) => route.id === editingId)?.api_key_configured;
  const routeNumber = (field: keyof GatewayRouteInput, value: string) => setRouteForm((current) => ({ ...current, [field]: Number(value) }));

  return <div className="page-shell">
    <header className="page-header"><div><div className="section-kicker"><Settings2 size={13} />本地控制台</div><h1 className="page-title">连接与网关设置</h1><p className="page-subtitle">在网页中完成模型、Agent 与 MCP 工具连接；保存后立即生效，无需手改配置文件。</p></div><button type="button" className="btn-primary" onClick={() => void handleSave()} disabled={saving || loading || !form}><Save size={15} />{saving ? '正在保存' : '保存全部设置'}</button></header>

    <section className="mb-5 overflow-hidden rounded-xl border border-teal-200/70 bg-gradient-to-r from-teal-50 to-white"><div className="flex flex-wrap items-center gap-5 p-5"><div className="flex min-w-[240px] flex-1 items-start gap-3"><span className="rounded-lg border border-teal-200 bg-white p-2.5 text-teal-600 shadow-sm"><PlugZap size={19} /></span><div><p className="text-xs font-semibold text-teal-900">Agent 只需连接这一个地址</p><code className="mt-1.5 block font-mono text-sm font-semibold text-ink-900">{origin}/v1</code><p className="mt-1 text-[11px] text-teal-800/60">保存默认上游后，Agent 无需再逐个填写真实上游地址。</p></div></div><div className="flex flex-wrap gap-2"><CopyButton text={`${origin}/v1`} label="复制模型代理" /><CopyButton text={`${origin}/mcp`} label="复制 MCP 代理" /></div></div></section>

    {notice && <div className={`mb-5 flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm ${notice.ok ? 'border-teal-200 bg-teal-50 text-teal-800' : 'border-red-200 bg-red-50 text-red-700'}`} role={notice.ok ? 'status' : 'alert'}>{notice.ok ? <CheckCircle2 size={17} className="mt-0.5 shrink-0" /> : <AlertCircle size={17} className="mt-0.5 shrink-0" />}{notice.text}</div>}
    {error && <div className="panel mb-5 p-5 text-sm text-red-600" role="alert">{error}<button className="btn-secondary ml-3 h-8 text-xs" onClick={() => void load()}>重新加载</button></div>}
    {loading && !form ? <div className="panel"><Spinner label="正在读取本地连接配置" /></div> : form && <>
      <div className="mb-5 grid items-stretch gap-5 xl:grid-cols-2">
        <section className="panel flex h-full flex-col"><SectionHeader icon={ServerCog} title="默认模型连接" description="供未显式指定上游的 Agent 请求使用；客户端密钥优先于默认密钥。" /><div className="flex flex-1 flex-col gap-4 p-5">
          <Field label="OpenAI 兼容 Base URL" hint="例如 https://api.deepseek.com/v1 或 http://127.0.0.1:11434/v1"><input className="input w-full font-mono text-xs" value={form.default_upstream_base} onChange={(event) => update('default_upstream_base', event.target.value)} /></Field>
          <Field label="默认模型名称" hint="Agent 请求未提供 model 时自动补全。"><input className="input w-full font-mono" value={form.default_upstream_model} onChange={(event) => update('default_upstream_model', event.target.value)} placeholder="deepseek-chat" /></Field>
          <SecretField label="默认上游 API Key" configured={form.default_upstream_key_configured} value={secrets.default_upstream_key} onChange={(value) => updateSecret('default_upstream_key', value)} onClear={() => void clearSecret('default_upstream_key')} />
          <div className="grid gap-4 sm:grid-cols-2"><Field label="输入价格（USD / 百万 Token）"><input type="number" min="0" step="0.01" className="input w-full" value={form.default_input_price_per_million} onChange={(event) => update('default_input_price_per_million', Number(event.target.value))} /></Field><Field label="输出价格（USD / 百万 Token）"><input type="number" min="0" step="0.01" className="input w-full" value={form.default_output_price_per_million} onChange={(event) => update('default_output_price_per_million', Number(event.target.value))} /></Field></div>
          <div className="mt-auto flex justify-end border-t border-slate-100 pt-4"><button type="button" className="btn-secondary" onClick={() => void handleTest('openai')} disabled={testing !== ''}><Activity size={14} className={testing === 'openai' ? 'animate-pulse' : ''} />{testing === 'openai' ? '正在测试' : '保存并测试连接'}</button></div>
        </div></section>

        <section className="panel flex h-full flex-col"><SectionHeader icon={ShieldCheck} title="二阶幻觉检测模型" description="仅在你主动点击 Trace 的 LLM 检测时调用，不影响免费规则。" /><div className="flex flex-1 flex-col gap-4 p-5">
          <label className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200 bg-slate-50/70 px-3.5 py-3"><span><span className="block text-xs font-medium text-slate-700">启用二阶 LLM 检测</span><span className="mt-1 block text-[11px] text-slate-400">关闭时不会请求检测模型</span></span><input type="checkbox" className="h-4 w-4 accent-teal-600" checked={form.enable_llm_check} onChange={(event) => update('enable_llm_check', event.target.checked)} /></label>
          <Field label="检测模型 Base URL"><input className="input w-full font-mono text-xs" value={form.check_model_base} onChange={(event) => update('check_model_base', event.target.value)} /></Field>
          <div className="grid gap-4 sm:grid-cols-[1fr_120px]"><Field label="检测模型名称"><input className="input w-full font-mono" value={form.check_model_name} onChange={(event) => update('check_model_name', event.target.value)} /></Field><Field label="超时（秒）"><input type="number" min="1" max="300" className="input w-full" value={form.check_model_timeout} onChange={(event) => update('check_model_timeout', Number(event.target.value))} /></Field></div>
          <SecretField label="检测模型 API Key" configured={form.check_model_key_configured} value={secrets.check_model_key} onChange={(value) => updateSecret('check_model_key', value)} onClear={() => void clearSecret('check_model_key')} />
          <div className="mt-auto flex justify-end border-t border-slate-100 pt-4"><button type="button" className="btn-secondary" onClick={() => void handleTest('check')} disabled={testing !== ''}><Bot size={14} />{testing === 'check' ? '正在测试' : '保存并测试连接'}</button></div>
        </div></section>

        <section className="panel flex h-full flex-col"><SectionHeader icon={Wrench} title="MCP 工具连接" description="配置后，MCP 客户端只需连接 AgentTrace /mcp。" /><div className="flex flex-1 flex-col gap-4 p-5">
          <Field label="默认 MCP 完整 URL" hint="包含服务路径，例如 http://127.0.0.1:9000/mcp"><input className="input w-full font-mono text-xs" value={form.default_mcp_upstream} onChange={(event) => update('default_mcp_upstream', event.target.value)} placeholder="http://127.0.0.1:9000/mcp" /></Field>
          <SecretField label="MCP API Key（可选）" configured={form.default_mcp_key_configured} value={secrets.default_mcp_key} onChange={(value) => updateSecret('default_mcp_key', value)} onClear={() => void clearSecret('default_mcp_key')} />
          <div className="mt-auto flex justify-end border-t border-slate-100 pt-4"><button type="button" className="btn-secondary" onClick={() => void handleTest('mcp')} disabled={testing !== ''}><Network size={14} />{testing === 'mcp' ? '正在初始化' : '保存并测试 initialize'}</button></div>
        </div></section>

        <section className="panel flex h-full flex-col"><SectionHeader icon={CircleDollarSign} title="预算与失败熔断" description="轻量本地网关策略；0  Token 预算表示不限制。" /><div className="grid flex-1 gap-4 p-5 sm:grid-cols-3">
          <Field label="每日 Token 预算" hint="达到后在调用上游前拦截"><input type="number" min="0" className="input w-full" value={form.gateway_daily_token_budget} onChange={(event) => update('gateway_daily_token_budget', Number(event.target.value))} /></Field>
          <Field label="连续失败阈值" hint="网络错误、408、429、5xx"><input type="number" min="1" max="20" className="input w-full" value={form.gateway_failure_threshold} onChange={(event) => update('gateway_failure_threshold', Number(event.target.value))} /></Field>
          <Field label="熔断冷却（秒）" hint="冷却后自动允许重试"><input type="number" min="1" max="3600" className="input w-full" value={form.gateway_cooldown_seconds} onChange={(event) => update('gateway_cooldown_seconds', Number(event.target.value))} /></Field>
          <div className="rounded-lg border border-blue-100 bg-blue-50/70 p-3 text-[11px] leading-5 text-blue-700 sm:col-span-3">失败切换只发生在收到上游响应之前。SSE 流式输出已开始后，为保持响应一致性不会切换模型。</div>
        </div></section>
      </div>

      <section className="panel"><SectionHeader icon={Route} title="模型路由与备用链路" description="按请求模型名称匹配，数字越小优先级越高；支持 * 通配符。" action={<button type="button" className="btn-primary h-8 text-xs" onClick={() => openEditor()}><Plus size={14} />添加路由规则</button>} />
        {editorOpen && <form data-testid="route-editor" className="border-b border-slate-200 bg-slate-50/70 p-5" onSubmit={(event) => { event.preventDefault(); void submitRoute(); }}><div className="mb-4 flex items-center justify-between"><div><h3 className="text-sm font-semibold text-ink-900">{editingId ? '编辑路由规则' : '创建路由规则'}</h3><p className="mt-1 text-[11px] text-slate-400">多条规则匹配同一模型时，失败会按优先级尝试下一条。</p></div><button type="button" className="icon-button h-8 w-8" aria-label="关闭路由编辑" onClick={() => setEditorOpen(false)}><X size={16} /></button></div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4"><Field label="规则名称"><input required className="input w-full" value={routeForm.name} onChange={(event) => setRouteForm({ ...routeForm, name: event.target.value })} placeholder="例如：DeepSeek 主路由" /></Field><Field label="请求模型匹配" hint="如 gpt-4*、deepseek-* 或 *"><input required className="input w-full font-mono" value={routeForm.match_model} onChange={(event) => setRouteForm({ ...routeForm, match_model: event.target.value })} /></Field><Field label="目标模型"><input required className="input w-full font-mono" value={routeForm.target_model} onChange={(event) => setRouteForm({ ...routeForm, target_model: event.target.value })} /></Field><Field label="优先级"><input type="number" min="0" className="input w-full" value={routeForm.priority} onChange={(event) => routeNumber('priority', event.target.value)} /></Field></div>
          <div className="mt-4 grid gap-4 md:grid-cols-2"><Field label="路由上游 Base URL"><input required className="input w-full font-mono text-xs" value={routeForm.upstream_base} onChange={(event) => setRouteForm({ ...routeForm, upstream_base: event.target.value })} /></Field><Field label="路由 API Key（可选）" hint={clearRouteKey ? '保存规则后将清除已保存密钥；输入新值则会改为替换。' : editingId ? '留空保留原密钥；输入新值替换。' : '只写入本地 .env，不进入数据库。'}><input type="password" autoComplete="new-password" className="input w-full font-mono" value={routeForm.api_key ?? ''} onChange={(event) => setRouteForm({ ...routeForm, api_key: event.target.value })} /></Field></div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Field label="输入价格 / 百万 Token"><input type="number" min="0" step="any" className="input w-full" value={routeForm.input_price_per_million} onChange={(event) => routeNumber('input_price_per_million', event.target.value)} /></Field><Field label="输出价格 / 百万 Token"><input type="number" min="0" step="any" className="input w-full" value={routeForm.output_price_per_million} onChange={(event) => routeNumber('output_price_per_million', event.target.value)} /></Field><label className="flex items-center gap-2 self-end pb-2 text-xs text-slate-600"><input type="checkbox" className="h-4 w-4 accent-teal-600" checked={routeForm.enabled} onChange={(event) => setRouteForm({ ...routeForm, enabled: event.target.checked })} />创建后立即启用</label></div>
          <div className="mt-5 flex justify-end gap-2">{editingRouteHasKey && <button type="button" className="btn-secondary mr-auto text-red-600" disabled={clearRouteKey} onClick={() => { setClearRouteKey(true); setRouteForm({ ...routeForm, api_key: '' }); }}>{clearRouteKey ? '已选择清除密钥' : '清除已保存密钥'}</button>}<button type="button" className="btn-secondary" onClick={() => setEditorOpen(false)}>取消</button><button type="submit" className="btn-primary" disabled={routeBusy === 'editor'}><Save size={14} />{editingId ? '保存规则' : '创建规则'}</button></div>
        </form>}
        {routes.length ? <div className="divide-y divide-slate-100">{routes.map((route) => <article key={route.id} className="group px-5 py-4 transition-colors hover:bg-slate-50/60"><div className="flex flex-wrap items-start gap-3"><span className={`mt-1 rounded-lg p-2 ${route.circuit.status === 'open' ? 'bg-red-50 text-red-600' : route.enabled ? 'bg-teal-50 text-teal-600' : 'bg-slate-100 text-slate-400'}`}><Route size={17} /></span><div className="min-w-[220px] flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold text-ink-900">{route.name}</h3><span className={`badge ${route.enabled ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-slate-200 bg-slate-100 text-slate-500'}`}>{route.enabled ? '已启用' : '已停用'}</span>{route.circuit.status === 'open' && <span className="badge border-red-200 bg-red-50 text-red-700">熔断中 · {Math.ceil(route.circuit.open_remaining_seconds)}s</span>}</div><div className="mt-2 flex flex-wrap items-center gap-2 font-mono text-xs"><code className="rounded bg-slate-100 px-2 py-1 text-slate-600">{route.match_model}</code><ArrowRight size={12} className="text-slate-300" /><code className="rounded bg-teal-50 px-2 py-1 text-teal-700">{route.target_model}</code></div><p className="mt-2 break-all font-mono text-[11px] text-slate-400">{route.upstream_base}</p></div><div className="grid grid-cols-3 gap-4 text-right text-[11px] text-slate-400"><div><span className="block">优先级</span><strong className="mt-1 block font-mono text-xs font-medium text-slate-600">{route.priority}</strong></div><div><span className="block">输入 / 输出价格</span><strong className="mt-1 block font-mono text-xs font-medium text-slate-600">${route.input_price_per_million} / ${route.output_price_per_million}</strong></div><div><span className="block">连续失败</span><strong className="mt-1 block font-mono text-xs font-medium text-slate-600">{route.circuit.failures}</strong></div></div><div className="flex w-full flex-wrap justify-end gap-1.5 xl:w-auto"><button type="button" className="btn-secondary h-8 px-2.5 text-xs" aria-label={`编辑路由 ${route.name}`} onClick={() => openEditor(route)}>编辑</button><button type="button" className="btn-secondary h-8 px-2.5 text-xs" disabled={routeBusy === route.id} onClick={() => void mutateRoute(route, 'toggle')}>{route.enabled ? '停用' : '启用'}</button>{route.circuit.status === 'open' && <button type="button" className="btn-secondary h-8 px-2.5 text-xs" onClick={() => void mutateRoute(route, 'reset')}><RotateCcw size={12} />复位</button>}<button type="button" className="icon-button h-8 w-8 hover:bg-red-50 hover:text-red-600" aria-label={`删除 ${route.name}`} onClick={() => void mutateRoute(route, 'delete')}><Trash2 size={14} /></button></div></div></article>)}</div> : <EmptyState title="尚未创建路由规则" description="不创建规则时使用上方默认上游。添加多条同匹配规则即可形成主备链路。" action={<button type="button" className="btn-secondary" onClick={() => openEditor()}><Plus size={14} />创建第一条规则</button>} />}
      </section>
      <footer className="mt-5 flex items-center justify-between text-[11px] text-slate-400"><span className="flex items-center gap-1.5"><KeyRound size={12} />密钥仅写入本地 .env，数据库和 API 响应不保存原值。</span><button type="button" className="flex items-center gap-1.5 hover:text-teal-700" onClick={() => void load()}><RefreshCw size={12} />刷新状态</button></footer>
    </>}
  </div>;
}
