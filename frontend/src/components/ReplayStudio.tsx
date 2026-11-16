import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ExternalLink, LoaderCircle, RotateCcw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { replaySpan } from '../api/client';
import type { ReplayOverrides, ReplaySpanResult, Span } from '../api/types';
import { stringifySafe } from '../lib/sensitive';

interface ReplayStudioProps {
  traceId: string;
  span: Span | null;
}

type OverrideField = keyof ReplayOverrides;

const EMPTY_FIELDS: Record<OverrideField, string> = {
  model: '',
  temperature: '',
  top_p: '',
  seed: '',
  max_tokens: '',
};

const NUMERIC_FIELDS = ['temperature', 'top_p', 'seed', 'max_tokens'] as const;

export function ReplayStudio({ traceId, span }: ReplayStudioProps) {
  const [fields, setFields] = useState<Record<OverrideField, string>>({ ...EMPTY_FIELDS });
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ReplaySpanResult | null>(null);
  const [error, setError] = useState('');
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const generationRef = useRef(0);
  const requestTokenRef = useRef(0);
  const pendingRef = useRef(false);

  useEffect(() => {
    generationRef.current += 1;
    requestTokenRef.current += 1;
    pendingRef.current = false;
    setFields({ ...EMPTY_FIELDS });
    setConfirmed(false);
    setPending(false);
    setResult(null);
    setError('');
    setIdempotencyKey(null);
    return () => {
      generationRef.current += 1;
      requestTokenRef.current += 1;
      pendingRef.current = false;
    };
  }, [traceId, span?.id]);

  if (!span) {
    return (
      <section className="panel overflow-hidden">
        <div className="panel-header"><h2 className="panel-title">Replay Studio · 真实重放</h2></div>
        <div className="px-6 py-10 text-center text-sm text-slate-500">请选择一个 LLM Span 以开始真实重放。</div>
      </section>
    );
  }

  if (span.kind !== 'llm') {
    return (
      <section className="panel overflow-hidden">
        <div className="panel-header"><h2 className="panel-title">Replay Studio · 真实重放</h2></div>
        <div className="px-6 py-10 text-center">
          <p className="text-sm font-medium text-slate-600">真实重放仅支持 LLM Span</p>
          <p className="mt-2 text-xs text-slate-400">当前节点不会调用模型，请改选 LLM Span。</p>
        </div>
      </section>
    );
  }

  const updateField = (field: OverrideField, value: string) => {
    setFields((current) => ({ ...current, [field]: value }));
  };

  const buildOverrides = (): ReplayOverrides => {
    const overrides: ReplayOverrides = {};
    if (fields.model.trim()) overrides.model = fields.model.trim();
    for (const field of NUMERIC_FIELDS) {
      const value = fields[field].trim();
      if (!value) continue;
      const parsed = Number(value);
      if (Number.isFinite(parsed)) overrides[field] = parsed;
    }
    return overrides;
  };

  const submitReplay = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!confirmed || pendingRef.current) return;
    const generation = generationRef.current;
    const requestToken = ++requestTokenRef.current;
    const requestKey = idempotencyKey ?? crypto.randomUUID();
    if (!idempotencyKey) setIdempotencyKey(requestKey);
    pendingRef.current = true;
    setPending(true);
    setResult(null);
    setError('');
    const isCurrentRequest = () => generation === generationRef.current && requestToken === requestTokenRef.current;

    try {
      const nextResult = await replaySpan(traceId, span.id, {
        confirm: true,
        overrides: buildOverrides(),
      }, requestKey);
      if (!isCurrentRequest()) return;
      setResult(nextResult);
    } catch (reason) {
      if (!isCurrentRequest()) return;
      setResult(null);
      setError(stringifySafe(reason instanceof Error ? reason.message : '真实重放失败，请稍后重试。'));
    } finally {
      if (isCurrentRequest()) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };

  const startNewExperiment = () => {
    requestTokenRef.current += 1;
    pendingRef.current = false;
    setPending(false);
    setResult(null);
    setError('');
    setIdempotencyKey(null);
    setConfirmed(false);
  };

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Replay Studio · 真实重放</h2>
          <p className="mt-1 text-[11px] text-slate-400">当前 Span：{span.name}</p>
        </div>
        <span className="badge border-amber-200 bg-amber-50 text-amber-700">可能付费</span>
      </div>

      <div className="space-y-4 p-5">
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-5 text-slate-600">
          <p className="font-medium text-slate-700">只读播放器仅还原历史记录，消耗 0 Token。</p>
          <p className="mt-1 text-amber-700">真实重放会请求上游模型，可能产生费用。</p>
          <p className="mt-1">仅重放当前单个 LLM Span，不会执行工具，也不会恢复完整链路。</p>
        </div>

        <form className="space-y-4" onSubmit={(event) => void submitReplay(event)}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <div>
              <label className="label" htmlFor="replay-model">model</label>
              <input id="replay-model" className="input w-full" value={fields.model} onChange={(event) => updateField('model', event.target.value)} placeholder="沿用原始模型" autoComplete="off" />
            </div>
            <div>
              <label className="label" htmlFor="replay-temperature">temperature</label>
              <input id="replay-temperature" className="input w-full" type="number" step="any" value={fields.temperature} onChange={(event) => updateField('temperature', event.target.value)} placeholder="沿用原值" />
            </div>
            <div>
              <label className="label" htmlFor="replay-top-p">top_p</label>
              <input id="replay-top-p" className="input w-full" type="number" step="any" value={fields.top_p} onChange={(event) => updateField('top_p', event.target.value)} placeholder="沿用原值" />
            </div>
            <div>
              <label className="label" htmlFor="replay-seed">seed</label>
              <input id="replay-seed" className="input w-full" type="number" step="1" value={fields.seed} onChange={(event) => updateField('seed', event.target.value)} placeholder="沿用原值" />
            </div>
            <div>
              <label className="label" htmlFor="replay-max-tokens">max_tokens</label>
              <input id="replay-max-tokens" className="input w-full" type="number" step="1" min="1" value={fields.max_tokens} onChange={(event) => updateField('max_tokens', event.target.value)} placeholder="沿用原值" />
            </div>
          </div>
          <p className="text-[11px] leading-5 text-slate-400">留空字段不会放入 overrides，后端沿用可安全重建的原始请求参数。</p>

          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-3 text-xs leading-5 text-amber-800">
            <input className="mt-1 h-4 w-4 shrink-0 accent-amber-600" type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            <span>我确认本次操作是单次 LLM 真实重放，会请求上游模型并可能产生费用。</span>
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <button className="btn-primary" type="submit" disabled={!confirmed || pending}>
              {pending && <LoaderCircle size={14} className="animate-spin" />}
              {pending ? '重放中…' : '开始真实重放'}
            </button>
            {pending && <span className="text-xs text-slate-500" role="status">正在请求上游模型，请勿重复提交。</span>}
            {result && <button className="btn-secondary" type="button" onClick={startNewExperiment}><RotateCcw size={14} />新实验</button>}
          </div>
        </form>

        {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert"><p>{error}</p></div>}

        {result && (
          <div className="space-y-4 border-t border-slate-100 pt-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="badge border-slate-200 bg-slate-50 text-slate-600">对比状态：{result.comparison.status === 'equal' ? '一致' : '不同'}</span>
              <span className={`badge ${result.comparison.content_equal ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>内容{result.comparison.content_equal ? '一致' : '不同'}</span>
              <span className={`badge ${result.comparison.reasoning_equal ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>推理{result.comparison.reasoning_equal ? '一致' : '不同'}</span>
              <span className={`badge ${result.comparison.tool_calls_equal ? 'border-teal-200 bg-teal-50 text-teal-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>工具调用{result.comparison.tool_calls_equal ? '一致' : '不同'}</span>
              <span className="text-xs font-semibold text-slate-600">内容相似度：{Math.round(result.comparison.content_similarity * 100)}%</span>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <section className="min-w-0">
                <h3 className="mb-2 text-xs font-semibold text-slate-700">原始输出</h3>
                <pre className="code-block">{stringifySafe(result.source_span.content)}</pre>
                <p className="mb-1.5 mt-3 text-[11px] font-medium text-slate-500">推理内容（已脱敏）</p>
                <pre className="code-block">{stringifySafe(result.source_span.reasoning)}</pre>
              </section>
              <section className="min-w-0">
                <h3 className="mb-2 text-xs font-semibold text-slate-700">重放输出</h3>
                <pre className="code-block">{stringifySafe(result.replay_span.content)}</pre>
                <p className="mb-1.5 mt-3 text-[11px] font-medium text-slate-500">推理内容（已脱敏）</p>
                <pre className="code-block">{stringifySafe(result.replay_span.reasoning)}</pre>
              </section>
            </div>

            <section>
              <h3 className="mb-2 text-xs font-semibold text-slate-700">内容 Diff（已脱敏）</h3>
              <pre className="code-block max-w-none">{stringifySafe(result.comparison.content_diff)}</pre>
            </section>
            <p className="rounded-lg border border-blue-200 bg-blue-50 px-3.5 py-2.5 text-xs leading-5 text-blue-700">{stringifySafe(result.comparison.attribution_note)}</p>
            <Link className="inline-flex items-center gap-1.5 text-xs font-medium text-teal-700 hover:text-teal-800" to={`/traces/${encodeURIComponent(result.replay_trace.id)}`}>
              查看重放 Trace<ExternalLink size={13} />
            </Link>
          </div>
        )}
      </div>
    </section>
  );
}
