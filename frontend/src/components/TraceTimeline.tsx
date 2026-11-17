import type { Span } from '../api/types';
import { formatDuration, formatNumber } from '../lib/format';
import { StatusBadge } from './ui';
import { kindLabels } from '../lib/span';
import { stringifySafe } from '../lib/sensitive';

export function TraceTimeline({ spans, selectedSpanId, onSelect }: { spans: Span[]; selectedSpanId: string | null; onSelect: (id: string) => void }) {
  const ordered = [...spans].sort((a, b) => a.sequence - b.sequence);
  return <div className="max-h-[310px] overflow-y-auto">{ordered.map((span, index) => <button key={span.id} className={`relative flex w-full items-center gap-3 border-b border-slate-100 px-4 py-3 text-left last:border-0 ${selectedSpanId === span.id ? 'bg-teal-50/70' : 'hover:bg-slate-50'}`} onClick={() => onSelect(span.id)}><div className="relative flex w-7 shrink-0 justify-center"><span className={`flex h-6 w-6 items-center justify-center rounded-full border font-mono text-[10px] ${selectedSpanId === span.id ? 'border-teal-500 bg-teal-600 text-white' : 'border-slate-200 bg-white text-slate-400'}`}>{index + 1}</span>{index < ordered.length - 1 && <span className="absolute left-1/2 top-7 h-6 w-px bg-slate-200" />}</div><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="truncate text-xs font-medium text-ink-900">{stringifySafe(span.name)}</span><span className="text-[10px] text-slate-400">{kindLabels[span.kind] ?? span.kind}</span></div><p className="mt-1 font-mono text-[10px] text-slate-400">{formatDuration(span.duration_ms)} · {formatNumber(span.total_tokens)} Token</p></div><StatusBadge status={span.status} /></button>)}</div>;
}
