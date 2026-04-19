"""Unit tests for the anomaly detection service.

These tests are pure -- no database, no mocks, no I/O. They pass in-memory
data and assert on the returned ``NewAnomaly`` dataclasses.

Follows the project test naming convention: behaviour-oriented names
using "should" / "should not" (CLAUDE.md §Test style).
"""

from datetime import UTC
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

TENANT_ID = uuid4()
RUN_ID = uuid4()


def _make_report(
    handles: list[NormalisedHandle],
) -> NormalisedReport:
    """Build a minimal NormalisedReport with the given handles."""
    from datetime import datetime

    return NormalisedReport(
        started_at=datetime(2026, 4, 19, tzinfo=UTC),
        finished_at=datetime(2026, 4, 19, 0, 1, tzinfo=UTC),
        duration_ms=60000.0,
        environment="staging",
        transport="MockTransport",
        branch="main",
        git_sha="abc123",
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
                test_nodeid="test.py::test_foo",
                name="test-scenario",
                correlation_id="corr-1",
                outcome="pass",
                duration_ms=100.0,
                completed_normally=True,
                handles=tuple(handles),
            )
        ],
    )


def _handle(
    *,
    topic: str = "test.topic",
    outcome: str = "pass",
    latency_ms: float | None = 10.0,
    budget_ms: float | None = None,
    over_budget: bool = False,
    diagnosis_kind: str | None = "matched",
) -> NormalisedHandle:
    return NormalisedHandle(
        topic=topic,
        outcome=outcome,
        latency_ms=latency_ms,
        budget_ms=budget_ms,
        attempts=0,
        matcher_description="eq('X')",
        diagnosis_kind=diagnosis_kind,
        over_budget=over_budget,
    )


class TestRollingBaselineDetection:
    """Detection method 1: rolling baseline comparison."""

    def test_a_latency_spike_beyond_two_sigma_should_create_a_warning_anomaly(
        self,
    ) -> None:
        service = DetectionService(DetectionConfig(baseline_sigma=2.0))
        # Baseline: 10 runs at ~10ms
        baselines = {"test.topic": [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 10.0, 9.7, 10.4]}
        # Current: 50ms -- well beyond 2 sigma
        report = _make_report([_handle(latency_ms=50.0)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        assert len(anomalies) == 1
        a = anomalies[0]
        assert a.detection_method == "rolling_baseline"
        assert a.metric == "p95_ms"
        assert a.severity in ("warning", "critical")
        assert a.topic == "test.topic"
        assert a.current_value == 50.0

    def test_a_latency_spike_beyond_three_sigma_should_create_a_critical_anomaly(
        self,
    ) -> None:
        service = DetectionService(DetectionConfig(baseline_sigma=2.0))
        baselines = {"test.topic": [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 10.0, 9.7, 10.4]}
        report = _make_report([_handle(latency_ms=100.0)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        assert len(anomalies) >= 1
        assert anomalies[0].severity == "critical"

    def test_latency_within_normal_range_should_not_create_an_anomaly(
        self,
    ) -> None:
        service = DetectionService()
        baselines = {"test.topic": [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 10.0, 9.7, 10.4]}
        report = _make_report([_handle(latency_ms=10.5)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        # Only baseline anomalies -- should be empty
        baseline_anomalies = [a for a in anomalies if a.detection_method == "rolling_baseline"]
        assert len(baseline_anomalies) == 0

    def test_identical_baseline_values_should_not_produce_false_anomalies(
        self,
    ) -> None:
        """When stddev is 0, no anomaly should be reported (division guard)."""
        service = DetectionService()
        baselines = {"test.topic": [10.0] * 10}
        report = _make_report([_handle(latency_ms=10.5)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        baseline_anomalies = [a for a in anomalies if a.detection_method == "rolling_baseline"]
        assert len(baseline_anomalies) == 0


class TestInsufficientBaseline:
    """Detection should not fire with too few data points."""

    def test_insufficient_baseline_data_should_not_produce_false_anomalies(
        self,
    ) -> None:
        service = DetectionService(DetectionConfig(baseline_min_samples=5))
        # Only 3 data points -- below the 5 minimum
        baselines = {"test.topic": [10.0, 10.0, 10.0]}
        report = _make_report([_handle(latency_ms=100.0)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        assert len(anomalies) == 0

    def test_missing_baseline_for_topic_should_not_produce_anomalies(
        self,
    ) -> None:
        service = DetectionService()
        # No baseline data for this topic at all
        baselines = {}
        report = _make_report([_handle(latency_ms=100.0)])

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        assert len(anomalies) == 0


class TestBudgetViolationDetection:
    """Detection method 2: budget violation rate."""

    def test_budget_violation_rate_exceeding_threshold_should_flag_anomaly(
        self,
    ) -> None:
        service = DetectionService(
            DetectionConfig(
                budget_violation_pct=20.0,
                baseline_min_samples=1,
            )
        )
        # 3 out of 4 handles are over budget = 75%
        handles = [
            _handle(over_budget=True),
            _handle(over_budget=True),
            _handle(over_budget=True),
            _handle(over_budget=False),
        ]
        baselines = {"test.topic": [10.0] * 10}
        report = _make_report(handles)

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        budget_anomalies = [a for a in anomalies if a.detection_method == "budget_violation"]
        assert len(budget_anomalies) == 1
        assert budget_anomalies[0].current_value == 75.0

    def test_budget_violation_rate_below_threshold_should_not_flag_anomaly(
        self,
    ) -> None:
        service = DetectionService(
            DetectionConfig(
                budget_violation_pct=20.0,
                baseline_min_samples=1,
            )
        )
        # 1 out of 10 = 10%, below 20% threshold
        handles = [_handle(over_budget=False)] * 9 + [_handle(over_budget=True)]
        baselines = {"test.topic": [10.0] * 10}
        report = _make_report(handles)

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        budget_anomalies = [a for a in anomalies if a.detection_method == "budget_violation"]
        assert len(budget_anomalies) == 0


class TestMultiTopicDetection:
    """Detection should operate per-topic independently."""

    def test_detection_should_evaluate_each_topic_independently(
        self,
    ) -> None:
        service = DetectionService(DetectionConfig(baseline_sigma=2.0))
        baselines = {
            "topic.a": [10.0, 10.5, 9.8, 10.2, 10.1, 9.9, 10.3, 10.0, 9.7, 10.4],
            "topic.b": [50.0, 51.0, 49.0, 50.5, 49.5, 50.0, 51.0, 49.0, 50.5, 49.5],
        }
        handles = [
            _handle(topic="topic.a", latency_ms=50.0),  # spike on A
            _handle(topic="topic.b", latency_ms=50.0),  # normal for B
        ]
        report = _make_report(handles)

        anomalies = service.detect(
            tenant_id=TENANT_ID,
            run_id=RUN_ID,
            normalised=report,
            baselines=baselines,
        )

        baseline_anomalies = [a for a in anomalies if a.detection_method == "rolling_baseline"]
        # Only topic.a should be flagged -- topic.b is within normal range
        assert len(baseline_anomalies) == 1
        assert baseline_anomalies[0].topic == "topic.a"
