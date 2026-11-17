export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type TraceStatus = 'completed' | 'error' | 'running' | string;
export type SpanKind = 'user' | 'llm' | 'tool' | 'tool_result' | 'mcp' | 'system';
export type Severity = 'low' | 'medium' | 'high' | 'critical' | string;

export interface Trace {
  id: string;
  parent_id?: string | null;
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
  estimated_cost_usd?: number;
  tags: string[];
  metadata: Record<string, JsonValue>;
  error: JsonValue;
  risk_count: number;
  span_count: number;
  created_at?: string;
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
  error: JsonValue;
  metadata: Record<string, JsonValue>;
}

export interface ReplayOverrides {
  model?: string;
  temperature?: number;
  top_p?: number;
  seed?: number;
  max_tokens?: number;
}

export interface ReplaySpanRequest {
  confirm: true;
  overrides: ReplayOverrides;
}

export interface ReplayComparison {
  status: 'same' | 'different' | 'failed';
  content_equal: boolean;
  reasoning_equal: boolean;
  tool_calls_equal: boolean;
  content_similarity: number;
  content_diff: string;
  attribution_note: string;
}

export interface ReplaySpanResult {
  replay_trace: Trace;
  replay_span: Span;
  source_span: Span;
  comparison: ReplayComparison;
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
  total_cost_usd?: number;
  today_tokens?: number;
  daily_token_budget?: number;
  active_routes?: number;
  open_circuits?: number;
  avg_duration_ms: number;
  tool_calls: number;
  models: Record<string, number>;
  daily: DailyStat[];
  recent_traces?: Trace[];
}

export interface AppSettings {
  default_upstream_base: string;
  default_upstream_model: string;
  default_upstream_key_configured: boolean;
  default_mcp_upstream: string;
  default_mcp_key_configured: boolean;
  enable_llm_check: boolean;
  check_model_base: string;
  check_model_key_configured: boolean;
  check_model_name: string;
  check_model_timeout: number;
  gateway_daily_token_budget: number;
  gateway_failure_threshold: number;
  gateway_cooldown_seconds: number;
  default_input_price_per_million: number;
  default_output_price_per_million: number;
}

export type SettingsUpdate = Partial<Omit<AppSettings,
  'default_upstream_key_configured' | 'default_mcp_key_configured' | 'check_model_key_configured'
>> & {
  default_upstream_key?: string;
  default_mcp_key?: string;
  check_model_key?: string;
};

export interface CircuitState {
  status: 'closed' | 'open' | string;
  failures: number;
  open_remaining_seconds: number;
}

export interface GatewayRoute {
  id: string;
  name: string;
  match_model: string;
  target_model: string;
  upstream_base: string;
  api_key_configured: boolean;
  priority: number;
  enabled: boolean;
  input_price_per_million: number;
  output_price_per_million: number;
  circuit: CircuitState;
}

export interface GatewayRouteInput {
  name: string;
  match_model: string;
  target_model: string;
  upstream_base: string;
  api_key?: string;
  priority: number;
  enabled: boolean;
  input_price_per_million: number;
  output_price_per_million: number;
}

export type GatewayRouteUpdate = Partial<GatewayRouteInput>;
export interface ConnectionTestResult { ok: boolean; kind: string; message: string; model?: string }
