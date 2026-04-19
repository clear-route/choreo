/**
 * Pure transform functions for the Run Summary view.
 *
 * Converts API response types (RunSummary) into component prop types
 * (HeroCardProps, RunRow). No React imports. No side effects.
 * Unit-testable without a DOM.
 */

import type { RunSummary } from "@/api/types";
import {
  formatDuration as _formatDuration,
  fmtMs as _fmtMs,
  formatTime as _formatTime,
} from "@/utils/format";

// ── Component prop types ──

export interface ProportionSegment {
  key: "passed" | "slow" | "failed" | "errored" | "skipped";
  fraction: number;
}

export interface SubLineStat {
  label: string;
  colour?: string;
  isMono?: boolean;
}

export interface HeroCardProps {
  headline: string;
  headlineOutcome: "pass" | "fail" | "slow";
  duration: string;
  segments: ProportionSegment[];
  stats: SubLineStat[];
}

export interface RunRow {
  id: string;
  startedAt: string;
  duration: string;
  total: number;
  passed: number;
  failed: number;
  slow: number;
  passRate: string;          // pre-formatted: "98.0%"
  outcome: "pass" | "fail" | "slow";
  anomalyCount: number;
}

export interface RunTrendPoint {
  runId: string;
  time: string;            // pre-formatted: "19 Apr 09:44"
  passRate: number;        // 0–100
  failed: number;
  slow: number;
  total: number;
  durationSec: number;     // duration in seconds for the chart
}

// ── Transform functions ──

/** Convert a RunSummary to HeroCard props. */
export function toHeroProps(run: RunSummary): HeroCardProps {
  const total = run.total_tests;
  const outcome = resolveOutcome(run);

  const headline = buildHeadline(run, outcome);

  const segments = toSegments(run);

  const stats: SubLineStat[] = [
    { label: `${total} tests` },
    { label: `\u2713 ${run.total_passed} passed`, colour: "pass" },
  ];
  if (run.total_failed > 0) {
    stats.push({ label: `${run.total_failed} failed`, colour: "fail" });
  }
  if (run.total_errored > 0) {
    stats.push({ label: `${run.total_errored} errored`, colour: "fail" });
  }
  if (run.total_slow > 0) {
    stats.push({ label: `${run.total_slow} slow`, colour: "slow" });
  }
  if (run.total_skipped > 0) {
    stats.push({ label: `${run.total_skipped} skipped` });
  }

  if (run.p50_ms != null) {
    stats.push({ label: `p50 ${formatLatency(run.p50_ms)}`, isMono: true });
  }
  if (run.p95_ms != null) {
    stats.push({ label: `p95 ${formatLatency(run.p95_ms)}`, isMono: true });
  }
  if (run.p99_ms != null) {
    stats.push({ label: `p99 ${formatLatency(run.p99_ms)}`, isMono: true });
  }

  stats.push({ label: formatTimestamp(run.started_at) });

  return {
    headline,
    headlineOutcome: outcome,
    duration: formatDuration(run.duration_ms),
    segments,
    stats,
  };
}

/** Convert a list of RunSummary responses to RunTable rows. */
export function toRunRows(
  runs: RunSummary[],
  displayCount: number,
): RunRow[] {
  return runs.slice(0, displayCount).map((run) => ({
    id: run.id,
    startedAt: formatTimestamp(run.started_at),
    duration: formatDuration(run.duration_ms),
    total: run.total_tests,
    passed: run.total_passed,
    failed: run.total_failed,
    slow: run.total_slow,
    passRate: `${(run.pass_rate * 100).toFixed(1)}%`,
    outcome: resolveOutcome(run),
    anomalyCount: run.anomaly_count,
  }));
}

/** Convert runs to time-series chart data in chronological order (oldest first). */
export function toRunTrend(runs: RunSummary[]): RunTrendPoint[] {
  if (runs.length === 0) return [];
  return [...runs].reverse().map((r) => ({
    runId: r.id,
    time: formatTimestamp(r.started_at),
    passRate: Math.round(r.pass_rate * 1000) / 10,
    failed: r.total_failed + r.total_errored,
    slow: r.total_slow,
    total: r.total_tests,
    durationSec: Math.round(r.duration_ms / 100) / 10,  // one decimal
  }));
}

// ── Formatting helpers (re-exported from @/utils/format) ──

export const formatDuration = _formatDuration;
export const formatLatency = _fmtMs;
export const formatTimestamp = _formatTime;

// ── Internal helpers ──

function resolveOutcome(run: RunSummary): "pass" | "fail" | "slow" {
  if (run.total_failed > 0 || run.total_errored > 0) return "fail";
  if (run.total_slow > 0) return "slow";
  return "pass";
}

function buildHeadline(
  run: RunSummary,
  outcome: "pass" | "fail" | "slow",
): string {
  if (outcome === "fail") {
    const failCount = run.total_failed + run.total_errored;
    return `${failCount} of ${run.total_tests} tests failed`;
  }
  if (outcome === "slow") {
    return `${run.total_slow} of ${run.total_tests} tests slow`;
  }
  return `${run.total_passed} of ${run.total_tests} tests passed`;
}

function toSegments(run: RunSummary): ProportionSegment[] {
  const total = run.total_tests;
  if (total === 0) return [];

  const segments: ProportionSegment[] = [];
  const add = (key: ProportionSegment["key"], count: number) => {
    if (count > 0) segments.push({ key, fraction: count / total });
  };

  add("passed", run.total_passed);
  add("slow", run.total_slow);
  add("failed", run.total_failed);
  add("errored", run.total_errored);
  add("skipped", run.total_skipped);

  return segments;
}
