import { describe, expect, it } from 'vitest';
import { dateInputFromIso, formatDuration, formatNumber, formatTime, localDayEnd, localDayStart } from './format';

describe('审计数据格式化', () => {
  it('将大数字格式化为易读的分组数字', () => {
    expect(formatNumber(1234567)).toBe('1,234,567');
    expect(formatNumber(null)).toBe('—');
  });

  it('按数量级显示耗时', () => {
    expect(formatDuration(850)).toBe('850 ms');
    expect(formatDuration(1500)).toBe('1.50 s');
    expect(formatDuration(65000)).toBe('1 min 5 s');
  });

  it('无效时间不回显原始脏数据', () => {
    expect(formatTime('not-a-date')).toBe('—');
  });

  it('日期筛选覆盖当地完整一天且不落入次日', () => {
    const from = new Date(localDayStart('2026-11-11'));
    const end = new Date(localDayEnd('2026-11-11'));
    expect(from.getFullYear()).toBe(2026);
    expect(from.getMonth()).toBe(10);
    expect(from.getDate()).toBe(11);
    expect(from.getHours()).toBe(0);
    expect(from.getMinutes()).toBe(0);
    expect(end.getTime() - from.getTime()).toBe(86_399_999);
    expect(end.getDate()).toBe(11);
    expect(end.getHours()).toBe(23);
    expect(end.getMinutes()).toBe(59);
    expect(dateInputFromIso(from.toISOString())).toBe('2026-11-11');
    expect(localDayStart('')).toBe('');
    expect(localDayEnd('bad-date')).toBe('');
  });
});
