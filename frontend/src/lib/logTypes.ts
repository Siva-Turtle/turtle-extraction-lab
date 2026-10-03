// Shared log + comparison types (extracted from Logs.tsx; behaviour unchanged).

export type Rating = "up" | "down";

export type FeedbackEntry = { rating: Rating | ""; remarks: string; auto?: boolean };

export type FeedbackMap = Record<string, Record<string, FeedbackEntry>>;

export type ConsistencyAgent = {
  agent_name: string;
  predicted: string[];
  extracted: string[];
  missed: string[];
  unexpected: string[];
  score: number;
};

export type Consistency = {
  score: number | null;
  identifier_agent_id?: string;
  agents: Record<string, ConsistencyAgent>;
};

export type AgentUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | null;
  input_cost_usd?: number | null;
  output_cost_usd?: number | null;
  duration_ms: number;
  model: string;
  reused_from_log_id?: string;
  reused_from_created_at?: string | null;
};

export type RunUsage = AgentUsage & {
  per_agent: Record<string, AgentUsage>;
  reused_agents?: Record<string, { log_id: string; created_at: string }>;
};

export type LogFilters = {
  client_id?: string;
  client_name?: string;
  meeting_title?: string;
  meeting_date?: string;
  meeting_id?: string;
};

export type LogRow = {
  id: string;
  run_id: string;
  input_type: string;
  input_data: string;
  model: string;
  agent_snapshot: Record<string, { name: string }>;
  attribute_snapshot?: Record<string, unknown>;
  outputs: Record<string, unknown>;
  requests?: Record<string, unknown>;
  feedback: Record<string, Record<string, { rating: string; remarks: string; auto?: boolean }>>;
  consistency?: Consistency | null;
  usage?: RunUsage;
  filters?: LogFilters;
  // Denormalized meeting snapshot (plain strings, "" on old rows).
  client?: string;
  meeting_type?: string;
  meeting_title?: string;
  // Denormalized snapshot ("" on old rows).
  reasoning_effort?: string;
  created_at: string;
  // Multi-model compare: the backend always ships a string ("" on old rows).
  run_group_id: string;
  // Reused output (POST /runs/reuse copies an old log into a new row in the
  // same group): "" = real model call. Optional for old rows / old backends.
  reused_from_log_id?: string;
  reused_from_created_at?: string | null;
};

export type ModelSlot = { model: string; effort: string };

export type ColumnStatus = "queued" | "running" | "done" | "partial" | "error";

export type CompareColumn = {
  key: string;
  model: string;
  effort: string;
  status: ColumnStatus;
  startedAt?: number;
  error?: string;
  log: LogRow | null;
};

export type CompareAgent = {
  id: string;
  name: string;
  kind: string;
  attributes: { name: string; type: string; description: string; group: string }[];
};
