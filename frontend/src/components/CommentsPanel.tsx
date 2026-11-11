import { useState } from 'react';
import { CheckCircle2, MessageSquare, RotateCcw, Send } from 'lucide-react';
import { createComment, patchComment } from '../api/client';
import type { Comment } from '../api/types';
import { formatFullTime, shortId } from '../lib/format';
import { EmptyState } from './ui';

export function CommentsPanel({ traceId, selectedSpanId, comments, onChanged }: { traceId: string; selectedSpanId: string | null; comments: Comment[]; onChanged: () => Promise<void> }) {
  const [author, setAuthor] = useState(() => { try { return localStorage.getItem('agenttrace.author') ?? ''; } catch { return ''; } });
  const [body, setBody] = useState('');
  const [scope, setScope] = useState<'trace' | 'span'>('trace');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    if (!author.trim() || !body.trim()) { setError('请填写评论作者与内容。'); return; }
    setBusy(true); setError('');
    try {
      localStorage.setItem('agenttrace.author', author.trim());
      await createComment(traceId, scope === 'span' ? selectedSpanId : null, author.trim(), body.trim());
      setBody(''); await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '评论提交失败'); }
    finally { setBusy(false); }
  };
  const toggle = async (comment: Comment) => {
    setBusy(true); setError('');
    try { await patchComment(comment.id, comment.status === 'open' ? 'resolved' : 'open'); await onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '状态更新失败'); }
    finally { setBusy(false); }
  };
  return <div><form className="border-b border-slate-100 bg-slate-50/60 p-4" onSubmit={(event) => { event.preventDefault(); void submit(); }}><div className="grid grid-cols-2 gap-2"><input className="input h-9 bg-white text-xs" aria-label="评论作者" placeholder="你的姓名" value={author} maxLength={60} onChange={(event) => setAuthor(event.target.value)} /><select className="input h-9 bg-white text-xs" aria-label="评论关联范围" value={scope} onChange={(event) => setScope(event.target.value as 'trace' | 'span')}><option value="trace">关联整条链路</option><option value="span" disabled={!selectedSpanId}>关联当前 Span</option></select></div><textarea className="input mt-2 h-20 w-full resize-none bg-white py-2 text-xs leading-5" aria-label="评论内容" placeholder="记录复核结论、疑问或后续处理建议…" value={body} maxLength={4000} onChange={(event) => setBody(event.target.value)} /><div className="mt-2 flex items-center justify-between gap-2"><span className="text-[10px] text-slate-400">{scope === 'span' && selectedSpanId ? `Span ${shortId(selectedSpanId)}` : '作者保存在本地浏览器'}</span><button className="btn-primary h-8 px-3 text-xs" disabled={busy}>{busy ? '提交中…' : <><Send size={12} />发表评论</>}</button></div>{error && <p className="mt-2 text-xs text-red-600" role="alert">{error}</p>}</form>
    <div className="max-h-[400px] overflow-y-auto">{comments.length ? comments.map((comment) => <article key={comment.id} className={`border-b border-slate-100 p-4 last:border-0 ${comment.status === 'resolved' ? 'opacity-65' : ''}`}><div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink-900 text-[10px] font-semibold text-white">{comment.author.slice(0, 1).toUpperCase()}</span><div><p className="text-xs font-semibold text-slate-700">{comment.author}</p><p className="mt-0.5 text-[9px] text-slate-400">{formatFullTime(comment.created_at)}</p></div></div><span className={`badge ${comment.status === 'open' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-teal-200 bg-teal-50 text-teal-700'}`}>{comment.status === 'open' ? '待处理' : '已解决'}</span></div><p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600">{comment.body}</p><div className="mt-3 flex items-center justify-between gap-2"><span className="text-[9px] text-slate-400">{comment.span_id ? `关联 Span ${shortId(comment.span_id)}` : '整条链路'}</span><button className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-teal-700" disabled={busy} onClick={() => void toggle(comment)}>{comment.status === 'open' ? <><CheckCircle2 size={12} />标记已解决</> : <><RotateCcw size={12} />重新打开</>}</button></div></article>) : <div className="py-6"><EmptyState title="还没有审计评论" description="与团队协作记录风险复核与处理结论。" /></div>}</div><div className="flex items-center gap-2 border-t border-slate-100 px-4 py-3 text-[10px] text-slate-400"><MessageSquare size={12} />共 {comments.length} 条评论</div>
  </div>;
}
