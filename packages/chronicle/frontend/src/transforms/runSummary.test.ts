import { describe, it, expect } from "vitest";
import {
  toHeroProps,
  toRunRows,
  formatDuration,
  formatLatency,
  formatTimestamp,
} from "./runSummary";
import type { RunSummary } from "@/api/types";

function mockRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: "run-1",
    tenant_slug: "test",
    started_at: "2026-04-19T09:00:00Z",
    finished_at: "2026-04-19T09:01:00Z",
    duration_ms: 60000,
    environment: "staging",
    transport: "MockTransport",
    branch: "main",
    git_sha: "abc123",
    project_name: "test",
    total_tests: 100,
    total_passed: 95,
    total_failed: 3,
    total_errored: 0,
    total_skipped: 0,
    total_slow: 2,
    anomaly_count: 1,
    pass_rate: 0.95,
    topic_count: 10,
    p50_ms: 5.0,
    p95_ms: 50.0,
    p99_ms: 100.0,
    ...overrides,
  };
}

describe("formatDuration", () => {
  it("should format sub-second as milliseconds", () => {
    expect(formatDuration(42)).toBe("42ms");
  });

  it("should format seconds with two decimals", () => {
    expect(formatDuration(2180)).toBe("2.18s");
  });

  it("should format minutes and seconds", () => {
    expect(formatDuration(72000)).toBe("1m 12s");
  });
});

describe("formatLatency", () => {
  it("should show <1ms for sub-millisecond values", () => {
    expect(formatLatency(0.5)).toBe("<1ms");
  });

  it("should show one decimal for values under 100ms", () => {
    expect(formatLatency(42.7)).toBe("42.7ms");
  });

  it("should round values over 100ms", () => {
    expect(formatLatency(142.7)).toBe("143ms");
  });

  it("should show seconds for values over 1000ms", () => {
    expect(formatLatency(1500)).toBe("1.5s");
  });
});

describe("formatTimestamp", () => {
  it("should format as DD Mon, HH:MM", () => {
    const result = formatTimestamp("2026-04-19T09:44:00Z");
    expect(result).toContain("19");
    expect(result).toContain("Apr");
  });
});

describe("toHeroProps", () => {
  it("should produce a fail headline when tests failed", () => {
    const props = toHeroProps(mockRun({ total_failed: 3 }));
    expect(props.headlineOutcome).toBe("fail");
    expect(props.headline).toContain("failed");
  });

  it("should produce a pass headline when all tests passed", () => {
    const props = toHeroProps(mockRun({ total_failed: 0, total_slow: 0, total_passed: 100 }));
    expect(props.headlineOutcome).toBe("pass");
    expect(props.headline).toContain("passed");
  });

  it("should produce a slow headline when tests are slow but none failed", () => {
    const props = toHeroProps(mockRun({ total_failed: 0, total_slow: 5 }));
    expect(props.headlineOutcome).toBe("slow");
    expect(props.headline).toContain("slow");
  });

  it("should format duration in the hero card", () => {
    const props = toHeroProps(mockRun({ duration_ms: 43342 }));
    expect(props.duration).toBe("43.34s");
  });

  it("should produce proportion segments that sum to 1", () => {
    const props = toHeroProps(mockRun({
      total_passed: 90, total_failed: 5, total_slow: 3, total_skipped: 2, total_tests: 100,
    }));
    const total = props.segments.reduce((s, seg) => s + seg.fraction, 0);
    expect(total).toBeCloseTo(1.0);
  });

  it("should include percentile stats when available", () => {
    const props = toHeroProps(mockRun({ p50_ms: 5.0, p95_ms: 50.0 }));
    const labels = props.stats.map((s) => s.label);
    expect(labels.some((l) => l.includes("p50"))).toBe(true);
    expect(labels.some((l) => l.includes("p95"))).toBe(true);
  });

  it("should omit percentile stats when null", () => {
    const props = toHeroProps(mockRun({ p50_ms: null, p95_ms: null, p99_ms: null }));
    const labels = props.stats.map((s) => s.label);
    expect(labels.some((l) => l.includes("p50"))).toBe(false);
  });
});

describe("toRunRows", () => {
  it("should convert runs to rows with pre-formatted values", () => {
    const rows = toRunRows([mockRun()], 50);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.id).toBe("run-1");
    expect(rows[0]!.passRate).toBe("95.0%");
    expect(rows[0]!.outcome).toBe("fail");
  });

  it("should limit to displayCount", () => {
    const runs = Array.from({ length: 10 }, (_, i) =>
      mockRun({ id: `run-${i}` }),
    );
    const rows = toRunRows(runs, 5);
    expect(rows).toHaveLength(5);
  });

  it("should handle empty runs list", () => {
    expect(toRunRows([], 50)).toEqual([]);
  });
});
