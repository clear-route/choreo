"""Unit tests for the resolution service.

Tests auto-resolution selection, aggregate fallback to raw data,
and partial-bucket merge logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from chronicle.repositories.topic_repo import LatencyBucketRow, TopicRepository
from chronicle.services.resolution_service import ResolutionService

_TENANT_ID = uuid4()
_NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _bucket(*, offset_hours: int = 0, p95: float = 50.0, samples: int = 10) -> LatencyBucketRow:
    return LatencyBucketRow(
        bucket=_NOW - timedelta(hours=offset_hours),
        sample_count=samples,
        avg_ms=p95 * 0.6,
        p50_ms=p95 * 0.5,
        p95_ms=p95,
        p99_ms=p95 * 1.2,
        min_ms=1.0,
        max_ms=p95 * 2,
        slow_count=0,
        timeout_count=0,
        fail_count=0,
        budget_violation_count=0,
    )


class TestAutoResolution:
    @pytest.mark.asyncio
    async def test_span_under_24h_should_select_raw(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_raw.return_value = [_bucket()]
        svc = ResolutionService(repo)

        _, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(hours=12),
            _NOW,
        )
        assert resolution == "raw"
        repo.query_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_span_1_to_30_days_should_select_hourly(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = [_bucket()]
        repo.query_raw.return_value = []
        svc = ResolutionService(repo)

        _, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
        )
        assert resolution == "hourly"

    @pytest.mark.asyncio
    async def test_span_over_30_days_should_select_daily(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = [_bucket()]
        repo.query_raw.return_value = []
        svc = ResolutionService(repo)

        _, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=60),
            _NOW,
        )
        assert resolution == "daily"

    @pytest.mark.asyncio
    async def test_explicit_resolution_should_override_auto(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_raw.return_value = [_bucket()]
        svc = ResolutionService(repo)

        _, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
            resolution="raw",
        )
        assert resolution == "raw"


class TestAggregateFallback:
    @pytest.mark.asyncio
    async def test_empty_aggregate_should_fallback_to_raw(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = []  # aggregate empty
        # First call (partial bucket) returns [], second call (fallback) returns data
        repo.query_raw.side_effect = [[], [_bucket()]]
        svc = ResolutionService(repo)

        buckets, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
        )
        assert resolution == "raw"
        assert len(buckets) == 1

    @pytest.mark.asyncio
    async def test_empty_aggregate_and_empty_raw_should_return_empty(self):
        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = []
        repo.query_raw.return_value = []
        svc = ResolutionService(repo)

        buckets, resolution = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
        )
        assert resolution == "raw"
        assert len(buckets) == 0


class TestPartialBucketMerge:
    @pytest.mark.asyncio
    async def test_partial_data_should_take_precedence_over_aggregate(self):
        aggregate = [_bucket(offset_hours=6, p95=40.0), _bucket(offset_hours=3, p95=45.0)]
        partial = [_bucket(offset_hours=0, p95=60.0)]

        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = aggregate
        repo.query_raw.return_value = partial
        svc = ResolutionService(repo)

        buckets, _ = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
        )
        # Should have aggregate + partial (3 total)
        assert len(buckets) == 3
        # Last bucket is the partial (most recent, p95=60)
        assert buckets[-1].p95_ms == 60.0

    @pytest.mark.asyncio
    async def test_no_partial_data_should_return_aggregate_only(self):
        aggregate = [_bucket(offset_hours=6), _bucket(offset_hours=3)]

        repo = AsyncMock(spec=TopicRepository)
        repo.query_aggregate.return_value = aggregate
        repo.query_raw.return_value = []
        svc = ResolutionService(repo)

        buckets, _ = await svc.get_topic_latency(
            "t",
            _TENANT_ID,
            None,
            _NOW - timedelta(days=7),
            _NOW,
        )
        assert len(buckets) == 2
