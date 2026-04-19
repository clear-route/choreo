"""Unit tests for the normalisation step of the ingest pipeline.

These tests verify that ``normalise_report()`` correctly extracts
and derives fields from raw ``test-report-v1`` JSON -- including
``over_budget``, ``diagnosis_kind``, and the flattened test->scenario->handle
hierarchy.

Uses the real ``results.json`` from the core package's test report as a
fixture alongside hand-crafted minimal reports for edge cases.
"""

from pathlib import Path

import pytest
from chronicle.schemas.ingest import IngestRequest
from chronicle.services.normalise import normalise_report
from factories import make_handle, make_report, make_scenario

REPORT_PATH = Path(__file__).resolve().parents[2] / "core" / "test-report" / "results.json"


def _to_request(report_dict: dict) -> IngestRequest:
    """Convert a factory-built report dict to an IngestRequest."""
    return IngestRequest(**report_dict)


class TestNormaliseRunMetadata:
    """Verify run-level fields are extracted correctly."""

    def test_normalise_should_extract_run_timestamps(self) -> None:
        report = _to_request(make_report())
        result = normalise_report(report)

        assert result.started_at.isoformat() == "2026-04-19T01:00:00+00:00"
        assert result.finished_at.isoformat() == "2026-04-19T01:00:01+00:00"
        assert result.duration_ms == 1000.0

    def test_normalise_should_extract_run_environment(self) -> None:
        report = _to_request(make_report(environment="dev"))
        result = normalise_report(report)

        assert result.environment == "dev"
        assert result.transport == "MockTransport"
        assert result.branch == "main"
        assert result.git_sha == "deadbeef"

    def test_normalise_should_extract_totals(self) -> None:
        report = _to_request(make_report())
        result = normalise_report(report)

        assert result.total_tests == 1
        assert result.total_passed == 1
        assert result.total_failed == 0
        assert result.total_slow == 0

    def test_normalise_should_handle_null_environment(self) -> None:
        report = _to_request(make_report(environment=None))
        result = normalise_report(report)

        assert result.environment is None


class TestNormaliseScenarios:
    """Verify scenario flattening from test->scenario hierarchy."""

    def test_normalise_should_flatten_scenarios_from_all_tests(self) -> None:
        report_dict = make_report()
        # Add a second test with its own scenario
        report_dict["tests"].append(
            {
                "nodeid": "tests/test_foo.py::test_baz",
                "file": "tests/test_foo.py",
                "name": "test_baz",
                "class": None,
                "markers": [],
                "choreo_meta": None,
                "outcome": "passed",
                "duration_ms": 5.0,
                "traceback": None,
                "stdout": "",
                "stderr": "",
                "log": "",
                "skip_reason": None,
                "worker_id": None,
                "scenarios": [
                    make_scenario(
                        name="second-scenario",
                        correlation_id="corr-2",
                        duration_ms=5.0,
                    )
                ],
            }
        )
        report = _to_request(report_dict)
        result = normalise_report(report)

        assert result.scenario_count == 2
        assert result.scenarios[0].name == "test-scenario"
        assert result.scenarios[0].test_nodeid == "tests/test_foo.py::test_bar"
        assert result.scenarios[1].name == "second-scenario"
        assert result.scenarios[1].test_nodeid == "tests/test_foo.py::test_baz"

    def test_normalise_should_skip_tests_with_no_scenarios(self) -> None:
        report = _to_request(make_report(scenarios=[]))
        result = normalise_report(report)

        assert result.scenario_count == 0

    def test_normalise_should_preserve_scenario_metadata(self) -> None:
        report = _to_request(make_report())
        result = normalise_report(report)

        s = result.scenarios[0]
        assert s.correlation_id == "corr-1"
        assert s.outcome == "pass"
        assert s.duration_ms == 10.0
        assert s.completed_normally is True


