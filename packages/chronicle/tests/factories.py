"""Report builder utilities for Chronicle tests.

Provides ``make_report()``, ``make_scenario()``, and ``make_handle()``
helpers that build valid test-report-v1 JSON dicts with sensible defaults.
"""

from __future__ import annotations


def make_handle(
    *,
    topic: str = "orders.created",
    outcome: str = "pass",
    latency_ms: float | None = 5.2,
    budget_ms: float | None = 50.0,
    matcher_description: str = "contains_fields({'status': 'CREATED'})",
    expected: object = None,
    actual: object = None,
    attempts: int = 0,
    reason: str = "matched",
    truncated: bool = False,
    failure: object = None,
    failures: list[dict] | None = None,
    failures_dropped: int = 0,
    diagnosis: dict | None = None,
) -> dict:
    """Build a valid handle dict for embedding in a scenario."""
    if expected is None:
        expected = {"status": "CREATED"}
    if actual is None:
        actual = {"status": "CREATED", "id": 42}
    if diagnosis is None:
        diagnosis = {"kind": "matched"}
    return {
        "topic": topic,
        "outcome": outcome,
        "latency_ms": latency_ms,
        "budget_ms": budget_ms,
        "matcher_description": matcher_description,
        "expected": expected,
        "actual": actual,
        "attempts": attempts,
        "reason": reason,
        "truncated": truncated,
        "failure": failure,
        "failures": failures if failures is not None else [],
        "failures_dropped": failures_dropped,
        "diagnosis": diagnosis,
    }


def make_scenario(
    *,
    name: str = "test-scenario",
    correlation_id: str | None = "corr-1",
    outcome: str = "pass",
    duration_ms: float = 10.0,
    completed_normally: bool = True,
    handles: list[dict] | None = None,
    timeline: list[dict] | None = None,
    timeline_dropped: int = 0,
    replies: list[dict] | None = None,
    summary_text: str = "",
) -> dict:
    """Build a valid scenario dict for embedding in a test."""
    return {
        "name": name,
        "correlation_id": correlation_id,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "completed_normally": completed_normally,
        "handles": handles if handles is not None else [],
        "timeline": timeline if timeline is not None else [],
        "timeline_dropped": timeline_dropped,
        "replies": replies if replies is not None else [],
        "summary_text": summary_text,
    }


def make_report(
    *,
    schema_version: str = "1",
    transport: str = "MockTransport",
    environment: str | None = "test",
    branch: str | None = "main",
    git_sha: str | None = "deadbeef",
    hostname: str | None = "test-host",
    project_name: str | None = "test",
    started_at: str = "2026-04-19T01:00:00+00:00",
    finished_at: str = "2026-04-19T01:00:01+00:00",
    duration_ms: float = 1000.0,
    total_passed: int = 1,
    total_failed: int = 0,
    total_errored: int = 0,
    total_skipped: int = 0,
    total_slow: int = 0,
    total: int | None = None,
    scenarios: list[dict] | None = None,
    handles: list[dict] | None = None,
    tests: list[dict] | None = None,
    **overrides: object,
) -> dict:
    """Build a valid test-report-v1 JSON dict with sensible defaults.

    If ``scenarios`` is provided, it is used as the scenario list for the
    single default test.  If ``handles`` is provided without ``scenarios``,
    a default scenario wrapping those handles is created.

    If ``tests`` is provided it overrides the entire tests list.
    """
    computed_total = (
        total
        if total is not None
        else (total_passed + total_failed + total_errored + total_skipped + total_slow)
    )

    if tests is not None:
        test_list = tests
    else:
        if scenarios is None:
            if handles is not None:
                scenarios = [make_scenario(handles=handles)]
            else:
                scenarios = [make_scenario()]
        test_list = [
            {
                "nodeid": "tests/test_foo.py::test_bar",
                "file": "tests/test_foo.py",
                "name": "test_bar",
                "class": None,
                "markers": [],
                "choreo_meta": None,
                "outcome": "passed",
                "duration_ms": 10.0,
                "traceback": None,
                "stdout": "",
                "stderr": "",
                "log": "",
                "skip_reason": None,
                "worker_id": None,
                "scenarios": scenarios,
            }
        ]

    report: dict = {
        "schema_version": schema_version,
        "run": {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "totals": {
                "passed": total_passed,
                "failed": total_failed,
                "errored": total_errored,
                "skipped": total_skipped,
                "slow": total_slow,
                "total": computed_total,
            },
            "project_name": project_name,
            "transport": transport,
            "allowlist_path": None,
            "python_version": "3.13.0",
            "harness_version": "0.1.0",
            "reporter_version": "0.1.0",
            "git_sha": git_sha,
            "git_branch": branch,
            "environment": environment,
            "hostname": hostname,
            "xdist": None,
            "truncated": False,
            "redactions": {"fields": 0, "stream_matches": 0},
        },
        "tests": test_list,
    }

    for k, v in overrides.items():
        report[k] = v

    return report
