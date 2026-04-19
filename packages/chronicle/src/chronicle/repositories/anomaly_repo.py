"""Repository for anomaly storage and retrieval."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chronicle.models.tables import Anomaly
from chronicle.services.normalise import NewAnomaly


class AnomalyRepository:
    """Database access for anomalies."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_create_from_new(
        self,
        anomalies: list[NewAnomaly],
    ) -> list[Anomaly]:
        """Insert anomaly rows from ``NewAnomaly`` dataclasses.

        Called in the detection session (separate from the ingest
        transaction).  The database generates ``id`` and ``detected_at``.
        """
        created = []
        for a in anomalies:
            anomaly = Anomaly(
                tenant_id=a.tenant_id,
                run_id=a.run_id,
                topic=a.topic,
                detection_method=a.detection_method,
                metric=a.metric,
                current_value=a.current_value,
                baseline_value=a.baseline_value,
                baseline_stddev=a.baseline_stddev,
                change_pct=a.change_pct,
                severity=a.severity,
            )
            self.session.add(anomaly)
            created.append(anomaly)
        await self.session.flush()
        return created

    async def resolve(
        self,
        anomaly_id: UUID,
        resolved_at: datetime,
    ) -> None:
        """Mark an anomaly as resolved."""
        stmt = (
            update(Anomaly)
            .where(Anomaly.id == anomaly_id)
            .values(resolved=True, resolved_at=resolved_at)
        )
        await self.session.execute(stmt)

    async def list_anomalies(
        self,
        tenant_id: UUID,
        *,
        topic: str | None = None,
        severity: str | None = None,
        resolved: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Anomaly], int]:
        """Return a page of anomalies for a tenant, newest first.

        Returns ``(anomalies, total_count)``.
        """
        base = select(Anomaly).where(Anomaly.tenant_id == tenant_id)
        if topic is not None:
            base = base.where(Anomaly.topic == topic)
        if severity is not None:
            base = base.where(Anomaly.severity == severity)
        if resolved is not None:
            base = base.where(Anomaly.resolved == resolved)

        count_result = await self.session.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar_one()

        result = await self.session.execute(
            base.order_by(Anomaly.detected_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all()), total

    async def get_unresolved_for_topic(
        self,
        tenant_id: UUID,
        topic: str,
    ) -> list[Anomaly]:
        """Return all unresolved anomalies for a specific topic.

        Used by the ingest service to check resolution conditions.
        """
        stmt = (
            select(Anomaly)
            .where(
                Anomaly.tenant_id == tenant_id,
                Anomaly.topic == topic,
                Anomaly.resolved == False,  # noqa: E712
            )
            .order_by(Anomaly.detected_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_baseline_values(
        self,
        tenant_id: UUID,
        environment: str | None,
        topic: str,
        *,
        window: int = 10,
    ) -> list[float]:
        """Return the last N p95 latency values for a topic.

        Queries raw ``handle_measurements`` grouped by run, ordered by
        time descending.  Each value is the p95 latency across all
        handles for that topic in a single run.

        Returns an empty list if insufficient data points exist.
        """
        from sqlalchemy import text

        stmt = text("""
            SELECT
                percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                    FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms
            FROM handle_measurements
            WHERE tenant_id = :tenant_id
              AND topic = :topic
              AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
            GROUP BY run_id
            ORDER BY max(time) DESC
            LIMIT :window
        """)
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "topic": topic,
                "environment": environment,
                "window": window,
            },
        )
        return [row[0] for row in result.fetchall() if row[0] is not None]
