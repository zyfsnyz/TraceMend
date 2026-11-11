import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Pause, Play } from 'lucide-react';
import type { Span } from '../api/types';
import { initialReplayState, transitionReplay, type ReplayAction, type ReplayState } from '../lib/replay';

interface ReplayBarProps {
  spans: Span[];
  onActiveChange: (spanId: string | null) => void;
}

export function ReplayBar({ spans, onActiveChange }: ReplayBarProps) {
  const ordered = useMemo(() => [...spans].sort((a, b) => a.sequence - b.sequence), [spans]);
  const [state, setState] = useState<ReplayState>(initialReplayState);
  const current = ordered[state.index];
  const currentId = current?.id ?? null;

  useEffect(() => onActiveChange(currentId), [currentId, onActiveChange]);
  useEffect(() => setState(initialReplayState), [spans]);
  useEffect(() => {
    if (!state.playing) return;
    const timer = window.setInterval(() => setState((value) => transitionReplay(value, { type: 'tick' }, ordered.length)), 1200);
    return () => window.clearInterval(timer);
  }, [state.playing, ordered.length]);

  const dispatch = (action: ReplayAction) => setState((value) => transitionReplay(value, action, ordered.length));

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-white px-4 py-3">
      <div className="flex items-center rounded-lg border border-slate-200 bg-slate-50 p-1">
        <button className="icon-button h-8 w-8" aria-label="上一步" title="上一步" disabled={!ordered.length || state.index === 0} onClick={() => dispatch({ type: 'previous' })}><ChevronLeft size={16} /></button>
        <button className="icon-button h-8 w-8 text-teal-700" aria-label={state.playing ? '暂停' : '播放'} title={state.playing ? '暂停' : '播放'} disabled={!ordered.length} onClick={() => dispatch({ type: state.playing ? 'pause' : 'play' })}>
          {state.playing ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" />}
        </button>
        <button className="icon-button h-8 w-8" aria-label="下一步" title="下一步" disabled={!ordered.length || state.index >= ordered.length - 1} onClick={() => dispatch({ type: 'next' })}><ChevronRight size={16} /></button>
      </div>
      <input aria-label="回放进度" className="mx-1 min-w-24 flex-1 accent-teal-600" type="range" min={0} max={Math.max(0, ordered.length - 1)} value={Math.min(state.index, Math.max(0, ordered.length - 1))} disabled={!ordered.length} onChange={(event) => dispatch({ type: 'seek', index: Number(event.target.value) })} />
      <span className="w-14 text-center font-mono text-xs font-semibold text-slate-600">{ordered.length ? state.index + 1 : 0} / {ordered.length}</span>
      <span className="hidden max-w-44 truncate text-xs text-slate-500 lg:inline" title={current?.name}>{current?.name ?? '暂无可回放步骤'}</span>
    </div>
  );
}
