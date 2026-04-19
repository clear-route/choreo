import { describe, it, expect } from "vitest";
import { groupTestsByFile, formatMs } from "./runDetail";
import type { RawReport, RawTest } from "@/hooks/useRunRaw";

function mockTest(overrides: Partial<RawTest> = {}): RawTest {
  return {
    nodeid: "tests/test_foo.py::test_bar",
    file: "tests/test_foo.py",
    name: "test_bar",
    class: null,
    markers: [],
    outcome: "passed",
    duration_ms: 10,
    traceback: null,
    stdout: "",
    stderr: "",
    log: "",
    skip_reason: null,
    worker_id: null,
    scenarios: [],
    ...overrides,
  };
}

function mockReport(tests: RawTest[]): RawReport {
  return {
    schema_version: "1",
    run: {
      started_at: "2026-04-19T09:00:00Z",
      finished_at: "2026-04-19T09:01:00Z",
      duration_ms: 60000,
      totals: { passed: tests.length, failed: 0, errored: 0, skipped: 0, slow: 0, total: tests.length },
    },
    tests,
  };
}

describe("formatMs", () => {
  it("should format sub-millisecond as <1 ms", () => {
    expect(formatMs(0.5)).toBe("<1 ms");
  });

  it("should round millisecond values", () => {
    expect(formatMs(42.7)).toBe("43 ms");
  });

  it("should format seconds for values >= 1000", () => {
    expect(formatMs(1500)).toBe("1.50s");
  });
});

describe("groupTestsByFile", () => {
  it("should group tests by file path", () => {
    const report = mockReport([
      mockTest({ file: "tests/test_a.py", name: "test_1" }),
      mockTest({ file: "tests/test_a.py", name: "test_2" }),
      mockTest({ file: "tests/test_b.py", name: "test_3" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups).toHaveLength(2);
    expect(groups[0]!.basename).toBe("test_a.py");
    expect(groups[0]!.testCount).toBe(2);
    expect(groups[1]!.basename).toBe("test_b.py");
    expect(groups[1]!.testCount).toBe(1);
  });

  it("should extract directory and basename", () => {
    const report = mockReport([
      mockTest({ file: "packages/core/tests/e2e/test_foo.py" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups[0]!.dir).toBe("packages/core/tests/e2e/");
    expect(groups[0]!.basename).toBe("test_foo.py");
  });

  it("should count failed tests", () => {
    const report = mockReport([
      mockTest({ outcome: "passed" }),
      mockTest({ outcome: "failed" }),
      mockTest({ outcome: "errored" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups[0]!.failCount).toBe(2);
  });

  it("should count slow tests", () => {
    const report = mockReport([
      mockTest({ outcome: "passed" }),
      mockTest({ outcome: "slow" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups[0]!.slowCount).toBe(1);
  });

  it("should handle tests with no file by using nodeid", () => {
    const report = mockReport([
      mockTest({ file: "", nodeid: "tests/test_x.py::test_y" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups[0]!.file).toBe("tests/test_x.py");
  });

  it("should handle empty report", () => {
    const report = mockReport([]);
    expect(groupTestsByFile(report)).toEqual([]);
  });

  it("should preserve test order within groups", () => {
    const report = mockReport([
      mockTest({ file: "a.py", name: "first" }),
      mockTest({ file: "a.py", name: "second" }),
      mockTest({ file: "a.py", name: "third" }),
    ]);

    const groups = groupTestsByFile(report);
    expect(groups[0]!.tests.map((t) => t.name)).toEqual(["first", "second", "third"]);
  });
});
