"""Repository for runs, scenarios, and handle measurements.

Owns all database access for the ingest write path and the run read path.
The COPY-protocol bulk insert for handle measurements bypasses the ORM.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chronicle.models.tables import Run, Scenario, Tenant, Topic
from chronicle.services.normalise import NormalisedReport

# Column list for asyncpg COPY. Derived from the ORM model to prevent drift.
# A startup assertion should verify this matches the actual table.
HANDLE_COPY_COLUMNS: list[str] = [
    "time",
    "tenant_id",
    "run_id",
    "scenario_id",
    "environment",
    "transport",
    "branch",
    "topic",
    "outcome",
    "latency_ms",
    "budget_ms",
    "attempts",
    "matcher_description",
    "diagnosis_kind",
    "over_budget",
]


class RunRepository:
    """Database access for runs, scenarios, and handle measurements."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Tenant --

    async def upsert_tenant(self, slug: str) -> Tenant:
        """Insert a tenant if it does not exist, then return it.

        Uses PostgreSQL ``INSERT ... ON CONFLICT DO NOTHING`` followed by
        a ``SELECT`` to handle concurrent inserts safely.  Always returns
        the ``Tenant`` row — whether it was newly created or already existed.
        """
        # Attempt upsert -- does nothing if slug already exists
        stmt = (
            pg_insert(Tenant)
            .values(slug=slug, name=slug)
            .on_conflict_do_nothing(index_elements=["slug"])
        )
        await self.session.execute(stmt)
        await self.session.flush()

        # Now fetch -- guaranteed to exist
        result = await self.session.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one()

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Look up a tenant by slug. Returns ``None`` if not found."""
        stmt = select(Tenant).where(Tenant.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -- Runs --

    async def find_by_idempotency_key(self, key: str) -> Run | None:
        """Look up a run by idempotency key.  Returns ``None`` if not found."""
        stmt = select(Run).where(Run.idempotency_key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_run(
        self,
        tenant: Tenant,
        *,
        normalised: NormalisedReport,
        raw_report: dict,
        idempotency_key: str | None,
    ) -> Run:
        """Insert a run row.  Called within the ingest transaction.

        Accepts a ``NormalisedReport`` and extracts fields internally.
        """
        run = Run(
            tenant_id=tenant.id,
            idempotency_key=idempotency_key,
            started_at=normalised.started_at,
            finished_at=normalised.finished_at,
            duration_ms=normalised.duration_ms,
            environment=normalised.environment,
            transport=normalised.transport,
            branch=normalised.branch,
            git_sha=normalised.git_sha,
            hostname=normalised.hostname,
            harness_version=normalised.harness_version,
            reporter_version=normalised.reporter_version,
            python_version=normalised.python_version,
            project_name=normalised.project_name,
            total_tests=normalised.total_tests,
            total_passed=normalised.total_passed,
            total_failed=normalised.total_failed,
            total_errored=normalised.total_errored,
            total_skipped=normalised.total_skipped,
            total_slow=normalised.total_slow,
            raw_report=raw_report,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def bulk_insert_scenarios(
        self,
        run: Run,
        scenarios: list[dict],
    ) -> list[Scenario]:
        """Insert scenario rows for a run.  Returns the created ORM objects
        (with ``id`` populated via flush) so the COPY step can reference them.

        Each dict in ``scenarios`` must have keys: ``test_nodeid``, ``name``,
        ``correlation_id``, ``outcome``, ``duration_ms``, ``completed_normally``.
        """
        created = []
        for s in scenarios:
            scenario = Scenario(
                run_id=run.id,
                test_nodeid=s["test_nodeid"],
                name=s["name"],
                correlation_id=s.get("correlation_id"),
                outcome=s["outcome"],
                duration_ms=s["duration_ms"],
                completed_normally=s["completed_normally"],
            )
            self.session.add(scenario)
            created.append(scenario)
        await self.session.flush()
        return created

    async def copy_handle_measurements(
        self,
        run: Run,
        scenarios: list[Scenario],
        handles_by_scenario: dict[UUID, list[tuple]],
    ) -> int:
        """Bulk-insert handle measurement rows via asyncpg's COPY protocol.

        ``handles_by_scenario`` maps ``scenario.id`` to a list of tuples,
        where each tuple contains the handle-level fields in the order
        defined by ``HANDLE_COPY_COLUMNS`` (excluding the run/scenario
        context fields which are prepended here).

        Returns the number of rows inserted.
        """
        records: list[tuple] = []
        run_time = run.started_at
        run_tenant = run.tenant_id
        run_id = run.id
        run_env = run.environment
        run_transport = run.transport
        run_branch = run.branch

        for scenario in scenarios:
            for handle_fields in handles_by_scenario.get(scenario.id, []):
                records.append(
                    (
                        run_time,
                        run_tenant,
                        run_id,
                        scenario.id,
                        run_env,
                        run_transport,
                        run_branch,
                        *handle_fields,
                    )
                )

        if not records:
            return 0

        sa_connection = await self.session.connection()
        raw_connection = await sa_connection.get_raw_connection()
        asyncpg_conn = raw_connection.driver_connection

        await asyncpg_conn.copy_records_to_table(
            "handle_measurements",
            records=records,
            columns=HANDLE_COPY_COLUMNS,
        )
        return len(records)

    async def upsert_topics(
        self,
        tenant: Tenant,
        topic_names: set[str],
        seen_at: datetime,
    ) -> None:
        """Register topics for a tenant.  Inserts new topics, updates
        ``last_seen_at`` for existing ones.  Called within the ingest
        transaction.

        Uses a single multi-row INSERT for all topics (one round trip
        instead of N).
        """
        if not topic_names:
            return
        values = [
            {
                "tenant_id": tenant.id,
                "name": name,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
            for name in topic_names
        ]
        stmt = (
            pg_insert(Topic)
            .values(values)
            .on_conflict_do_update(
                index_elements=["tenant_id", "name"],
                set_={"last_seen_at": seen_at},
            )
        )
        await self.session.execute(stmt)

    # -- Read path --

    async def list_runs(
        self,
        tenant_id: UUID,
        *,
        environment: str | None = None,
        branch: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Run], int]:
        """Return a page of runs for a tenant, newest first.

        Returns ``(runs, total_count)``.
        """
        base = select(Run).where(Run.tenant_id == tenant_id)
        if environment is not None:
            base = base.where(Run.environment == environment)
        if branch is not None:
            base = base.where(Run.branch == branch)

        count_result = await self.session.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar_one()

        rows_result = await self.session.execute(
            base.order_by(Run.started_at.desc()).limit(limit).offset(offset)
        )
        return list(rows_result.scalars().all()), total

    async def get_run(self, run_id: UUID) -> Run | None:
        """Fetch a single run by ID, with tenant eagerly loaded."""
        stmt = select(Run).where(Run.id == run_id).options(selectinload(Run.tenant))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_run_with_scenarios(self, run_id: UUID) -> Run | None:
        """Fetch a single run by ID with tenant and scenarios eagerly loaded."""
        stmt = (
            select(Run)
            .where(Run.id == run_id)
            .options(
                selectinload(Run.tenant),
                selectinload(Run.scenarios),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_run_raw_report(self, run_id: UUID) -> dict | None:
        """Fetch only the ``raw_report`` JSONB for a run.  Returns ``None``
        if the run does not exist."""
        stmt = select(Run.raw_report).where(Run.id == run_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_tenants(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        """Return a page of tenants ordered by creation date, with run counts.

        Each returned object has ``id``, ``slug``, ``name``, ``created_at``,
        and ``run_count`` attributes.
        """
        count_result = await self.session.execute(select(func.count()).select_from(Tenant))
        total = count_result.scalar_one()

        stmt = (
            select(
                Tenant.id,
                Tenant.slug,
                Tenant.name,
                Tenant.created_at,
                func.count(Run.id).label("run_count"),
            )
            .outerjoin(Run, Run.tenant_id == Tenant.id)
            .group_by(Tenant.id)
            .order_by(Tenant.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.all()), total
