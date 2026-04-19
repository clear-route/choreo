"""Property-based tests for the normalisation pipeline.

Uses Hypothesis to generate arbitrary valid (and edge-case) report
structures, verifying that ``normalise_report`` never crashes and
always produces consistent output regardless of input shape.

These tests catch edge cases that hand-crafted fixtures miss:
empty lists, unicode topic names, extreme numeric values, deeply
nested structures, null fields, etc.
"""

from __future__ import annotations

from datetime import datetime

from chronicle.schemas.ingest import IngestRequest
from chronicle.services.normalise import normalise_report
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# ── Strategies ──

_diagnosis_kinds = st.sampled_from(
    ["matched", "over_budget", "near_miss", "silent_timeout", "pending"]
)

_outcomes = st.sampled_from(["pass", "fail", "timeout", "slow", "pending"])

_scenario_outcomes = st.sampled_from(["pass", "fail", "timeout", "slow"])

_topic_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=100,
)


@st.composite
def diagnosis_strategy(draw: st.DrawFn) -> dict:
    kind = draw(_diagnosis_kinds)
    d: dict = {"kind": kind}
    if kind == "over_budget":
        d["latency_ms"] = draw(st.floats(min_value=0.01, max_value=1e6))
        d["budget_ms"] = draw(st.floats(min_value=0.01, max_value=1e6))
    elif kind == "silent_timeout":
        d["topic"] = draw(_topic_names)
        d["deadline_ms"] = draw(st.floats(min_value=0.01, max_value=1e6))
    elif kind == "near_miss":
        d["attempts"] = draw(st.integers(min_value=1, max_value=100))
        d["deadline_ms"] = draw(st.floats(min_value=0.01, max_value=1e6))
    return d


@st.composite
def handle_strategy(draw: st.DrawFn) -> dict:
    outcome = draw(_outcomes)
    has_latency = outcome in ("pass", "slow", "fail")
    diagnosis = draw(diagnosis_strategy())

    return {
        "topic": draw(_topic_names),
        "outcome": outcome,
        "latency_ms": draw(st.floats(min_value=0.01, max_value=1e6)) if has_latency else None,
        "budget_ms": draw(st.one_of(st.none(), st.floats(min_value=0.01, max_value=1e6))),
        "matcher_description": draw(st.text(max_size=200)),
        "expected": None,
        "actual": None,
        "attempts": draw(st.integers(min_value=0, max_value=1000)),
        "reason": draw(st.text(max_size=200)),
        "truncated": False,
        "failure": None,
        "failures": [],
        "failures_dropped": 0,
        "diagnosis": diagnosis,
    }


@st.composite
def scenario_strategy(draw: st.DrawFn) -> dict:
    handles = draw(st.lists(handle_strategy(), min_size=0, max_size=10))
    return {
        "name": draw(st.text(min_size=1, max_size=100)),
        "correlation_id": draw(st.one_of(st.none(), st.text(min_size=1, max_size=50))),
        "outcome": draw(_scenario_outcomes),
        "duration_ms": draw(st.floats(min_value=0, max_value=1e6)),
        "completed_normally": draw(st.booleans()),
        "handles": handles,
        "timeline": [],
        "timeline_dropped": 0,
        "replies": [],
        "summary_text": "",
    }


@st.composite
def report_strategy(draw: st.DrawFn) -> dict:
    scenarios = draw(st.lists(scenario_strategy(), min_size=0, max_size=5))
    passed = draw(st.integers(min_value=0, max_value=1000))
    failed = draw(st.integers(min_value=0, max_value=1000))
    errored = draw(st.integers(min_value=0, max_value=100))
    skipped = draw(st.integers(min_value=0, max_value=100))
    slow = draw(st.integers(min_value=0, max_value=100))
    total = passed + failed + errored + skipped + slow

    return {
        "schema_version": "1",
        "run": {
            "started_at": "2026-04-19T01:00:00+00:00",
            "finished_at": "2026-04-19T01:00:01+00:00",
            "duration_ms": draw(st.floats(min_value=0, max_value=1e9)),
            "totals": {
                "passed": passed,
                "failed": failed,
                "errored": errored,
                "skipped": skipped,
                "slow": slow,
                "total": total,
            },
            "project_name": draw(st.one_of(st.none(), st.text(max_size=50))),
            "transport": draw(st.text(min_size=1, max_size=50)),
            "allowlist_path": None,
            "python_version": "3.13.0",
            "harness_version": "0.1.0",
            "reporter_version": "0.1.0",
            "git_sha": draw(st.one_of(st.none(), st.text(max_size=40))),
            "git_branch": draw(st.one_of(st.none(), st.text(max_size=100))),
            "environment": draw(st.one_of(st.none(), st.text(max_size=50))),
            "hostname": draw(st.one_of(st.none(), st.text(max_size=100))),
            "xdist": None,
            "truncated": False,
            "redactions": {"fields": 0, "stream_matches": 0},
        },
        "tests": [
            {
                "nodeid": f"tests/test_{i}.py::test_generated",
                "file": f"tests/test_{i}.py",
                "name": "test_generated",
                "class": None,
                "markers": [],
                "choreo_meta": None,
                "outcome": "passed",
                "duration_ms": 1.0,
                "traceback": None,
                "stdout": "",
                "stderr": "",
                "log": "",
                "skip_reason": None,
                "worker_id": None,
                "scenarios": [scenarios[i]] if i < len(scenarios) else [],
            }
            for i in range(max(1, len(scenarios)))
        ],
    }


# ── Property tests ──


class TestNormaliseNeverCrashes:
    """normalise_report should handle any valid report without raising."""

    @given(report=report_strategy())
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=200)
    def test_normalise_should_not_crash_on_any_valid_report(self, report: dict) -> None:
        request = IngestRequest(**report)
        result = normalise_report(request)

        # Basic invariants that must always hold
        assert isinstance(result.started_at, datetime)
        assert isinstance(result.finished_at, datetime)
        assert result.duration_ms >= 0
        assert result.total_tests >= 0
        assert result.scenario_count >= 0
        assert result.handle_count >= 0

    @given(report=report_strategy())
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_handle_count_should_equal_sum_of_scenario_handles(self, report: dict) -> None:
        request = IngestRequest(**report)
        result = normalise_report(request)

        expected_count = sum(len(s.handles) for s in result.scenarios)
        assert result.handle_count == expected_count

    @given(report=report_strategy())
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_topics_should_be_subset_of_handle_topics(self, report: dict) -> None:
        request = IngestRequest(**report)
        result = normalise_report(request)

        all_handle_topics = {h.topic for s in result.scenarios for h in s.handles}
        assert result.topics == all_handle_topics


class TestOverBudgetDerivation:
    """over_budget should be True iff diagnosis.kind == 'over_budget'."""

    @given(report=report_strategy())
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=200)
    def test_over_budget_should_match_diagnosis_kind(self, report: dict) -> None:
        request = IngestRequest(**report)
        result = normalise_report(request)

        for scenario in result.scenarios:
            for handle in scenario.handles:
                if handle.diagnosis_kind == "over_budget":
                    assert handle.over_budget is True
                else:
                    assert handle.over_budget is False


class TestNormalisedHandlesAreImmutable:
    """NormalisedScenario.handles should be a tuple (immutable)."""

    @given(report=report_strategy())
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=50)
    def test_handles_should_be_tuples_not_lists(self, report: dict) -> None:
        request = IngestRequest(**report)
        result = normalise_report(request)

        for scenario in result.scenarios:
            assert isinstance(scenario.handles, tuple)
