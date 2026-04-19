"""Query resolution selection -- decides which data source to use.

Auto-selects raw handle_measurements, topic_latency_hourly, or
topic_latency_daily based on the requested time range.  For aggregate
sources, unions raw data for the most recent partial bucket to avoid
stale-data gaps.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from chronicle.repositories.topic_repo import LatencyBucketRow, TopicRepository

# Continuous aggregate refresh end_offsets from PRD-009.
_AGGREGATE_END_OFFSETS = {
    "hourly": timedelta(minutes=30),
    "daily": timedelta(hours=1),
}


class ResolutionService:
    """Selects the optimal data source for latency queries."""

    def __init__(self, topic_repo: TopicRepository) -> None:
        self._topic_repo = topic_repo

    async def get_topic_latency(
        self,
        topic: str,
        tenant_id: UUID,
        environment: str | None,
        start: datetime,
        end: datetime,
        resolution: str | None = None,
    ) -> tuple[list[LatencyBucketRow], str]:
        """Return latency buckets for a topic over a time range.

        Returns ``(buckets, resolution_used)`` where ``resolution_used``
        is one of ``"raw"``, ``"hourly"``, ``"daily"``.

        If ``resolution`` is ``None``, auto-selects based on the time span:
        - <= 24 hours: raw
        - <= 30 days: hourly
        - > 30 days: daily
        """
        span = end - start

        if resolution:
            source = resolution
        elif span <= timedelta(hours=24):
            source = "raw"
        elif span <= timedelta(days=30):
            source = "hourly"
        else:
            source = "daily"

        if source == "raw":
            buckets = await self._topic_repo.query_raw(topic, tenant_id, environment, start, end)
            return buckets, "raw"

        # Aggregate + partial-bucket union for freshness.
        aggregate_end = self._latest_complete_bucket(source)

        # Run both queries concurrently.
        aggregate_data, partial_data = await asyncio.gather(
            self._topic_repo.query_aggregate(
                source, topic, tenant_id, environment, start, aggregate_end
            ),
            self._topic_repo.query_raw(topic, tenant_id, environment, aggregate_end, end),
        )

        merged = self._merge(aggregate_data, partial_data)

        # Fallback: if the aggregate is empty (not yet refreshed),
        # query raw data for the full range instead of returning nothing.
        if not merged:
            buckets = await self._topic_repo.query_raw(topic, tenant_id, environment, start, end)
            return buckets, "raw"

        return merged, source

    @staticmethod
    def _latest_complete_bucket(source: str) -> datetime:
        """Compute the latest fully-materialised aggregate boundary.

        Derived from configuration (the aggregate refresh policy's
        ``end_offset``), not from a database query.
        """
        offset = _AGGREGATE_END_OFFSETS[source]
        return datetime.now(UTC) - offset

    @staticmethod
    def _merge(
        aggregate_data: list[LatencyBucketRow],
        partial_data: list[LatencyBucketRow],
    ) -> list[LatencyBucketRow]:
        """Merge aggregate and partial-bucket data.

        Raw data takes precedence in any overlap window to avoid
        double-counting.
        """
        if not partial_data:
            return aggregate_data

        # Find the earliest partial bucket timestamp
        partial_start = min(b.bucket for b in partial_data)

        # Exclude aggregate buckets that overlap with partial data
        filtered_aggregate = [b for b in aggregate_data if b.bucket < partial_start]

        return filtered_aggregate + partial_data
