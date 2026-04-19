import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";

/** Fetch the raw test-report-v1 JSON for a run. Cached indefinitely — immutable once ingested. */
export function useRunRaw(runId: string | undefined) {
  return useQuery({
    queryKey: ["runRaw", runId],
    queryFn: () => apiFetch<RawReport>(`/runs/${encodeURIComponent(runId!)}/raw`),
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    enabled: !!runId,
  });
}

// Raw report types — minimal, just what the UI needs.
// These mirror the test-report-v1 JSON schema fields we render.

export interface RawReport {
  schema_version: string;
  run: {
    started_at: string;
    finished_at: string;
    duration_ms: number;
    totals: { passed: number; failed: number; errored: number; skipped: number; slow: number; total: number };
    [key: string]: unknown;
  };
  tests: RawTest[];
}

export interface RawTest {
  nodeid: string;
  file: string;
  name: string;
  class: string | null;
  markers: string[];
  outcome: string;
  duration_ms: number;
  traceback: string | null;
  stdout: string;
  stderr: string;
  log: string;
  skip_reason: string | null;
  worker_id: string | null;
  scenarios: RawScenario[];
}

export interface RawScenario {
  name: string;
  correlation_id: string | null;
  outcome: string;
  duration_ms: number;
  completed_normally: boolean;
  summary_text: string;
  handles: RawHandle[];
  timeline: RawTimelineEntry[];
  replies?: unknown[];
}

export interface RawHandle {
  topic: string;
  outcome: string;
  latency_ms: number | null;
  budget_ms: number | null;
  matcher_description: string;
  expected: unknown;
  actual: unknown;
  attempts: number;
  reason: string;
  truncated: boolean;
  failure: string | null;
  failures: unknown[];
  failures_dropped: number;
  diagnosis: { kind: string; [key: string]: unknown };
}

export interface RawTimelineEntry {
  offset_ms: number;
  wall_clock: string;
  topic: string;
  action: string;
  detail: string;
}
