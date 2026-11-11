export interface ReplayState { index: number; playing: boolean }
export type ReplayAction =
  | { type: 'previous' | 'next' | 'play' | 'pause' | 'tick' }
  | { type: 'seek'; index: number };

export const initialReplayState: ReplayState = { index: 0, playing: false };

export function transitionReplay(state: ReplayState, action: ReplayAction, total: number): ReplayState {
  if (total <= 0) return initialReplayState;
  const last = total - 1;
  const index = Math.max(0, Math.min(state.index, last));
  switch (action.type) {
    case 'previous': return { index: Math.max(0, index - 1), playing: false };
    case 'next': return { index: Math.min(last, index + 1), playing: false };
    case 'play': return { index: index >= last ? 0 : index, playing: true };
    case 'pause': return { index, playing: false };
    case 'seek': return { index: Math.max(0, Math.min(last, action.index)), playing: false };
    case 'tick':
      if (!state.playing) return { index, playing: false };
      return { index: Math.min(last, index + 1), playing: index + 1 < last };
  }
}
