export interface RunRow {
  run_id: string;
  started: string | null;
  finished: string | null;
  scenario: string | null;
  outcome: string | null;
  goal: string | null;
  driving: boolean;
  killed: boolean;
}

export interface Task {
  id: string;
  title: string;
  depends_on?: string[];
  status?: string;
}

export interface Decision {
  stage: string;
  decision: string;
  rationale: string;
  alternatives?: string[];
  commit_sha?: string | null;
  at: string;
}

export interface GatePayload {
  gate: string;
  question: string;
  [key: string]: unknown;
}

export interface RunDetail {
  run_id: string;
  goal: string | null;
  profile: string | null;
  scenario: string | null;
  next: string[];
  driving: boolean;
  killed: boolean;
  pending_gate: GatePayload | null;
  task_idx: number;
  tasks: Task[];
  attempts: number;
  replan_budget: number | null;
  spend_usd: number;
  budget_usd: number | null;
  stages_done: string[];
  spec: Record<string, unknown> | null;
  design: Record<string, unknown> | null;
  risks: Record<string, unknown>[];
  ambiguities: string[];
  decisions: Decision[];
  sandbox: string | null;
  base_sha: string | null;
  head_sha: string | null;
  safe_stop: { reason?: string } | null;
  langfuse_url: string | null;
}

export interface RunEvent {
  type: string;
  at: string;
  seq: number;
  node?: string;
  gate?: string;
  payload?: GatePayload;
  outcome?: string;
  message?: string;
  decisions?: { decision: string; rationale: string }[];
}

export interface MetricsReport {
  run_id: string;
  scenario: string | null;
  outcome: string | null;
  started: string | null;
  finished: string | null;
  end_to_end_s: number | null;
  stage_durations: Record<string, number>;
  verification_passes: number;
  verification_failures: number;
  success_rate: number | null;
  first_attempt_success_rate: number | null;
  retries: number;
  rollbacks: number;
  mttr_s: number | null;
  unresolved_failures: number;
  cost_usd: number;
}

export interface Health {
  langfuse_enabled: boolean;
  langfuse_host: string | null;
  checkpoint_db: boolean;
  acceptance_dir: string;
  implementer_model: string;
  reasoner_model: string;
}

export interface SandboxFile {
  path: string;
  size: number;
}
