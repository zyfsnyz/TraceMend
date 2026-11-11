import { Handle, NodeProps, Position } from '@xyflow/react';
import { Bot, CircleUserRound, Cpu, DatabaseZap, ServerCog, Wrench } from 'lucide-react';
import type { SpanFlowNode } from '../lib/dag';
import type { SpanKind } from '../api/types';
import { kindLabels } from '../lib/span';
import { formatDuration, formatNumber } from '../lib/format';

const icons = { user: CircleUserRound, llm: Bot, tool: Wrench, tool_result: DatabaseZap, mcp: ServerCog, system: Cpu };
const colors: Record<SpanKind, string> = {
  user: 'bg-blue-50 text-blue-600 border-blue-100', llm: 'bg-violet-50 text-violet-600 border-violet-100',
  tool: 'bg-amber-50 text-amber-600 border-amber-100', tool_result: 'bg-cyan-50 text-cyan-600 border-cyan-100',
  mcp: 'bg-indigo-50 text-indigo-600 border-indigo-100', system: 'bg-slate-100 text-slate-600 border-slate-200',
};

export function SpanNode({ data, selected }: NodeProps<SpanFlowNode>) {
  const { span, active, riskCount = 0 } = data as { span: SpanFlowNode['data']['span']; active?: boolean; riskCount?: number };
  const Icon = icons[span.kind] ?? Cpu;
  const failed = span.status === 'error' || span.status === 'failed';
  return <div className={`relative w-[268px] rounded-xl border bg-white p-3.5 shadow-[0_4px_14px_rgba(15,23,42,.06)] transition-all ${selected ? 'border-teal-500 ring-2 ring-teal-500/15' : active ? 'border-teal-400' : failed ? 'border-red-200' : 'border-slate-200'} ${active ? 'shadow-[0_0_0_4px_rgba(20,169,139,.08),0_8px_20px_rgba(15,23,42,.08)]' : ''}`}>
    <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-2 !border-white !bg-slate-400" />
    <div className="flex items-center gap-2.5"><span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${colors[span.kind] ?? colors.system}`}><Icon size={15} /></span><div className="min-w-0 flex-1"><div className="flex items-center gap-1.5"><span className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">{String(span.sequence).padStart(2, '0')} · {kindLabels[span.kind] ?? span.kind}</span>{failed && <span className="h-1.5 w-1.5 rounded-full bg-red-500" />}</div><p className="mt-0.5 truncate text-xs font-semibold text-ink-900" title={span.name}>{span.name}</p></div>{riskCount > 0 && <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-orange-100 px-1 text-[10px] font-bold text-orange-700" title={`${riskCount} 项风险`}>{riskCount}</span>}</div>
    <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2.5 font-mono text-[10px] text-slate-400"><span>{formatDuration(span.duration_ms)}</span><span>{formatNumber(span.total_tokens)} Token</span><span className={failed ? 'text-red-600' : span.status === 'running' ? 'text-blue-600' : 'text-teal-600'}>{failed ? '异常' : span.status === 'running' ? '运行中' : '完成'}</span></div>
    <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-2 !border-white !bg-slate-400" />
  </div>;
}
