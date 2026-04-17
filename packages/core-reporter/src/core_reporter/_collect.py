"""Session collection — PRD-007 §2.

Bridges the pytest hook stream and the scenario-observer callback into a
single in-memory run document that the serialiser turns into JSON at
session-finish.

State shape:

  Collector
    |
    +-- tests: dict[nodeid, TestRecord]
    |     |
    |     +-- scenarios: list[ScenarioRecord]
    |           (appended each time the observer fires)
    |
    +-- run metadata (started_at, transport, env, ...)

Per the PRD, the observer keys each scenario to the currently-active
pytest nodeid via the `core._reporting.current_test_nodeid` contextvar.
A scenario fired outside any test (e.g. in a session fixture) is routed
to a synthetic nodeid `<session>` so the data is not lost.
"""
from __future__ import annotations

import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.scenario import ScenarioResult

from ._redact import RedactionStats, redact_stream
from ._serialise import (
    SCENARIOS_CAP,
    TESTS_CAP,
    cap_stream,
    derive_test_outcome,
    serialise_scenario,
)


_SESSION_KEY = "<session>"


@dataclass
class ScenarioRecord:
    result: ScenarioResult
    completed_normally: bool


@dataclass
class TestRecord:
    nodeid: str
    file: str = ""
    name: str = ""
    class_name: str | None = None
    markers: list[str] = field(default_factory=list)
    pytest_outcome: str = "passed"
    duration_ms: float = 0.0
    traceback: str | None = None
    stdout: str = ""
    stderr: str = ""
    log: str = ""
    skip_reason: str | None = None
    worker_id: str | None = None
    scenarios: list[ScenarioRecord] = field(default_factory=list)
    _setup_failed: bool = False


