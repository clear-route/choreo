"""Orchestrates the ingest pipeline for POST /api/v1/runs.

Pipeline steps:
1. Validate schema version (Pydantic -- already done by route handler).
2. Validate report against test-report-v1 JSON Schema.
3. Normalise (derive over_budget, diagnosis_kind, flatten structure).
4. Persist in a single transaction (run + scenarios + handle COPY).
5. Detect anomalies in a separate session (pure logic + DB reads).
6. Broadcast SSE events (always, even if detection fails).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from chronicle.broadcast import BroadcastChannel
from chronicle.exceptions import ReportValidationError
from chronicle.repositories.anomaly_repo import AnomalyRepository
from chronicle.repositories.run_repo import RunRepository
from chronicle.schemas.ingest import IngestRequest, IngestResponse
from chronicle.services.detection_service import DetectionService
from chronicle.services.normalise import (
    NewAnomaly,
    normalise_report,
)

logger = logging.getLogger(__name__)

_LATE_REPORT_THRESHOLD = timedelta(days=1)


class IngestService:
    """Orchestrates the full ingest pipeline.

    Receives repositories and a sessionmaker via dependency injection.
    The sessionmaker is used to create a **separate session** for anomaly
    detection, so the ingest connection is released back to the pool
    before detection runs.
    """

    def __init__(
        self,
        run_repo: RunRepository,
        detection: DetectionService,
        broadcast: BroadcastChannel,
        sessionmaker: async_sessionmaker,
        report_schema: dict | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._detection = detection
        self._broadcast = broadcast
        self._sessionmaker = sessionmaker
        self._report_schema = report_schema

    async def ingest(
        self,
        report: IngestRequest,
        tenant_slug: str,
        idempotency_key: str | None,
    ) -> IngestResponse:
        """Execute the full ingest pipeline.  Returns the response schema."""

        # 1. Validate against JSON Schema (level 2)
        self._validate_report(report.model_dump())

        # 2. Normalise
        normalised = normalise_report(report)

        # 3. Check for late reports
        warning: str | None = None
        age = datetime.now(UTC) - normalised.started_at
        if age > _LATE_REPORT_THRESHOLD:
            warning = (
                f"Report started_at is {age.days}d {age.seconds // 3600}h ago; "
                "it may miss the continuous aggregate refresh window."
            )
            logger.warning(
                "late report ingested",
                extra={"started_at": normalised.started_at.isoformat()},
            )

        # 4. Persist in a single transaction
        rows_inserted = 0
        try:
            async with self._run_repo.session.begin():
                tenant = await self._run_repo.upsert_tenant(tenant_slug)
                run = await self._run_repo.create_run(
                    tenant,
                    normalised=normalised,
                    raw_report=report.model_dump(),
                    idempotency_key=idempotency_key,
                )
                scenario_dicts = [
                    {
                        "test_nodeid": s.test_nodeid,
                        "name": s.name,
                        "correlation_id": s.correlation_id,
                        "outcome": s.outcome,
                        "duration_ms": s.duration_ms,
                        "completed_normally": s.completed_normally,
                    }
                    for s in normalised.scenarios
                ]
                db_scenarios = await self._run_repo.bulk_insert_scenarios(run, scenario_dicts)

                # Build handle tuples keyed by scenario ID
                handles_by_scenario: dict[UUID, list[tuple]] = {}
                for db_scenario, norm_scenario in zip(
                    db_scenarios, normalised.scenarios, strict=True
                ):
                    handle_tuples = [
                        (
                            h.topic,
                            h.outcome,
                            h.latency_ms,
                            h.budget_ms,
                            h.attempts,
                            h.matcher_description,
                            h.diagnosis_kind,
                            h.over_budget,
                        )
                        for h in norm_scenario.handles
                    ]
                    handles_by_scenario[db_scenario.id] = handle_tuples

                rows_inserted = await self._run_repo.copy_handle_measurements(
                    run, db_scenarios, handles_by_scenario
                )

                # Register topics for this tenant
                if normalised.topics:
                    await self._run_repo.upsert_topics(
                        tenant, normalised.topics, normalised.started_at
                    )

        except IntegrityError:
            # Optimistic concurrency: another request with the same
            # idempotency_key won the race.  The session is invalidated
            # after an IntegrityError, so use a fresh session to re-query.
            if idempotency_key is None:
                raise
            async with self._sessionmaker() as fresh_session:
                fresh_repo = RunRepository(fresh_session)
                existing = await fresh_repo.find_by_idempotency_key(idempotency_key)
            return IngestResponse(
                run_id=existing.id,  # type: ignore[union-attr]
                duplicate=True,
                handles_ingested=0,
                scenarios_ingested=0,
            )

        # 5. Detect anomalies in a SEPARATE session.
        #    Fetch baselines from DB, then pass data into the pure
        #    DetectionService (no repository inside detection logic).
        detected: list[NewAnomaly] = []
        try:
            async with self._sessionmaker() as detection_session:
                anomaly_repo = AnomalyRepository(detection_session)

                # Fetch baselines for each topic
                baselines: dict[str, list[float]] = {}
                for topic in normalised.topics:
                    baselines[topic] = await anomaly_repo.get_baseline_values(
                        tenant.id,
                        normalised.environment,
                        topic,
                    )

                # Pure detection -- no DB calls inside
                detected = self._detection.detect(
                    tenant_id=tenant.id,
                    run_id=run.id,
                    normalised=normalised,
                    baselines=baselines,
                )

                # Persist detected anomalies atomically
                if detected:
                    await anomaly_repo.bulk_create_from_new(detected)
                    await detection_session.commit()
        except Exception:
            logger.exception(
                "anomaly detection failed for run %s",
                run.id,
                extra={"run_id": str(run.id), "tenant": tenant_slug},
            )

        # 6. Broadcast SSE events -- always fires
        await self._broadcast.emit(
            tenant_id=tenant.id,
            event_type="run.completed",
            data={
                "run_id": str(run.id),
                "started_at": normalised.started_at.isoformat(),
                "environment": normalised.environment,
                "totals": {
                    "passed": normalised.total_passed,
                    "failed": normalised.total_failed,
                    "slow": normalised.total_slow,
                    "total": normalised.total_tests,
                },
            },
        )
        for anomaly in detected:
            await self._broadcast.emit(
                tenant_id=tenant.id,
                event_type="anomaly.detected",
                data={
                    "topic": anomaly.topic,
                    "metric": anomaly.metric,
                    "current": anomaly.current_value,
                    "baseline": anomaly.baseline_value,
                    "change_pct": anomaly.change_pct,
                    "severity": anomaly.severity,
                    "detection_method": anomaly.detection_method,
                },
            )

        return IngestResponse(
            run_id=run.id,
            duplicate=False,
            handles_ingested=rows_inserted,
            scenarios_ingested=normalised.scenario_count,
            warning=warning,
        )

    def _validate_report(self, raw: dict) -> None:
        """Validate the raw report dict against ``test-report-v1`` JSON Schema.

        Raises ``ReportValidationError`` on failure.  Skipped when no
        schema is loaded (e.g. in unit tests that only exercise normalisation).
        """
        if self._report_schema is None:
            return

        import jsonschema

        validator = jsonschema.Draft202012Validator(self._report_schema)
        errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
        if errors:
            messages = [
                f"{'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
                for e in errors[:10]  # cap to avoid huge error payloads
            ]
            raise ReportValidationError(messages)
