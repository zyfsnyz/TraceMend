import { describe, expect, it } from 'vitest';
import { formatDuration, formatNumber, formatTime } from './format';

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
});
