/** TypeScript types matching Chronicle's Pydantic response schemas. */

// ── Common ──

export interface PagedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ErrorResponse {
  error: string;
  detail: string;
}

// ── Tenants ──

export interface TenantSummary {
  id: string;
  slug: string;
  name: string;
  created_at: string;
  run_count: number;
}

// ── Runs ──

export interface RunSummary {
  id: string;
  tenant_slug: string;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  environment: string | null;
  transport: string;
  branch: string | null;
  git_sha: string | null;
  project_name: string | null;
  total_tests: number;
  total_passed: number;
  total_failed: number;
  total_errored: number;
  total_skipped: number;
  total_slow: number;
  anomaly_count: number;
  // PRD-010 additions
  pass_rate: number;
  topic_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
}

export interface ScenarioSummary {
  id: string;
  test_nodeid: string;
  name: string;
  correlation_id: string | null;
  outcome: string;
  duration_ms: number;
  completed_normally: boolean;
  handle_count: number;
}

export interface RunDetail extends RunSummary {
  scenarios: ScenarioSummary[];
}

// ── Topics ──

export interface TopicSummary {
  topic: string;
  latest_run_at: string;
  sample_count: number;
  latest_p50_ms: number | null;
  latest_p95_ms: number | null;
  latest_p99_ms: number | null;
  slow_count: number;
  timeout_count: number;
}

export interface LatencyBucket {
  bucket: string;
  sample_count: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  min_ms: number | null;
  max_ms: number | null;
  slow_count: number;
  timeout_count: number;
  fail_count: number;
  budget_violation_count: number;
}

export interface TopicLatencyResponse {
  topic: string;
  tenant: string;
  environment: string | null;
  resolution: string;
  buckets: LatencyBucket[];
}

export interface TopicRunSummary {
  run_id: string;
  started_at: string;
  environment: string | null;
  branch: string | null;
  handle_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  slow_count: number;
  timeout_count: number;
}

// ── Anomalies ──

export interface AnomalyCard {
  id: string;
  tenant_id: string;
  run_id: string;
  detected_at: string;
  topic: string;
  detection_method: string;
  metric: string;
  current_value: number;
  baseline_value: number;
  baseline_stddev: number;
  change_pct: number;
  severity: "warning" | "critical";
  resolved: boolean;
  resolved_at: string | null;
}

// ── Ingest ──

export interface IngestResponse {
  run_id: string;
  duplicate: boolean;
  handles_ingested: number;
  scenarios_ingested: number;
  warning: string | null;
}
