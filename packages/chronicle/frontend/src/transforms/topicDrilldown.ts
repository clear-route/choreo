import type { LatencyBucket } from "@/api/types";
import { fmtMs } from "@/utils/format";

export interface TopicSummaryStats {
  p50: string;
  p95: string;
  p99: string;
  totalSamples: number;
  totalFail: number;
  totalSlowAndTimeout: number;
  totalBudgetViolations: number;
  resolution: string;
}

/** Aggregate a series of latency buckets into the summary stats shown in TopicSummaryStrip. */
export function toTopicSummaryStats(buckets: LatencyBucket[], resolution: string): TopicSummaryStats {
  const latest = buckets[buckets.length - 1];

  const totalSamples = buckets.reduce((s, b) => s + b.sample_count, 0);
  const totalFail = buckets.reduce((s, b) => s + b.fail_count, 0);
  const totalSlow = buckets.reduce((s, b) => s + b.slow_count, 0);
  const totalTimeout = buckets.reduce((s, b) => s + b.timeout_count, 0);
  const totalBudgetViolations = buckets.reduce((s, b) => s + b.budget_violation_count, 0);

  return {
    p50: fmtMs(latest?.p50_ms ?? null),
    p95: fmtMs(latest?.p95_ms ?? null),
    p99: fmtMs(latest?.p99_ms ?? null),
    totalSamples,
    totalFail,
    totalSlowAndTimeout: totalSlow + totalTimeout,
    totalBudgetViolations,
    resolution,
  };
}
