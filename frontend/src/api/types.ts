export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type TraceStatus = 'completed' | 'error' | 'running' | string;
export type SpanKind = 'user' | 'llm' | 'tool' | 'tool_result' | 'mcp' | 'system';
export type Severity = 'low' | 'medium' | 'high' | 'critical' | string;

export interface Trace {
  id: string;
  name: string;
  source: string;
  status: TraceStatus;
  model: string | null;
  input: JsonValue;
  output: JsonValue;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  tags: string[];
  metadata: Record<string, JsonValue>;
  error: string | null;
  risk_count: number;
  span_count: number;
}

export interface Span {
  id: string;
  trace_id: string;
  parent_span_id: string | null;
  sequence: number;
  kind: SpanKind;
  name: string;
  status: TraceStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  input: JsonValue;
  output: JsonValue;
  content: string | null;
  reasoning: string | null;
  tool_name: string | null;
  tool_arguments: JsonValue;
  tool_result: JsonValue;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  error: string | null;
  metadata: Record<string, JsonValue>;
}

export interface Finding {
  id: string;
  span_id: string | null;
  source: string;
  rule_code: string;
  severity: Severity;
  category: string;
  title: string;
  explanation: string;
  evidence: JsonValue;
  created_at: string;
}

export interface Comment {
  id: string;
  span_id: string | null;
  author: string;
  body: string;
  status: 'open' | 'resolved';
  created_at: string;
  updated_at: string;
}

export interface TraceDetail { trace: Trace; spans: Span[]; findings: Finding[]; comments: Comment[] }
export interface TraceListResponse { items: Trace[]; total: number; page: number; page_size: number; pages: number }
export interface TraceFilters {
  q?: string; status?: string; risk?: string; tag?: string;
  from?: string; to?: string; page?: number; page_size?: number;
}
export interface DailyStat { date: string; count: number }
export interface Stats {
  total_traces: number;
  completed_traces: number;
  error_traces: number;
  risk_traces: number;
  total_findings: number;
  total_tokens: number;
  avg_duration_ms: number;
  tool_calls: number;
  models: Record<string, number>;
  daily: DailyStat[];
  recent_traces?: Trace[];
}
