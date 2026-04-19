"""Unit tests for late report detection logic.

Verifies timestamp preservation through normalisation and the threshold
constant, without any HTTP or database interaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chronicle.schemas.ingest import IngestRequest
from chronicle.services.ingest_service import _LATE_REPORT_THRESHOLD
from chronicle.services.normalise import normalise_report
from factories import make_report


def _iso_ago(days: int, hours: int = 0) -> str:
    dt = datetime.now(UTC) - timedelta(days=days, hours=hours)
    return dt.isoformat()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class TestNormalisePreservesTimestamps:
    """Verify that normalise_report faithfully extracts started_at."""

    def test_normalise_should_preserve_a_recent_started_at(self) -> None:
        now = _iso_now()
        report = IngestRequest(**make_report(started_at=now, finished_at=now))
        result = normalise_report(report)

        age = datetime.now(UTC) - result.started_at
        assert age < timedelta(minutes=1)

    def test_normalise_should_preserve_an_old_started_at(self) -> None:
        old = _iso_ago(days=7)
        report = IngestRequest(**make_report(started_at=old, finished_at=old))
        result = normalise_report(report)

        age = datetime.now(UTC) - result.started_at
        assert age >= timedelta(days=6)


class TestLateReportThreshold:
    """Verify the threshold constant is correctly defined."""

    def test_late_report_threshold_should_be_one_day(self) -> None:
        assert _LATE_REPORT_THRESHOLD == timedelta(days=1)
