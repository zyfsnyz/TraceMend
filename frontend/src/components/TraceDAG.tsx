import { useMemo } from 'react';
import { Background, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import type { Finding, Span } from '../api/types';
import { layoutSpans } from '../lib/dag';
import { EmptyState } from './ui';
import { SpanNode } from './SpanNode';

const nodeTypes = { span: SpanNode };

export function TraceDAG({ spans, findings, selectedSpanId, onSelect }: { spans: Span[]; findings: Finding[]; selectedSpanId: string | null; onSelect: (id: string) => void }) {
  const graph = useMemo(() => {
    const result = layoutSpans(spans);
    const riskCounts = findings.reduce<Record<string, number>>((counts, finding) => {
      if (finding.span_id) counts[finding.span_id] = (counts[finding.span_id] ?? 0) + 1;
      return counts;
    }, {});
    result.nodes = result.nodes.map((node) => ({ ...node, selected: node.id === selectedSpanId, data: { ...node.data, active: node.id === selectedSpanId, riskCount: riskCounts[node.id] ?? 0 } }));
    result.edges = result.edges.map((edge) => ({ ...edge, style: { ...edge.style, stroke: edge.source === selectedSpanId || edge.target === selectedSpanId ? '#14a98b' : '#a9b7c7', strokeWidth: edge.source === selectedSpanId || edge.target === selectedSpanId ? 2 : 1.5 } }));
    return result;
  }, [spans, findings, selectedSpanId]);
  if (!spans.length) return <div className="h-[440px]"><EmptyState title="此链路没有 Span" description="上报 spans 后，可在这里查看执行 DAG 与单步回放。" /></div>;
  return <div className="h-[440px] w-full" aria-label="Agent 执行 DAG"><ReactFlow nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} onNodeClick={(_, node) => onSelect(node.id)} fitView fitViewOptions={{ padding: 0.16, maxZoom: 1 }} minZoom={0.2} maxZoom={1.5} nodesConnectable={false} elementsSelectable proOptions={{ hideAttribution: false }}>
    <Background gap={18} size={1} color="#dce3eb" /><Controls showInteractive={false} /><MiniMap className="!hidden md:!block" pannable zoomable nodeColor={(node) => node.id === selectedSpanId ? '#14a98b' : '#cbd5e1'} maskColor="rgba(241,245,249,.8)" />
  </ReactFlow></div>;
}
