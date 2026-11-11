import { ChevronDown, Clock3, Coins } from 'lucide-react';
import type { Span } from '../api/types';
import { formatDuration, formatFullTime, formatNumber } from '../lib/format';
import { JsonBlock, StatusBadge } from './ui';
import { kindLabels } from '../lib/span';

function DataSection({ title, value, text, defaultOpen = false }: { title: string; value?: any; text?: string | null; defaultOpen?: boolean }) {
  if ((value === undefined || value === null) && !text) return null;
  return <details className="group border-b border-slate-100 last:border-0" open={defaultOpen}><summary className="flex cursor-pointer list-none items-center justify-between py-3 text-xs font-semibold text-slate-700 hover:text-teal-700">{title}<ChevronDown size={13} className="text-slate-400 transition-transform group-open:rotate-180" /></summary><div className="pb-3">{text ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-700">{text}</pre> : <JsonBlock value={value} />}</div></details>;
}

export function SpanInspector({ span }: { span: Span | null }) {
  if (!span) return <div className="p-5 text-sm text-slate-400">选择一个节点查看完整执行内容。</div>;
  return <div><div className="border-b border-slate-100 p-5"><div className="flex items-center justify-between gap-2"><span className="badge border-slate-200 bg-slate-50 text-slate-600">{kindLabels[span.kind] ?? span.kind} · #{span.sequence}</span><StatusBadge status={span.status} /></div><h2 className="mt-3 break-words text-base font-semibold text-ink-900">{span.name}</h2><code className="mt-1 block break-all text-[10px] text-slate-400">{span.id}</code><div className="mt-4 grid grid-cols-2 gap-2"><div className="rounded-lg bg-slate-50 p-2.5"><Clock3 size={13} className="text-slate-400" /><p className="mt-1.5 font-mono text-xs font-semibold text-slate-700">{formatDuration(span.duration_ms)}</p></div><div className="rounded-lg bg-slate-50 p-2.5"><Coins size={13} className="text-slate-400" /><p className="mt-1.5 font-mono text-xs font-semibold text-slate-700">{formatNumber(span.total_tokens)} Token</p></div></div><div className="mt-3 text-[10px] leading-5 text-slate-400"><p>开始：{formatFullTime(span.started_at)}</p><p>结束：{formatFullTime(span.ended_at)}</p></div></div>
    <div className="max-h-[680px] overflow-y-auto px-5"><DataSection title="Prompt / 输入" value={span.input} defaultOpen /><DataSection title="内容" text={span.content} defaultOpen={!span.input} /><DataSection title="思考摘要（Reasoning）" text={span.reasoning} defaultOpen /><DataSection title="工具参数" value={span.tool_arguments} defaultOpen /><DataSection title="工具返回" value={span.tool_result} defaultOpen /><DataSection title="模型 / 节点输出" value={span.output} /><DataSection title="错误详情" value={span.error} text={span.error} defaultOpen /><DataSection title="Metadata" value={span.metadata} /><DataSection title="完整 Span 原始 JSON" value={span} /><div className="py-3 text-[10px] leading-4 text-slate-400">原始 JSON 展示前会隐藏常见 API Key、令牌、密码与认证请求头。</div></div>
  </div>;
}
