/**
 * Pure transform functions for the Run Detail view.
 * Groups raw test-report-v1 tests by file for the file tree.
 *
 * The frontend renders the raw report faithfully. It does not override
 * or recompute outcomes — what the reporter produces is what the user
 * sees. If run totals and individual test outcomes disagree, that is
 * a reporter issue, not a Chronicle issue.
 */

import type { RawTest, RawReport } from "@/hooks/useRunRaw";

export interface FileGroup {
  file: string;
  dir: string;
  basename: string;
  tests: RawTest[];
  testCount: number;
  failCount: number;
  slowCount: number;
}

/** Group tests by file path, preserving order. */
export function groupTestsByFile(report: RawReport): FileGroup[] {
  const groups = new Map<string, RawTest[]>();

  for (const test of report.tests) {
    const file = test.file || test.nodeid.split("::")[0] || "unknown";
    const existing = groups.get(file);
    if (existing) {
      existing.push(test);
    } else {
      groups.set(file, [test]);
    }
  }

  return Array.from(groups.entries()).map(([file, tests]) => {
    const lastSlash = file.lastIndexOf("/");
    return {
      file,
      dir: lastSlash >= 0 ? file.slice(0, lastSlash + 1) : "",
      basename: lastSlash >= 0 ? file.slice(lastSlash + 1) : file,
      tests,
      testCount: tests.length,
      failCount: tests.filter((t) => t.outcome === "failed" || t.outcome === "errored").length,
      slowCount: tests.filter((t) => t.outcome === "slow").length,
    };
  });
}

/** Format milliseconds for display in the detail view. */
export function formatMs(ms: number): string {
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}
