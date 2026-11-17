import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowRight, RefreshCw, Search, ShieldAlert, ShieldCheck } from 'lucide-react';
import { getTrace, getTraces } from '../api/client';
import type { Finding, TraceListResponse } from '../api/types';
import { formatDuration, formatNumber, formatTime, shortId } from '../lib/format';
import { EmptyState, ErrorState, SeverityBadge, Spinner, StatusBadge } from '../components/ui';
import { stringifySafe } from '../lib/sensitive';

const severityOrder = ['critical', 'high', 'medium', 'low'];
function FindingSeverityBadges({ items }: { items: Finding[] }) {
  const present = severityOrder.filter((severity) => items.some((finding) => finding.severity === severity));
  return <>{present.map((severity) => <SeverityBadge key={severity} severity={severity} />)}</>;
}

export function RisksPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const risk = searchParams.get('risk') || 'any';
  const q = searchParams.get('q') || '';
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const [input, setInput] = useState(q);
  const [data, setData] = useState<TraceListResponse | null>(null);
  const [findingsByTrace, setFindingsByTrace] = useState<Record<string, Finding[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true); setError(''); setFindingsByTrace({});
    try {
      const list = await getTraces({ risk, q, page, page_size: 20 }, controller.signal);
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setData(list);
      const details = await Promise.all(
        list.items.map(async (trace) => [
          trace.id,
          (await getTrace(trace.id, controller.signal)).findings,
        ] as const),
      );
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setFindingsByTrace(Object.fromEntries(details));
    }
    catch (reason) {
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : '未知错误');
    }
    finally { if (requestId === requestRef.current) setLoading(false); }
  }, [risk, q, page]);
  useEffect(() => {
    void load();
    return () => abortRef.current?.abort();
  }, [load]);
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'page') next.set('page', '1');
    setSearchParams(next);
  };

  return <div className="page-shell">
    <header className="page-header"><div><div className="mb-2 flex items-center gap-2 text-xs font-medium text-orange-700"><ShieldAlert size={14} />风险证据工作区</div><h1 className="page-title">风险中心</h1><p className="page-subtitle">集中复核规则与模型检测结果，每项风险都可回到原始 Span 取证。</p></div><button className="btn-secondary" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新</button></header>
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-orange-200/70 bg-orange-50/60 px-4 py-3"><div className="min-w-[260px] flex-1"><div className="flex items-start gap-3"><span className="mt-0.5 rounded-lg border border-orange-100 bg-white p-1.5 text-orange-600"><ShieldAlert size={19} /></span><div><p className="text-sm font-semibold text-orange-900">风险优先，证据先行</p><p className="mt-1 text-xs leading-5 text-orange-700/80">免费规则持续检测敏感信息、异常工具行为等问题；可选 LLM 检测未配置时不影响规则审计。</p></div></div></div><Link to="/guide" className="btn-secondary h-8 border-orange-200 bg-white/80 text-xs text-orange-800 hover:bg-white">查看检测接入<ArrowRight size={14} /></Link></div>
    <section className="panel mb-4 flex flex-wrap items-center gap-3 p-4"><form className="relative min-w-[220px] flex-1" onSubmit={(event) => { event.preventDefault(); update('q', input.trim()); }}><Search className="absolute left-3 top-3 text-slate-400" size={16} /><input className="input w-full pl-9" aria-label="搜索风险链路" placeholder="搜索风险链路" value={input} onChange={(event) => setInput(event.target.value)} /></form><div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1">{[{ value: 'any', label: '全部风险' }, { value: 'high', label: '高风险' }, { value: 'medium', label: '中风险' }, { value: 'low', label: '低风险' }].map((item) => <button key={item.value} className={`h-8 rounded-md px-3 text-xs font-medium transition-colors ${risk === item.value ? 'bg-white text-ink-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`} onClick={() => update('risk', item.value)}>{item.label}</button>)}</div></section>
    {error && <div className="panel mb-4"><ErrorState message={error} onRetry={() => void load()} /></div>}
    <section className="panel overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">待复核风险链路</h2><p className="mt-1 text-[11px] text-slate-400">结果来自实时链路风险筛选，不生成演示记录</p></div><span className="badge border-orange-200 bg-orange-50 text-orange-700">{formatNumber(data?.total ?? 0)} 条</span></div>
      {loading && !data ? <Spinner label="正在汇总风险链路" /> : data?.items.length ? <div className="divide-y divide-slate-100">{data.items.map((trace) => <div key={trace.id} className={`flex flex-wrap items-center gap-4 border-l-[3px] px-5 py-3.5 transition-colors ${(findingsByTrace[trace.id] ?? []).some((finding) => finding.severity === 'high' || finding.severity === 'critical') ? 'border-l-red-400 bg-red-50/20 hover:bg-red-50/40' : 'border-l-transparent hover:bg-slate-50/70'}`}><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-600"><ShieldAlert size={17} /></span><div className="min-w-[200px] flex-1"><div className="flex flex-wrap items-center gap-2"><Link to={`/traces/${trace.id}#findings`} className="text-sm font-semibold text-ink-900 hover:text-teal-700">{stringifySafe(trace.name)}</Link><code className="text-[10px] text-slate-400">{shortId(trace.id)}</code><FindingSeverityBadges items={findingsByTrace[trace.id] ?? []} /></div><p className="mt-1.5 text-[11px] text-slate-400">{trace.source} · {trace.model || '未指定模型'} · {formatTime(trace.started_at)}</p></div><div className="flex items-center gap-4"><div className="rounded-lg border border-orange-100 bg-orange-50/80 px-3 py-1.5 text-right"><p className="text-sm font-semibold text-orange-700">{trace.risk_count} 项</p><p className="mt-0.5 text-[10px] text-slate-400">风险发现</p></div><div className="hidden text-right sm:block"><p className="font-mono text-xs text-slate-600">{formatDuration(trace.duration_ms)}</p><p className="mt-1 text-[10px] text-slate-400">{formatNumber(trace.total_tokens)} Token</p></div><StatusBadge status={trace.status} /><Link className="btn-secondary h-8 px-2.5 text-xs" to={`/traces/${trace.id}#findings`}>复核证据<ArrowRight size={12} /></Link></div></div>)}</div> : <EmptyState title="当前筛选下没有风险链路" description="可以切换风险级别，或前往接入指南上报更多 Agent 链路。" action={<span className="flex items-center gap-2 text-xs text-teal-700"><ShieldCheck size={15} />审计数据保持真实、可验证</span>} />}
      {data && data.pages > 1 && <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3"><button className="btn-secondary h-8 text-xs" disabled={page <= 1} onClick={() => update('page', String(page - 1))}>上一页</button><span className="text-xs text-slate-500">第 {page} / {data.pages} 页</span><button className="btn-secondary h-8 text-xs" disabled={page >= data.pages} onClick={() => update('page', String(page + 1))}>下一页</button></div>}
    </section>
  </div>;
}