@dataclass
class Collector:
    started_at: str = ""
    finished_at: str = ""
    started_mono: float = 0.0
    tests: dict[str, TestRecord] = field(default_factory=dict)
    transport: str = "unknown"
    allowlist_path: str | None = None
    environment: str | None = None
    project_name: str | None = None
    hostname: str = field(default_factory=socket.gethostname)
    python_version: str = field(
        default_factory=lambda: sys.version.split()[0]
    )
    stats: RedactionStats = field(default_factory=RedactionStats)
    truncated: bool = False

    # ----- run lifecycle -------------------------------------------------

    def start_run(self, mono: float) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_mono = mono

    def finish_run(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    # ----- per-test wiring ----------------------------------------------

    def ensure_test(self, nodeid: str) -> TestRecord:
        rec = self.tests.get(nodeid)
        if rec is None:
            rec = TestRecord(nodeid=nodeid)
            self.tests[nodeid] = rec
        return rec

    def note_test_metadata(
        self,
        nodeid: str,
        *,
        file: str,
        name: str,
        class_name: str | None,
        markers: list[str],
        worker_id: str | None,
    ) -> None:
        rec = self.ensure_test(nodeid)
        rec.file = file
        rec.name = name
        rec.class_name = class_name
        rec.markers = markers
        rec.worker_id = worker_id

    def handle_report(self, report: Any) -> None:
        """Consume a pytest `TestReport` for setup/call/teardown phases."""
        nodeid = report.nodeid
        rec = self.ensure_test(nodeid)
        phase = getattr(report, "when", "call")

        captured_stdout = getattr(report, "capstdout", "") or ""
        captured_stderr = getattr(report, "capstderr", "") or ""
        captured_log = getattr(report, "caplog", "") or ""
        if captured_stdout:
            rec.stdout += captured_stdout
        if captured_stderr:
            rec.stderr += captured_stderr
        if captured_log:
            rec.log += captured_log

        outcome = report.outcome
        if phase == "setup":
            if outcome == "failed":
                rec._setup_failed = True
                rec.pytest_outcome = "errored"
                rec.traceback = _longrepr(report)
            elif outcome == "skipped":
                # `@pytest.mark.skip(...)` surfaces as a skipped SETUP
                # phase with no subsequent call phase.
                rec.pytest_outcome = "skipped"
                rec.skip_reason = _skip_reason(report)
        elif phase == "call":
            rec.duration_ms = getattr(report, "duration", 0.0) * 1000
            if rec._setup_failed:
                return  # errored already recorded
            if outcome == "skipped":
                # `pytest.skip(...)` called from inside the test body.
                rec.pytest_outcome = "skipped"
                rec.skip_reason = _skip_reason(report)
            elif outcome == "failed":
                rec.pytest_outcome = "failed"
                rec.traceback = _longrepr(report)
            else:
                rec.pytest_outcome = "passed"
        elif phase == "teardown" and outcome == "failed":
            # A teardown failure after a passing call becomes `errored`.
            if rec.pytest_outcome == "passed":
                rec.pytest_outcome = "errored"
                rec.traceback = _longrepr(report)

    # ----- observer bridge ----------------------------------------------

    def record_scenario(
        self,
        result: ScenarioResult,
        nodeid: str | None,
        completed_normally: bool,
    ) -> None:
        key = nodeid or _SESSION_KEY
        rec = self.ensure_test(key)
        rec.scenarios.append(
            ScenarioRecord(result=result, completed_normally=completed_normally)
        )

    # ----- serialisation -------------------------------------------------

    def to_dict(
        self,
        *,
        reporter_version: str,
        harness_version: str,
        git_sha: str | None,
        git_branch: str | None,
        xdist: dict[str, Any] | None,
        final_duration_ms: float,
        stream_redact: bool = True,
    ) -> dict[str, Any]:
        def _stream(text: str) -> str:
            if stream_redact:
                text = redact_stream(text, self.stats)
            return cap_stream(text)

        tests_json: list[dict[str, Any]] = []
        scenario_outcomes_per_test: dict[str, list[str]] = {}

        for nodeid, rec in list(self.tests.items())[:TESTS_CAP]:
            scenarios = [
                serialise_scenario(
                    s.result,
                    duration_ms=_scenario_duration_ms(s.result),
                    completed_normally=s.completed_normally,
                    stats=self.stats,
                )
                for s in rec.scenarios[:SCENARIOS_CAP]
            ]
            scenario_outcomes_per_test[nodeid] = [
                s["outcome"] for s in scenarios
            ]
            tests_json.append(
                {
                    "nodeid": nodeid,
                    "file": rec.file,
                    "name": rec.name,
                    "class": rec.class_name,
                    "markers": rec.markers,
                    "outcome": derive_test_outcome(
                        rec.pytest_outcome,
                        scenario_outcomes_per_test[nodeid],
                    ),
                    "duration_ms": rec.duration_ms,
                    "traceback": rec.traceback,
                    "stdout": _stream(rec.stdout),
                    "stderr": _stream(rec.stderr),
                    "log": _stream(rec.log),
                    "skip_reason": rec.skip_reason,
                    "worker_id": rec.worker_id,
                    "scenarios": scenarios,
                }
            )

        totals = compute_totals(tests_json)

        return {
            "schema_version": "1",
            "run": {
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_ms": final_duration_ms,
                "totals": totals,
                "project_name": self.project_name,
                "transport": self.transport,
                "allowlist_path": self.allowlist_path,
                "python_version": self.python_version,
                "harness_version": harness_version,
                "reporter_version": reporter_version,
                "git_sha": git_sha,
                "git_branch": git_branch,
                "environment": self.environment,
                "hostname": self.hostname,
                "xdist": xdist,
                "truncated": self.truncated,
                "redactions": {
                    "fields": self.stats.fields,
                    "stream_matches": self.stats.stream_matches,
                },
            },
            "tests": tests_json,
        }


def _scenario_duration_ms(result: ScenarioResult) -> float:
    if not result.timeline:
        return 0.0
    return max(e.offset_ms for e in result.timeline)


def compute_totals(tests: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "passed": 0,
        "failed": 0,
        "errored": 0,
        "skipped": 0,
        "slow": 0,
        "total": 0,
    }
    for t in tests:
        counts["total"] += 1
        outcome = t.get("outcome", "passed")
        if outcome in counts:
            counts[outcome] += 1
    return counts


def _longrepr(report: Any) -> str:
    lr = getattr(report, "longrepr", None)
    if lr is None:
        return ""
    return str(lr)


def _skip_reason(report: Any) -> str | None:
    lr = getattr(report, "longrepr", None)
    if lr is None:
        return None
    # Pytest's skip longrepr is typically a 3-tuple (file, line, reason).
    if isinstance(lr, tuple) and len(lr) >= 3:
        return str(lr[2])
    return str(lr)
