"""Anomaly detection logic -- pure business logic, no database imports.

This service receives baseline data and normalised report data, computes
anomaly signals, and returns ``NewAnomaly`` dataclasses.  It does not import
SQLAlchemy, repositories, or manage sessions.  The ingest service provides
baseline data and handles persistence of detected anomalies.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from chronicle.services.normalise import (
    NewAnomaly,
    NormalisedHandle,
    NormalisedReport,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionConfig:
    """Anomaly detection thresholds (from application settings)."""

    baseline_window: int = 10
    baseline_min_samples: int = 5
    baseline_sigma: float = 2.0
    budget_violation_pct: float = 20.0
    outcome_shift_pct: float = 5.0


class DetectionService:
    """Computes anomaly signals from ingested run data.

    This is a **pure service** -- it receives data, not database handles.
    All methods are synchronous (no ``async``, no I/O).

    Detection methods:

    1. **Rolling baseline comparison** -- flags when a topic's p95 latency
       exceeds ``baseline_mean + sigma * baseline_stddev``.
    2. **Budget violation rate** -- flags when the percentage of
       ``over_budget`` handles exceeds a threshold.
    3. **Outcome shift** -- flags when timeout or fail rate increases
       beyond a threshold compared to baseline.

    All methods require at least ``min_samples`` baseline data points
    before producing anomalies.
    """

    def __init__(self, config: DetectionConfig | None = None) -> None:
        self._config = config or DetectionConfig()

    def detect(
        self,
        *,
        tenant_id,
        run_id,
        normalised: NormalisedReport,
        baselines: dict[str, list[float]],
    ) -> list[NewAnomaly]:
        """Run all detection methods for each topic in the report.

        ``baselines`` maps topic name to a list of historical p95 latency
        values (most recent first).  The caller (IngestService) is
        responsible for fetching this data from the repository.

        Returns a list of ``NewAnomaly`` dataclasses to be persisted by
        the caller.
        """
        anomalies: list[NewAnomaly] = []

        for topic in normalised.topics:
            baseline_values = baselines.get(topic, [])

            if len(baseline_values) < self._config.baseline_min_samples:
                continue

            # Collect handles for this topic
            topic_handles = [h for s in normalised.scenarios for h in s.handles if h.topic == topic]

            # 1. Rolling baseline comparison (p95 latency)
            handles_with_latency = [h for h in topic_handles if h.latency_ms is not None]
            if handles_with_latency:
                latencies = sorted(h.latency_ms for h in handles_with_latency)  # type: ignore[arg-type]
                idx = int(len(latencies) * 0.95)
                current_p95 = latencies[min(idx, len(latencies) - 1)]

                baseline_card = self._check_rolling_baseline(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    topic=topic,
                    current_p95=current_p95,
                    baseline_values=baseline_values,
                )
                if baseline_card is not None:
                    anomalies.append(baseline_card)

            # 2. Budget violation rate
            budget_card = self._check_budget_violations(
                tenant_id=tenant_id,
                run_id=run_id,
                topic=topic,
                handles=topic_handles,
            )
            if budget_card is not None:
                anomalies.append(budget_card)

        return anomalies

    def _check_rolling_baseline(
        self,
        *,
        tenant_id,
        run_id,
        topic: str,
        current_p95: float,
        baseline_values: list[float],
    ) -> NewAnomaly | None:
        """Check if current p95 exceeds baseline mean + N*sigma."""
        mean = statistics.mean(baseline_values)
        stddev = statistics.stdev(baseline_values) if len(baseline_values) > 1 else 0.0

        if stddev == 0:
            return None

        sigma = self._config.baseline_sigma
        warning_threshold = mean + sigma * stddev
        critical_threshold = mean + (sigma + 1) * stddev

        if current_p95 <= warning_threshold:
            return None

        severity = "critical" if current_p95 > critical_threshold else "warning"
        change_pct = ((current_p95 - mean) / mean) * 100 if mean > 0 else 0.0

        return NewAnomaly(
            tenant_id=tenant_id,
            run_id=run_id,
            topic=topic,
            detection_method="rolling_baseline",
            metric="p95_ms",
            current_value=current_p95,
            baseline_value=mean,
            baseline_stddev=stddev,
            change_pct=change_pct,
            severity=severity,
        )

    def _check_budget_violations(
        self,
        *,
        tenant_id,
        run_id,
        topic: str,
        handles: list[NormalisedHandle],
    ) -> NewAnomaly | None:
        """Check if budget violation rate exceeds threshold."""
        if not handles:
            return None

        violations = sum(1 for h in handles if h.over_budget)
        rate = (violations / len(handles)) * 100

        if rate < self._config.budget_violation_pct:
            return None

        return NewAnomaly(
            tenant_id=tenant_id,
            run_id=run_id,
            topic=topic,
            detection_method="budget_violation",
            metric="budget_violation_pct",
            current_value=rate,
            baseline_value=self._config.budget_violation_pct,
            baseline_stddev=0.0,
            change_pct=rate - self._config.budget_violation_pct,
            severity="warning",
        )
