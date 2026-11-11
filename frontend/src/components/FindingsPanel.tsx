import { Crosshair, ShieldAlert } from 'lucide-react';
import type { Finding } from '../api/types';
import { formatFullTime } from '../lib/format';
import { EmptyState, JsonBlock, SeverityBadge } from './ui';

export function FindingsPanel({ findings, onLocate }: { findings: Finding[]; onLocate: (spanId: string) => void }) {
  if (!findings.length) return <EmptyState title="未发现风险" description="当前检测结果为空；重新检测后仍无发现时，此链路没有已识别风险。" />;
  return <div className="divide-y divide-slate-100">{findings.map((finding) => <article key={finding.id} className="p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div className="flex items-start gap-3"><span className={`mt-0.5 rounded-lg p-2 ${finding.severity === 'high' || finding.severity === 'critical' ? 'bg-red-50 text-red-600' : 'bg-orange-50 text-orange-600'}`}><ShieldAlert size={17} /></span><div><div className="flex flex-wrap items-center gap-2"><SeverityBadge severity={finding.severity} /><span className="text-[10px] text-slate-400">{finding.category} · {finding.rule_code}</span></div><h3 className="mt-2 text-sm font-semibold text-ink-900">{finding.title}</h3></div></div>{finding.span_id && <button className="btn-secondary h-8 px-2.5 text-xs" onClick={() => onLocate(finding.span_id!)}><Crosshair size={13} />定位 Span</button>}</div><p className="mt-3 text-xs leading-6 text-slate-600">{finding.explanation}</p><details className="mt-3"><summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-teal-700">查看风险证据</summary><div className="mt-2"><JsonBlock value={finding.evidence} /></div></details><p className="mt-3 text-[10px] text-slate-400">{finding.source} · {formatFullTime(finding.created_at)}</p></article>)}</div>;
}
