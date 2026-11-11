import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Clock3, Coins, Download, FileText, GitBranch, RefreshCw, ScanSearch, ShieldAlert } from 'lucide-react';
import { downloadReport, getTrace, llmCheckTrace, reanalyzeTrace } from '../api/client';
import { ApiError } from '../api/client';
import type { TraceDetail } from '../api/types';
import { formatDuration, formatFullTime, formatNumber, shortId } from '../lib/format';
import { ErrorState, JsonBlock, Spinner, StatusBadge } from '../components/ui';
import { TraceDAG } from '../components/TraceDAG';
import { ReplayBar } from '../components/ReplayBar';
import { SpanInspector } from '../components/SpanInspector';
import { TraceTimeline } from '../components/TraceTimeline';
import { FindingsPanel } from '../components/FindingsPanel';
import { CommentsPanel } from '../components/CommentsPanel';

export function TraceDetailPage() {
  const { traceId = '' } = useParams();
  const location = useLocation();
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [action, setAction] = useState('');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError('');
    try { setDetail(await getTrace(traceId)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '未知错误'); }
    finally { if (showLoading) setLoading(false); }
  }, [traceId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!detail) return;
    if (!selectedSpanId || !detail.spans.some((span) => span.id === selectedSpanId)) {
      const first = [...detail.spans].sort((a, b) => a.sequence - b.sequence)[0];
      setSelectedSpanId(first?.id ?? null);
    }
  }, [detail, selectedSpanId]);
  useEffect(() => {
    if (detail && location.hash === '#findings') window.setTimeout(() => document.getElementById('findings')?.scrollIntoView({ behavior: 'smooth' }), 100);
  }, [detail, location.hash]);

  const runRules = async () => {
    setAction('rules'); setNotice(null);
    try { await reanalyzeTrace(traceId); await load(false); setNotice({ tone: 'success', text: '规则检测已完成，风险结果已更新。' }); }
    catch (reason) { setNotice({ tone: 'error', text: reason instanceof Error ? reason.message : '重新检测失败' }); }
    finally { setAction(''); }
  };
  const runLlm = async () => {
    setAction('llm'); setNotice(null);
    try { await llmCheckTrace(traceId); await load(false); setNotice({ tone: 'success', text: 'LLM 检测已完成，结果已更新。' }); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) setNotice({ tone: 'info', text: '未配置，不影响免费规则。可继续使用规则检测与完整审计功能。' });
      else setNotice({ tone: 'error', text: reason instanceof Error ? reason.message : 'LLM 检测失败' });
    } finally { setAction(''); }
  };
  const exportReport = async (format: 'md' | 'pdf') => {
    setAction(format); setNotice(null);
    try { await downloadReport(traceId, format); setNotice({ tone: 'success', text: `${format.toUpperCase()} 审计报告已开始下载。` }); }
    catch (reason) { setNotice({ tone: 'error', text: reason instanceof Error ? reason.message : '报告导出失败' }); }
    finally { setAction(''); }
  };
  const locateSpan = (id: string) => {
    setSelectedSpanId(id);
    document.getElementById('dag')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  if (loading && !detail) return <div className="p-8"><div className="panel"><Spinner label="正在还原 Agent 执行现场" /></div></div>;
  if (!detail) return <div className="mx-auto max-w-4xl p-8"><Link to="/traces" className="btn-secondary mb-4"><ArrowLeft size={14} />返回链路列表</Link><div className="panel"><ErrorState message={error || '链路不存在'} onRetry={() => void load()} /></div></div>;
  const { trace, spans, findings, comments } = detail;
  const selectedSpan = spans.find((span) => span.id === selectedSpanId) ?? null;
  const noticeStyles = { success: 'border-teal-200 bg-teal-50 text-teal-700', error: 'border-red-200 bg-red-50 text-red-700', info: 'border-blue-200 bg-blue-50 text-blue-700' };

  return <div className="mx-auto max-w-[1680px] p-5 md:p-7 xl:p-8">
    <div className="mb-5"><Link to="/traces" className="mb-4 inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-teal-700"><ArrowLeft size={14} />返回审计链路</Link><div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2.5"><h1 className="page-title break-words">{trace.name}</h1><StatusBadge status={trace.status} />{trace.risk_count > 0 && <a href="#findings" className="badge border-orange-200 bg-orange-50 text-orange-700"><ShieldAlert size={11} />{trace.risk_count} 项风险</a>}</div><div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400"><code>{shortId(trace.id)}</code><span>{trace.source}</span><span>{trace.model || '未指定模型'}</span>{trace.tags?.map((tag) => <span key={tag} className="rounded bg-slate-200/60 px-1.5 py-0.5 text-[10px] text-slate-500">{tag}</span>)}</div></div><div className="flex flex-wrap gap-2"><button className="btn-secondary" disabled={Boolean(action)} onClick={() => void runRules()}><RefreshCw size={14} className={action === 'rules' ? 'animate-spin' : ''} />{action === 'rules' ? '检测中' : '重新检测'}</button><button className="btn-secondary" disabled={Boolean(action)} onClick={() => void runLlm()} title="可选 LLM 检测，未配置时不影响免费规则"><Bot size={14} />{action === 'llm' ? '检测中' : 'LLM 检测'}</button><button className="btn-secondary" disabled={Boolean(action)} onClick={() => void exportReport('md')}><FileText size={14} />{action === 'md' ? '导出中' : 'Markdown'}</button><button className="btn-primary" disabled={Boolean(action)} onClick={() => void exportReport('pdf')}><Download size={14} />{action === 'pdf' ? '导出中' : '导出 PDF'}</button></div></div></div>
    {error && <div className="panel mb-4"><ErrorState message={error} onRetry={() => void load()} /></div>}{notice && <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${noticeStyles[notice.tone]}`} role="status">{notice.text}</div>}{trace.error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs leading-5 text-red-700"><span className="font-semibold">链路错误：</span>{trace.error}</div>}
    <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{[
      { label: '开始时间', value: formatFullTime(trace.started_at), icon: Clock3 }, { label: '端到端耗时', value: formatDuration(trace.duration_ms), icon: ScanSearch },
      { label: 'Token 用量', value: `${formatNumber(trace.total_tokens)}（入 ${formatNumber(trace.prompt_tokens)} / 出 ${formatNumber(trace.completion_tokens)}）`, icon: Coins },
      { label: '执行节点', value: `${formatNumber(trace.span_count || spans.length)} 个 Span · ${formatNumber(findings.length)} 项发现`, icon: GitBranch },
    ].map(({ label, value, icon: Icon }) => <div className="panel flex items-start gap-3 p-4" key={label}><span className="rounded-lg bg-slate-100 p-2 text-slate-500"><Icon size={15} /></span><div className="min-w-0"><p className="text-[10px] font-medium text-slate-400">{label}</p><p className="mt-1.5 break-words text-xs font-semibold leading-5 text-slate-700">{value}</p></div></div>)}</section>
    <details className="panel group mb-5 overflow-hidden"><summary className="panel-header cursor-pointer list-none"><span className="panel-title">Trace 原始输入/输出</span><ChevronDown size={15} className="text-slate-400 transition-transform group-open:rotate-180" /></summary><div className="grid gap-4 border-t border-slate-100 p-5 md:grid-cols-2"><section className="min-w-0"><h3 className="mb-2 text-xs font-semibold text-slate-600">原始输入</h3><JsonBlock value={trace.input} /></section><section className="min-w-0"><h3 className="mb-2 text-xs font-semibold text-slate-600">原始输出</h3><JsonBlock value={trace.output} /></section></div></details>
    <div id="dag" className="grid scroll-mt-5 gap-5 2xl:grid-cols-[minmax(0,1fr)_370px]">
      <section className="panel min-w-0 overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">执行 DAG</h2><p className="mt-1 text-[11px] text-slate-400">按父子关系连线；无父子关系时按 sequence 时序连接</p></div><div className="hidden items-center gap-3 text-[10px] text-slate-400 md:flex"><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-violet-400" />模型</span><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-amber-400" />工具</span><span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-teal-400" />当前节点</span></div></div><TraceDAG spans={spans} findings={findings} selectedSpanId={selectedSpanId} onSelect={setSelectedSpanId} /><ReplayBar spans={spans} onActiveChange={setSelectedSpanId} /></section>
      <section className="panel min-w-0 overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">Span 详情</h2><p className="mt-1 text-[11px] text-slate-400">与 DAG、时间线和回放同步</p></div>{selectedSpan && <span className="font-mono text-[10px] text-teal-700">已选中</span>}</div><SpanInspector span={selectedSpan} /></section>
    </div>
    <section className="panel mt-5 overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">执行时间线</h2><p className="mt-1 text-[11px] text-slate-400">按 sequence 排列，点击任意步骤可同步定位</p></div><span className="text-xs text-slate-400">{spans.length} 步</span></div><TraceTimeline spans={spans} selectedSpanId={selectedSpanId} onSelect={setSelectedSpanId} /></section>
    <div className="mt-5 grid items-start gap-5 xl:grid-cols-2"><section id="findings" className="panel scroll-mt-5 overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">风险发现与证据</h2><p className="mt-1 text-[11px] text-slate-400">严重度、解释与原始 evidence 可逐项复核</p></div><span className="badge border-orange-200 bg-orange-50 text-orange-700">{findings.length} 项</span></div><FindingsPanel findings={findings} onLocate={locateSpan} /></section><section className="panel overflow-hidden"><div className="panel-header"><div><h2 className="panel-title">审计评论</h2><p className="mt-1 text-[11px] text-slate-400">协作记录复核结论，支持待处理与已解决</p></div><span className="badge border-slate-200 bg-slate-50 text-slate-500">{comments.filter((comment) => comment.status === 'open').length} 待处理</span></div><CommentsPanel traceId={traceId} selectedSpanId={selectedSpanId} comments={comments} onChanged={() => load(false)} /></section></div>
  </div>;
}
