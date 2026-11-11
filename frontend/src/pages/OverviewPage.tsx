import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Clock3, Coins, GitBranch, RefreshCw, ShieldAlert, TriangleAlert } from 'lucide-react';
import { getStats, getTraces } from '../api/client';
import type { Stats, TraceListResponse } from '../api/types';
import { formatDate, formatDuration, formatNumber, formatTime, shortId } from '../lib/format';
import { EmptyState, ErrorState, Spinner, StatusBadge } from '../components/ui';

function DailyTrend({ stats }: { stats: Stats }) {
  const data = stats.daily ?? [];
  if (!data.length) return <EmptyState title="暂无趋势数据" description="接入 Agent 后，这里会呈现每日链路与异常变化。" />;
  const width = 760; const height = 210; const left = 34; const top = 18; const bottom = 34;
  const chartHeight = height - top - bottom; const chartWidth = width - left - 14;
  const max = Math.max(1, ...data.map((day) => day.traces));
  const slot = chartWidth / Math.max(data.length, 1); const barWidth = Math.max(3, Math.min(18, slot * 0.55));
  const points = data.map((day, index) => `${left + index * slot + slot / 2},${top + chartHeight - (day.errors / max) * chartHeight}`).join(' ');
  return <div className="overflow-x-auto px-3 pb-3 pt-2"><svg className="min-w-[600px]" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="每日链路数与异常数趋势图">
    {[0, .5, 1].map((ratio) => <g key={ratio}><line x1={left} x2={width - 14} y1={top + chartHeight * ratio} y2={top + chartHeight * ratio} stroke="#e8edf2" /><text x={left - 8} y={top + chartHeight * ratio + 4} textAnchor="end" fontSize="10" fill="#94a3b8">{Math.round(max * (1 - ratio))}</text></g>)}
    {data.map((day, index) => {
      const x = left + index * slot + (slot - barWidth) / 2; const barHeight = Math.max(day.traces ? 2 : 0, (day.traces / max) * chartHeight);
      return <g key={day.date}><rect x={x} y={top + chartHeight - barHeight} width={barWidth} height={barHeight} rx={Math.min(4, barWidth / 2)} fill="#82d9c7"><title>{day.date}：{day.traces} 条链路，{day.errors} 条异常</title></rect>{(data.length <= 14 || index % Math.ceil(data.length / 10) === 0) && <text x={x + barWidth / 2} y={height - 10} textAnchor="middle" fontSize="10" fill="#94a3b8">{formatDate(day.date)}</text>}</g>;
    })}
    {data.length > 1 && <polyline points={points} fill="none" stroke="#f59e0b" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />}
  </svg><div className="mt-1 flex justify-center gap-5 text-[11px] text-slate-500"><span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-[#82d9c7]" />链路数</span><span className="flex items-center gap-1.5"><i className="h-0.5 w-3 bg-amber-500" />异常数</span></div></div>;
}

export function OverviewPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<TraceListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const [statsData, recentData] = await Promise.all([getStats(30), getTraces({ page: 1, page_size: 6 })]);
      setStats(statsData); setRecent(recentData);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '未知错误'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const cards = stats ? [
    { label: '审计链路', value: formatNumber(stats.total_traces), note: `${formatNumber(stats.completed_traces)} 条已完成`, icon: GitBranch, tone: 'bg-teal-50 text-teal-600' },
    { label: '风险链路', value: formatNumber(stats.risk_traces), note: `${formatNumber(stats.total_findings)} 项风险发现`, icon: ShieldAlert, tone: 'bg-orange-50 text-orange-600' },
    { label: '异常链路', value: formatNumber(stats.error_traces), note: `占比 ${stats.total_traces ? ((stats.error_traces / stats.total_traces) * 100).toFixed(1) : '0.0'}%`, icon: TriangleAlert, tone: 'bg-red-50 text-red-600' },
    { label: '总 Token', value: formatNumber(stats.total_tokens), note: `${formatNumber(stats.tool_calls)} 次工具调用`, icon: Coins, tone: 'bg-blue-50 text-blue-600' },
    { label: '平均耗时', value: formatDuration(stats.avg_duration_ms), note: '单条链路端到端', icon: Clock3, tone: 'bg-violet-50 text-violet-600' },
  ] : [];

  return <div className="mx-auto max-w-[1560px] p-5 md:p-7 xl:p-8">
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4"><div><div className="mb-2 flex items-center gap-2 text-xs font-medium text-teal-700"><span className="h-1.5 w-1.5 rounded-full bg-teal-500" />审计工作区</div><h1 className="page-title">运行总览</h1><p className="page-subtitle">观测 Agent 行为，定位风险证据，回溯每一次关键决策。</p></div><div className="flex items-center gap-2"><span className="badge border-slate-200 bg-white px-3 py-1.5 text-slate-500">近 30 天</span><button className="btn-secondary" onClick={() => void load()} disabled={loading}><RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新</button></div></header>
    {error && <div className="panel mb-5"><ErrorState message={error} onRetry={() => void load()} /></div>}
    {loading && !stats ? <div className="panel"><Spinner /></div> : stats && <>
      <section aria-label="关键指标" className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{cards.map(({ label, value, note, icon: Icon, tone }) => <div className="panel p-4.5 p-5" key={label}><div className="flex items-start justify-between"><div><p className="text-xs font-medium text-slate-500">{label}</p><p className="mt-2 text-[26px] font-semibold tracking-tight text-ink-950">{value}</p></div><span className={`rounded-lg p-2 ${tone}`}><Icon size={18} /></span></div><p className="mt-2 text-[11px] text-slate-400">{note}</p></div>)}</section>
      <div className="mb-5 grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,.75fr)]">
        <section className="panel min-w-0"><div className="panel-header"><div><h2 className="panel-title">每日运行趋势</h2><p className="mt-1 text-[11px] text-slate-400">链路量与异常变化</p></div><Link className="text-xs font-medium text-teal-700 hover:text-teal-600" to="/traces">查看链路 <ArrowRight className="inline" size={12} /></Link></div><DailyTrend stats={stats} /></section>
        <section className="panel"><div className="panel-header"><div><h2 className="panel-title">模型分布</h2><p className="mt-1 text-[11px] text-slate-400">按审计链路数量统计</p></div></div><div className="p-5">{stats.models?.length ? <div className="space-y-5">{stats.models.map((model, index) => { const max = Math.max(...stats.models.map((item) => item.count), 1); return <div key={model.model}><div className="mb-2 flex items-center justify-between gap-3 text-xs"><span className="flex min-w-0 items-center gap-2 font-medium text-slate-700"><i className={`h-2 w-2 shrink-0 rounded-full ${['bg-teal-500','bg-blue-500','bg-violet-500','bg-amber-500','bg-slate-400'][index % 5]}`} /><span className="truncate" title={model.model}>{model.model || '未指定模型'}</span></span><span className="font-mono text-slate-500">{formatNumber(model.count)}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-teal-500" style={{ width: `${Math.max(2, model.count / max * 100)}%` }} /></div></div>; })}</div> : <EmptyState title="暂无模型数据" description="统计期内尚未记录模型调用。" />}</div></section>
      </div>
      <section className="panel"><div className="panel-header"><div><h2 className="panel-title">最近审计链路</h2><p className="mt-1 text-[11px] text-slate-400">按最近开始时间排序</p></div><Link className="btn-secondary h-8 text-xs" to="/traces">全部链路<ArrowRight size={13} /></Link></div>
        {recent?.items.length ? <div className="divide-y divide-slate-100">{recent.items.map((trace) => <Link to={`/traces/${trace.id}`} key={trace.id} className="group flex flex-wrap items-center gap-3 px-5 py-4 transition-colors hover:bg-slate-50/80"><span className={`h-2 w-2 shrink-0 rounded-full ${trace.risk_count ? 'bg-orange-500' : trace.status === 'error' ? 'bg-red-500' : 'bg-teal-500'}`} /><div className="min-w-[180px] flex-1"><div className="flex items-center gap-2"><p className="truncate text-sm font-medium text-ink-900 group-hover:text-teal-700">{trace.name}</p><code className="hidden text-[10px] text-slate-400 md:inline">{shortId(trace.id)}</code></div><p className="mt-1 text-[11px] text-slate-400">{trace.source} · {trace.model || '未指定模型'} · {formatTime(trace.started_at)}</p></div>{trace.risk_count > 0 && <span className="badge border-orange-200 bg-orange-50 text-orange-700">{trace.risk_count} 项风险</span>}<StatusBadge status={trace.status} /><span className="w-20 text-right font-mono text-xs text-slate-500">{formatDuration(trace.duration_ms)}</span><ArrowRight size={14} className="text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-teal-600" /></Link>)}</div> : <EmptyState title="还没有审计链路" description="通过 OpenAI SDK、LangChain 或自定义 Ingest 上报第一条链路。" action={<Link to="/guide" className="btn-primary">查看接入指南<ArrowRight size={14} /></Link>} />}
      </section>
    </>}
  </div>;
}
