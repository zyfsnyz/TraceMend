import type { Edge, Node } from '@xyflow/react';
import { Position } from '@xyflow/react';
import type { Span } from '../api/types';

export type SpanNodeData = Record<string, unknown> & { span: Span; active?: boolean };
export type SpanFlowNode = Node<SpanNodeData, 'span'>;

function bySequence(a: Span, b: Span): number {
  const sequence = (a.sequence ?? 0) - (b.sequence ?? 0);
  if (sequence !== 0) return sequence;
  return (a.started_at ?? '').localeCompare(b.started_at ?? '') || a.id.localeCompare(b.id);
}

export function layoutSpans(input: Span[]): { nodes: SpanFlowNode[]; edges: Edge[] } {
  const spans = [...input].sort(bySequence);
  const ids = new Set(spans.map((span) => span.id));
  const byId = new Map(spans.map((span) => [span.id, span]));
  const hasParentRelations = spans.some((span) => Boolean(span.parent_span_id));
  const validParents = new Map<string, string>();

  for (const span of spans) {
    const parent = span.parent_span_id;
    if (parent && parent !== span.id && ids.has(parent)) validParents.set(span.id, parent);
  }

  // Reject only edges that close a cycle (DFS three-colour), keeping the graph a rooted DAG.
  const visitState = new Map<string, 1 | 2>();
  const stabilise = (id: string): boolean => {
    const state = visitState.get(id);
    if (state === 1) return false;
    if (state === 2) return true;
    visitState.set(id, 1);
    const parent = validParents.get(id);
    if (parent && !stabilise(parent)) validParents.delete(id);
    visitState.set(id, 2);
    return true;
  };
  for (const span of spans) stabilise(span.id);

  const edges: Edge[] = [];
  if (validParents.size > 0) {
    for (const span of spans) {
      const parent = validParents.get(span.id);
      if (parent) edges.push({
        id: `${parent}-${span.id}`, source: parent, target: span.id,
        type: 'smoothstep', animated: span.status === 'running',
        style: { stroke: '#a9b7c7', strokeWidth: 1.5 },
      });
    }
  } else if (!hasParentRelations) {
    for (let index = 1; index < spans.length; index += 1) {
      const source = spans[index - 1].id;
      const target = spans[index].id;
      edges.push({ id: `${source}-${target}`, source, target, type: 'smoothstep', style: { stroke: '#a9b7c7', strokeWidth: 1.5 } });
    }
  }

  const depthCache = new Map<string, number>();
  const depthOf = (span: Span, visited = new Set<string>()): number => {
    if (depthCache.has(span.id)) return depthCache.get(span.id)!;
    if (!hasParentRelations && validParents.size === 0) return spans.indexOf(span);
    if (visited.has(span.id)) return 0;
    visited.add(span.id);
    const parentId = validParents.get(span.id);
    const parent = parentId ? byId.get(parentId) : undefined;
    const depth = parent ? depthOf(parent, visited) + 1 : 0;
    depthCache.set(span.id, depth);
    return depth;
  };

  const rowByDepth = new Map<number, number>();
  const nodes = spans.map<SpanFlowNode>((span) => {
    const depth = depthOf(span);
    const row = rowByDepth.get(depth) ?? 0;
    rowByDepth.set(depth, row + 1);
    return {
      id: span.id, type: 'span' as const, position: { x: depth * 310, y: row * 148 },
      data: { span } as SpanNodeData, sourcePosition: Position.Right, targetPosition: Position.Left,
    };
  });
  return { nodes, edges };
}
