import { describe, expect, it } from 'vitest';
import { initialReplayState, transitionReplay } from './replay';

describe('单步回放状态机', () => {
  it('上一步和下一步受边界约束，并暂停自动播放', () => {
    const start = { ...initialReplayState, index: 1, playing: true };
    expect(transitionReplay(start, { type: 'previous' }, 3)).toEqual({ index: 0, playing: false });
    expect(transitionReplay(start, { type: 'next' }, 3)).toEqual({ index: 2, playing: false });
    expect(transitionReplay({ index: 2, playing: false }, { type: 'next' }, 3).index).toBe(2);
    expect(transitionReplay({ index: 0, playing: false }, { type: 'previous' }, 3).index).toBe(0);
  });

  it('播放推进到最后一步时自动暂停', () => {
    const playing = transitionReplay({ index: 1, playing: false }, { type: 'play' }, 3);
    expect(playing).toEqual({ index: 1, playing: true });
    expect(transitionReplay(playing, { type: 'tick' }, 3)).toEqual({ index: 2, playing: false });
  });

  it('在末尾重新播放会回到起点，进度选择会暂停', () => {
    expect(transitionReplay({ index: 2, playing: false }, { type: 'play' }, 3)).toEqual({ index: 0, playing: true });
    expect(transitionReplay({ index: 0, playing: true }, { type: 'seek', index: 1 }, 3)).toEqual({ index: 1, playing: false });
  });

  it('空链路不进入播放状态', () => {
    expect(transitionReplay(initialReplayState, { type: 'play' }, 0)).toEqual(initialReplayState);
  });
});