class TestNormaliseHandles:
    """Verify handle extraction and field derivation."""

    def test_normalise_should_extract_handle_fields(self) -> None:
        report = _to_request(make_report(handles=[make_handle()]))
        result = normalise_report(report)

        assert result.handle_count == 1
        h = result.scenarios[0].handles[0]
        assert h.topic == "orders.created"
        assert h.outcome == "pass"
        assert h.latency_ms == 5.2
        assert h.budget_ms == 50.0
        assert h.attempts == 0
        assert h.matcher_description == "contains_fields({'status': 'CREATED'})"

    def test_normalise_should_derive_over_budget_from_diagnosis_kind(self) -> None:
        report = _to_request(
            make_report(
                handles=[
                    make_handle(
                        topic="test.topic",
                        outcome="slow",
                        latency_ms=20.0,
                        budget_ms=5.0,
                        matcher_description="eq('PASS')",
                        expected="PASS",
                        actual="PASS",
                        reason="matched but over budget",
                        diagnosis={
                            "kind": "over_budget",
                            "latency_ms": 20.0,
                            "budget_ms": 5.0,
                        },
                    )
                ]
            )
        )
        result = normalise_report(report)

        h = result.scenarios[0].handles[0]
        assert h.over_budget is True
        assert h.diagnosis_kind == "over_budget"

    def test_normalise_should_set_over_budget_false_for_matched_handles(self) -> None:
        report = _to_request(
            make_report(
                handles=[
                    make_handle(
                        topic="test.topic",
                        outcome="pass",
                        latency_ms=3.0,
                        budget_ms=50.0,
                        matcher_description="eq('PASS')",
                        expected="PASS",
                        actual="PASS",
                        diagnosis={"kind": "matched"},
                    )
                ]
            )
        )
        result = normalise_report(report)

        h = result.scenarios[0].handles[0]
        assert h.over_budget is False
        assert h.diagnosis_kind == "matched"

    def test_normalise_should_handle_timeout_with_null_latency(self) -> None:
        report = _to_request(
            make_report(
                handles=[
                    make_handle(
                        topic="never.arrives",
                        outcome="timeout",
                        latency_ms=None,
                        budget_ms=None,
                        matcher_description="eq('PASS')",
                        expected="PASS",
                        actual=None,
                        reason="no message arrived",
                        diagnosis={
                            "kind": "silent_timeout",
                            "topic": "never.arrives",
                            "deadline_ms": 500.0,
                        },
                    )
                ]
            )
        )
        result = normalise_report(report)

        h = result.scenarios[0].handles[0]
        assert h.latency_ms is None
        assert h.over_budget is False
        assert h.diagnosis_kind == "silent_timeout"

    def test_normalise_should_handle_missing_diagnosis(self) -> None:
        """Handles from older reporters may not have a diagnosis field."""
        report = _to_request(
            make_report(
                handles=[
                    {
                        "topic": "test.topic",
                        "outcome": "pass",
                        "latency_ms": 5.0,
                        "budget_ms": None,
                        "matcher_description": "eq('X')",
                        "expected": "X",
                        "actual": "X",
                        "attempts": 0,
                        "reason": "matched",
                        "truncated": False,
                        "failure": None,
                        "failures": [],
                        "failures_dropped": 0,
                        # No diagnosis key at all
                    }
                ]
            )
        )
        result = normalise_report(report)

        h = result.scenarios[0].handles[0]
        assert h.diagnosis_kind is None
        assert h.over_budget is False


class TestNormaliseConvenienceProperties:
    """Verify computed properties on NormalisedReport."""

    def test_topics_should_return_distinct_topic_names(self) -> None:
        def _simple_handle(topic: str, latency: float) -> dict:
            return make_handle(
                topic=topic,
                latency_ms=latency,
                budget_ms=None,
                matcher_description="",
                expected=None,
                actual=None,
            )

        report = _to_request(
            make_report(
                handles=[
                    _simple_handle("topic.a", 1.0),
                    _simple_handle("topic.b", 2.0),
                    _simple_handle("topic.a", 3.0),
                ]
            )
        )
        result = normalise_report(report)

        assert result.topics == {"topic.a", "topic.b"}
        assert result.handle_count == 3


class TestNormaliseRealReport:
    """Verify normalisation against the real core test report."""

    @pytest.mark.skipif(
        not REPORT_PATH.exists(),
        reason="Real test report not available",
    )
    def test_normalise_should_handle_the_real_core_test_report(self, real_report: dict) -> None:
        request = IngestRequest(**real_report)
        result = normalise_report(request)

        assert result.total_tests == 426
        assert result.total_passed == 424
        assert result.total_slow == 2
        assert result.transport == "unknown"
        assert result.branch == "main"

        # Should have scenarios from the 103 tests that have them
        assert result.scenario_count > 0

        # Should find the slow handles with over_budget
        slow_handles = [h for s in result.scenarios for h in s.handles if h.over_budget]
        assert len(slow_handles) == 2
        for h in slow_handles:
            assert h.diagnosis_kind == "over_budget"
            assert h.outcome == "slow"
            assert h.budget_ms == 5.0
            assert h.latency_ms is not None
            assert h.latency_ms > h.budget_ms

    @pytest.mark.skipif(
        not REPORT_PATH.exists(),
        reason="Real test report not available",
    )
    def test_normalise_should_extract_all_unique_topics_from_real_report(
        self, real_report: dict
    ) -> None:
        request = IngestRequest(**real_report)
        result = normalise_report(request)

        # The real report has ~20 distinct topics
        assert len(result.topics) > 10
        assert "test.topic" in result.topics
        assert "topic.a" in result.topics
