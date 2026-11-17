import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AppLayout } from './AppLayout';

describe('AppLayout 产品品牌', () => {
  it('展示 TraceMend 名称与审计回放定位', () => {
    render(<MemoryRouter><AppLayout /></MemoryRouter>);

    expect(screen.getByText('TraceMend')).toBeInTheDocument();
    expect(screen.getByText('Agent 审计与链路回放')).toBeInTheDocument();
    expect(screen.queryByText('AgentTrace')).not.toBeInTheDocument();
  });
});
