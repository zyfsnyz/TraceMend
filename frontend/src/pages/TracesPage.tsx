import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, CalendarDays, ChevronRight, Filter, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { deleteTrace, getTraces } from '../api/client';
import type { Trace, TraceListResponse } from '../api/types';
import { dateInputFromIso, formatDuration, formatNumber, formatTime, localDayEnd, localDayStart, shortId } from '../lib/format';
import { EmptyState, ErrorState, Modal, Spinner, StatusBadge } from '../components/ui';

export function TracesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [data, setData] = useState<TraceListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState(searchParams.get('q') ?? '');
  const [tag, setTag] = useState(searchParams.get('tag') ?? '');
  const [from, setFrom] = useState(dateInputFromIso(searchParams.get('from')));
  const [to, setTo] = useState(dateInputFromIso(searchParams.get('to')));
  const [showAdvanced, setShowAdvanced] = useState(Boolean(searchParams.get('tag') || searchParams.get('from') || searchParams.get('to')));
  const [deleting, setDeleting] = useState<Trace | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const requestRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const pageSize = Number(searchParams.get('page_size')) || 20;

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true); setError('');
    try {
      const result = await getTraces({
        q: searchParams.get('q') ?? '', status: searchParams.get('status') ?? '', risk: searchParams.get('risk') ?? '',
        tag: searchParams.get('tag') ?? '', from: searchParams.get('from') ?? '', to: searchParams.get('to') ?? '', page, page_size: pageSize,
      }, controller.signal);
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setData(result);
    } catch (reason) {
      if (requestId !== requestRef.current || controller.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : '未知错误');
    }
    finally { if (requestId === requestRef.current) setLoading(false); }
  }, [page, pageSize, searchParams]);
  useEffect(() => {
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  const updateFilters = (updates: Record<string, string | number>) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => String(value) ? next.set(key, String(value)) : next.delete(key));
    if (!('page' in updates)) next.set('page', '1');
    setSearchParams(next);
  };
  const reset = () => { setQ(''); setTag(''); setFrom(''); setTo(''); setSearchParams({}); };
  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true); setError('');
    try {
      await deleteTrace(deleting.id); setDeleting(null);
      if (data?.items.length === 1 && page > 1) updateFilters({ page: page - 1 }); else await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败'); setDeleting(null); }
    finally { setDeleteBusy(false); }
  };
  const pageNumbers = useMemo(() => {
    const pages = data?.pages ?? 0; const start = Math.max(1, Math.min(page - 2, pages - 4)); const end = Math.min(pages, start + 4);
    return Array.from({ length: Math.max(0, end - start + 1) }, (_, index) => start + index);
  }, [data?.pages, page]);
  const hasFilters = ['q', 'status', 'risk', 'tag', 'from', 'to'].some((key) => searchParams.get(key));

  return <div className="page-shell">
    <header className="page-header"><div><h1 className="page-title">审计链路</h1><p className="page-subtitle">检索完整 Agent 执行记录，深入每个 Span 的输入、输出与风险证据。</p></div><button className="btn-secondary" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新</button></header>
    <section className="panel mb-5 border-slate-200/70 bg-white/85 p-4">
      <form className="flex flex-wrap items-center gap-2.5" onSubmit={(event) => { event.preventDefault(); updateFilters({ q: q.trim(), tag: tag.trim(), from: localDayStart(from), to: localDayEnd(to) }); }}>
        <div className="relative min-w-[220px] flex-1"><Search className="absolute left-3 top-3 text-slate-400" size={16} /><input className="input w-full pl-9" value={q} onChange={(event) => setQ(event.target.value)} placeholder="搜索链路名称、ID、模型或来源" aria-label="搜索链路" /></div>
        <select className="input w-32" aria-label="状态筛选" value={searchParams.get('status') ?? ''} onChange={(event) => updateFilters({ status: event.target.value })}><option value="">全部状态</option><option value="completed">已完成</option><option value="running">运行中</option><option value="error">异常</option></select>
        <select className="input w-32" aria-label="风险筛选" value={searchParams.get('risk') ?? ''} onChange={(event) => updateFilters({ risk: event.target.value })}><option value="">全部风险</option><option value="any">仅看有风险</option><option value="high">高风险</option><option value="medium">中风险</option><option value="low">低风险</option><option value="none">无风险</option></select>
        <button className="btn-primary" type="submit"><Search size={14} />查询</button>
        <button className={`btn-secondary ${showAdvanced ? 'border-teal-200 text-teal-700' : ''}`} type="button" onClick={() => setShowAdvanced((value) => !value)}><Filter size={14} />更多</button>
        {hasFilters && <button className="icon-button h-9 w-9" type="button" aria-label="清空筛选" title="清空筛选" onClick={reset}><X size={16} /></button>}
      </form>
      {showAdvanced && <div className="mt-3 grid gap-3 border-t border-slate-100 pt-3 sm:grid-cols-2 xl:grid-cols-4"><div><label className="label" htmlFor="trace-tag">标签</label><input id="trace-tag" className="input w-full" value={tag} onChange={(event) => setTag(event.target.value)} placeholder="例如 production" /></div><div><label className="label" htmlFor="trace-from">开始日期</label><div className="relative"><CalendarDays className="pointer-events-none absolute left-3 top-3 text-slate-400" size={14} /><input id="trace-from" type="date" className="input w-full pl-9" value={from} onChange={(event) => setFrom(event.target.value)} /></div></div><div><label className="label" htmlFor="trace-to">结束日期</label><div className="relative"><CalendarDays className="pointer-events-none absolute left-3 top-3 text-slate-400" size={14} /><input id="trace-to" type="date" className="input w-full pl-9" value={to} onChange={(event) => setTo(event.target.value)} /></div></div><div className="flex items-end"><button className="btn-secondary w-full" onClick={() => updateFilters({ q: q.trim(), tag: tag.trim(), from: localDayStart(from), to: localDayEnd(to) })}>应用高级筛选</button></div></div>}
    </section>
    {error && <div className="panel mb-4"><ErrorState message={error} onRetry={() => void load()} /></div>}
    <section className="panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5"><p className="text-xs text-slate-500">共 <span className="font-semibold text-ink-900">{formatNumber(data?.total ?? 0)}</span> 条链路{hasFilters && '符合当前筛选'}</p><select className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-600" aria-label="每页条数" value={pageSize} onChange={(event) => updateFilters({ page_size: event.target.value })}><option value={10}>10 条 / 页</option><option value={20}>20 条 / 页</option><option value={50}>50 条 / 页</option></select></div>
      {loading && !data ? <Spinner label="正在检索审计链路" /> : data?.items.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1050px] border-collapse"><thead className="bg-slate-50/70"><tr><th className="table-heading">链路</th><th className="table-heading">状态</th><th className="table-heading">来源 / 模型</th><th className="table-heading">开始时间</th><th className="table-heading text-right">耗时</th><th className="table-heading text-right">Token</th><th className="table-heading">风险</th><th className="table-heading w-20 text-right">操作</th></tr></thead><tbody className="divide-y divide-slate-100">{data.items.map((trace) => <tr key={trace.id} className="group cursor-pointer transition-colors hover:bg-teal-50/35" onClick={() => navigate(`/traces/${trace.id}`)}>
        <td className="table-cell max-w-[280px]"><Link to={`/traces/${trace.id}`} className="block truncate font-medium text-ink-900 group-hover:text-teal-700" onClick={(event) => event.stopPropagation()}>{trace.name}</Link><div className="mt-1 flex items-center gap-2"><code className="text-[10px] text-slate-400">{shortId(trace.id)}</code>{trace.tags?.slice(0, 2).map((item) => <span key={item} className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-500">{item}</span>)}</div></td>
        <td className="table-cell"><StatusBadge status={trace.status} /></td><td className="table-cell"><p className="text-xs text-slate-600">{trace.source}</p><p className="mt-1 max-w-40 truncate text-[11px] text-slate-400">{trace.model || '未指定模型'}</p></td><td className="table-cell whitespace-nowrap text-xs text-slate-500">{formatTime(trace.started_at)}</td><td className="table-cell text-right font-mono text-xs text-slate-600">{formatDuration(trace.duration_ms)}</td><td className="table-cell text-right font-mono text-xs text-slate-600">{formatNumber(trace.total_tokens)}</td><td className="table-cell">{trace.risk_count ? <span className="badge border-orange-200 bg-orange-50 text-orange-700">{trace.risk_count} 项</span> : <span className="text-xs text-slate-300">—</span>}</td><td className="table-cell"><div className="flex justify-end gap-1"><button className="icon-button h-8 w-8 hover:bg-red-50 hover:text-red-600" aria-label={`删除 ${trace.name}`} title="删除链路" onClick={(event) => { event.stopPropagation(); setDeleting(trace); }}><Trash2 size={14} /></button><ChevronRight size={15} className="mt-2 text-slate-300" /></div></td>
      </tr>)}</tbody></table></div> : <EmptyState title="没有匹配的链路" description={hasFilters ? '尝试调整搜索关键词或清除筛选条件。' : '尚未接入审计数据，请先完成 Agent 接入。'} action={hasFilters ? <button className="btn-secondary" onClick={reset}>清空筛选</button> : <Link to="/guide" className="btn-primary">查看接入指南</Link>} />}
      {data && data.pages > 1 && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3.5"><span className="text-xs text-slate-400">第 {page} / {data.pages} 页</span><div className="flex items-center gap-1"><button className="btn-secondary h-8 px-2.5 text-xs" disabled={page <= 1} onClick={() => updateFilters({ page: page - 1 })}><ArrowLeft size={13} />上一页</button>{pageNumbers.map((number) => <button key={number} className={`h-8 min-w-8 rounded-md px-2 text-xs font-medium ${number === page ? 'bg-ink-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`} onClick={() => updateFilters({ page: number })}>{number}</button>)}<button className="btn-secondary h-8 px-2.5 text-xs" disabled={page >= data.pages} onClick={() => updateFilters({ page: page + 1 })}>下一页<ArrowRight size={13} /></button></div></div>}
    </section>
    {deleting && <Modal title="删除审计链路" onClose={() => !deleteBusy && setDeleting(null)}><p className="text-sm leading-6 text-slate-600">确定删除“<span className="font-medium text-ink-900">{deleting.name}</span>”吗？关联 Span、风险发现与评论将一并删除，此操作无法撤销。</p><div className="mt-6 flex justify-end gap-2"><button className="btn-secondary" disabled={deleteBusy} onClick={() => setDeleting(null)}>取消</button><button className="btn border-red-600 bg-red-600 text-white hover:bg-red-700" disabled={deleteBusy} onClick={() => void confirmDelete()}>{deleteBusy ? '正在删除…' : '确认删除'}</button></div></Modal>}
  </div>;
}
