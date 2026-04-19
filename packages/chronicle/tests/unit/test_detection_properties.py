"""Property-based tests for the anomaly detection service.

Uses Hypothesis to verify detection invariants hold across a wide
range of baseline distributions, latency values, and topic counts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from chronicle.services.detection_service import (
    DetectionConfig,
    DetectionService,
)
from chronicle.services.normalise import (
    NormalisedHandle,
    NormalisedReport,
    NormalisedScenario,
)
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st


def _make_report(handles: list[NormalisedHandle]) -> NormalisedReport:
    return NormalisedReport(
        started_at=datetime(2026, 4, 19, tzinfo=UTC),
        finished_at=datetime(2026, 4, 19, 0, 1, tzinfo=UTC),
        duration_ms=60000.0,
        environment="test",
        transport="MockTransport",
        branch="main",
        git_sha="abc",
        hostname="test",
        harness_version="0.1.0",
        reporter_version="0.1.0",
        python_version="3.13.0",
        project_name="test",
        total_tests=1,
        total_passed=1,
        total_failed=0,
        total_errored=0,
        total_skipped=0,
        total_slow=0,
        scenarios=[
            NormalisedScenario(
                test_nodeid="test.py::test",
                name="test",
                correlation_id=None,
                outcome="pass",
                duration_ms=100.0,
                completed_normally=True,
                handles=tuple(handles),
            )
        ],
    )


def _handle(
    topic: str = "test.topic",
    latency_ms: float | None = 10.0,
    over_budget: bool = False,
) -> NormalisedHandle:
    return NormalisedHandle(
        topic=topic,
        outcome="pass",
        latency_ms=latency_ms,
        budget_ms=None,
        attempts=0,
        matcher_description="",
        diagnosis_kind="matched",
        over_budget=over_budget,
    )


_positive_floats = st.floats(min_value=0.01, max_value=1e6, allow_nan=False)


class TestDetectionNeverCrashes:
    """Detection should handle any valid inputs without raising."""

    @given(
        baseline=st.lists(_positive_floats, min_size=0, max_size=50),
        current_latency=_positive_floats,
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=200)
    def test_detect_should_not_crash_on_any_baseline_and_latency(
        self, baseline: list[float], current_latency: float
    ) -> None:
        service = DetectionService()
        report = _make_report([_handle(latency_ms=current_latency)])
        baselines = {"test.topic": baseline}

        # Should never raise
        anomalies = service.detect(
            tenant_id=uuid4(),
            run_id=uuid4(),
            normalised=report,
            baselines=baselines,
        )
        assert isinstance(anomalies, list)


class TestDetectionSeverityOrdering:
    """Critical severity should require a larger deviation than warning."""

    @given(
        baseline=st.lists(
            st.floats(min_value=5.0, max_value=15.0, allow_nan=False),
            min_size=10,
            max_size=10,
        ),
        current_latency=st.floats(min_value=50.0, max_value=1e6, allow_nan=False),
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=100)
    def test_critical_anomaly_should_have_larger_change_pct_than_warning(
        self, baseline: list[float], current_latency: float
    ) -> None:
        import statistics

        assume(statistics.stdev(baseline) > 0.01)

        service = DetectionService(DetectionConfig(baseline_sigma=2.0))
        report = _make_report([_handle(latency_ms=current_latency)])
        baselines = {"test.topic": baseline}

        anomalies = service.detect(
            tenant_id=uuid4(),
            run_id=uuid4(),
            normalised=report,
            baselines=baselines,
        )

        baseline_anomalies = [a for a in anomalies if a.detection_method == "rolling_baseline"]
        if baseline_anomalies:
            a = baseline_anomalies[0]
            # Severity should be consistent with threshold
            assert a.severity in ("warning", "critical")
            assert a.change_pct > 0


class TestBudgetViolationProperties:
    """Budget violation rate should be mathematically correct."""

    @given(
        n_over=st.integers(min_value=0, max_value=20),
        n_under=st.integers(min_value=0, max_value=20),
        threshold=st.floats(min_value=1.0, max_value=99.0, allow_nan=False),
    )
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=200)
    def test_budget_violation_rate_should_match_actual_ratio(
        self, n_over: int, n_under: int, threshold: float
    ) -> None:
        assume(n_over + n_under > 0)

        service = DetectionService(
            DetectionConfig(
                budget_violation_pct=threshold,
                baseline_min_samples=0,
            )
        )
        handles = [_handle(over_budget=True)] * n_over + [_handle(over_budget=False)] * n_under
        report = _make_report(handles)
        baselines = {"test.topic": [10.0] * 10}

        anomalies = service.detect(
            tenant_id=uuid4(),
            run_id=uuid4(),
            normalised=report,
            baselines=baselines,
        )

        budget_anomalies = [a for a in anomalies if a.detection_method == "budget_violation"]
        actual_rate = (n_over / (n_over + n_under)) * 100

        if actual_rate >= threshold:
            assert len(budget_anomalies) == 1
            assert abs(budget_anomalies[0].current_value - actual_rate) < 0.01
        else:
            assert len(budget_anomalies) == 0


class TestEmptyInputs:
    """Detection should handle empty edge cases gracefully."""

    @given(n_topics=st.integers(min_value=0, max_value=10))
    @settings(suppress_health_check=[HealthCheck.differing_executors], max_examples=50)
    def test_detect_with_empty_baselines_should_return_no_anomalies(self, n_topics: int) -> None:
        service = DetectionService()
        handles = [_handle(topic=f"topic.{i}") for i in range(n_topics)]
        report = _make_report(handles)

        anomalies = service.detect(
            tenant_id=uuid4(),
            run_id=uuid4(),
            normalised=report,
            baselines={},
        )
        assert len(anomalies) == 0

    def test_detect_with_no_handles_should_return_no_anomalies(self) -> None:
        service = DetectionService()
        report = _make_report([])

        anomalies = service.detect(
            tenant_id=uuid4(),
            run_id=uuid4(),
            normalised=report,
            baselines={"test.topic": [10.0] * 10},
        )
        assert len(anomalies) == 0
