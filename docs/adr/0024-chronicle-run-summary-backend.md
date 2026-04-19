# 0024. Chronicle Run Summary Backend — Query Endpoints and Schema Extensions for View 1

**Status:** Proposed
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-010 — Chronicle Dashboard Views](../prd/PRD-010-chronicle-dashboard-views.md), View 1; [ADR-0023 — Run Summary View](0023-chronicle-run-summary-view.md)

---

## Context

The Run Summary frontend (ADR-0023) requires data from two backend
sources:

1. `GET /api/v1/runs` — already exists, returns `RunSummary` objects.
   But the schema is missing five fields needed by the Hero Card and
   sparkline: `pass_rate`, `topic_count`, `p50_ms`, `p95_ms`, `p99_ms`.
2. `GET /api/v1/tenants` — already exists and working.

The repository layer (`RunRepository.list_runs`) already returns `Run`
ORM objects with all the totals columns. The route handler
(`api/runs.py`) already maps runs to `RunSummary` Pydantic models. The
gap is: the new fields require data that is either computable from
existing columns (`pass_rate`) or requires a subquery against
`handle_measurements` (`topic_count`, `p50_ms`, `p95_ms`, `p99_ms`).

ADR-0021 established the Repository + Service Layer pattern: routes are
thin, repositories own SQL, services own business logic. This ADR
records how the new fields are computed, where that computation lives,
and the query strategy for keeping the run list endpoint fast.

### Problem Statement

How should the five new `RunSummary` fields be computed and served
without degrading the run list endpoint's response time (< 200ms p99
target per PRD-009)?

### Goals

- **Add `pass_rate`, `topic_count`, `p50_ms`, `p95_ms`, `p99_ms` to
  the `RunSummary` response** without breaking existing consumers.
- **Keep `GET /api/v1/runs` under 200ms p99** for a 50-run page.
- **No new database tables or schema migrations** — use computed fields
  and efficient queries against existing tables.
- **Add `branch` query parameter** to `GET /api/v1/runs` for the "Filter
  by branch" interaction from ADR-0023.

### Non-Goals

- Implementing the regression endpoint (`GET /api/v1/regression`) — that
  is a separate ADR.
- Adding anomaly query filters (`run`, `severity`, `method`, `topic`) —
  separate work.
- Frontend changes — ADR-0023 covers the component architecture.

---

## Decision Drivers

- **`pass_rate` is trivially computable.** `total_passed / total_tests`
  — no database query needed. This is a Pydantic computed field.
- **`topic_count` requires a subquery.** Counting distinct topics per
  run requires querying `handle_measurements` or the raw JSONB. A
  subquery per run in the list is an N+1 risk.
- **Percentiles require aggregation.** `p50_ms`, `p95_ms`, `p99_ms`
  across all handles in a run require `percentile_cont` against
  `handle_measurements`. Again, a subquery per run is expensive.
- **The run list is the most-hit endpoint.** Every page load, every SSE
  invalidation, and every filter change triggers this query. It must
  stay fast.

---

## Considered Options

### Option 1: Compute all fields at query time via subqueries

**Description:** Add a lateral subquery to `list_runs` that computes
`topic_count`, `p50_ms`, `p95_ms`, `p99_ms` per run from
`handle_measurements`.

```sql
SELECT r.*,
       stats.topic_count,
       stats.p50_ms,
       stats.p95_ms,
       stats.p99_ms
FROM runs r
LEFT JOIN LATERAL (
    SELECT count(DISTINCT topic) AS topic_count,
           percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
               FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
               FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
               FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms
    FROM handle_measurements hm
    WHERE hm.run_id = r.id
) stats ON true
WHERE r.tenant_id = :tenant_id
ORDER BY r.started_at DESC
LIMIT 50;
```

**Pros:**
- No schema changes — fields are computed on the fly.
- Always up to date — no stale data risk.
- Single query — no N+1.

**Cons:**
- `percentile_cont` is an aggregate function that requires scanning all
  handles per run. For 50 runs x 200 handles = 10,000 rows. Not expensive
  for uncompressed data, but compressed chunks (> 7 days old) must be
  decompressed for the lateral join. This could push the query past 200ms.
- The lateral join prevents the query planner from using the
  `idx_runs_tenant_time` index efficiently — it must execute the
  subquery for each of the 50 runs.

### Option 2: Denormalise into the `runs` table (chosen)

**Description:** Add `topic_count`, `p50_ms`, `p95_ms`, `p99_ms` as
nullable columns on the `runs` table, populated during ingest. The
`pass_rate` field is computed at the Pydantic layer (no column needed).

**Pros:**
- **The run list query stays trivial.** `SELECT * FROM runs WHERE
  tenant_id = :id ORDER BY started_at DESC LIMIT 50` — no joins, no
  subqueries, no aggregation. The existing `idx_runs_tenant_time` index
  serves the query directly.
- **Ingest already scans all handles.** The COPY step iterates every
  handle to build the `records` list. Computing `topic_count` and
  percentiles during this iteration adds negligible overhead (one pass
  over in-memory data).
- **Consistent with existing pattern.** `total_passed`, `total_failed`,
  `total_slow` etc. are already denormalised on `runs` for the same
  reason — fast list queries without joins.
- **Nullable columns require no data migration.** Existing rows get
  `NULL` for the new columns. New rows are populated during ingest.
  The frontend treats `null` as "data not available" (hides the
  percentile sub-line).

**Cons:**
- Four new columns on the `runs` table. Adds ~32 bytes per row
  (4 x DOUBLE PRECISION). Negligible at the target scale.
- Values are set once at ingest and never updated. If a bug in the
  percentile computation is fixed, historical rows retain the old values.
  Mitigated by: a one-time backfill script can recompute from
  `handle_measurements` if needed.
- Requires an Alembic migration to add the columns.

### Option 3: Compute in the Pydantic serialisation layer from raw JSONB

**Description:** Extract `topic_count` and percentiles from the
`raw_report` JSONB column during Pydantic model construction.

**Pros:**
- No schema migration. No new columns.
- The data is already in the JSONB.

**Cons:**
- **Parsing JSONB for 50 runs is expensive.** PostgreSQL must decompress
  and traverse the JSONB tree for each run. A JSONB with 200 handles
  requires iterating the nested `tests[].scenarios[].handles[]` structure.
  For 50 runs, this is 50 JSONB parses with nested array traversal.
- **JSONB extraction in PostgreSQL is slower than columnar access** by
  an order of magnitude. This defeats the purpose of denormalisation.
- **Percentile computation in Pydantic** means loading all handle
  latencies into Python memory for each run. For 50 runs x 200 handles
  = 10,000 floats — manageable but wasteful compared to computing once
  at ingest.

---

## Decision

**Chosen Option:** Option 2 — Denormalise into the `runs` table.

### Rationale

- Option 1's lateral subquery with `percentile_cont` across 50 runs
  risks exceeding the 200ms NFR, particularly for compressed chunks.
  The query complexity also makes it harder to maintain.
- Option 3's JSONB parsing is the worst of both worlds — slow and
  complex, with no caching benefit.
- Option 2 follows the established pattern (denormalised totals on
  `runs`), keeps the list query trivial, and computes the values at
  ingest time when the data is already in memory.

The `pass_rate` field is computed at the Pydantic layer as a `@computed_field`,
not stored in the database — it is a trivial division of two existing
columns.

---

## Consequences

### Positive

- **Run list query stays a single indexed SELECT.** No joins, no
  subqueries, no aggregation. The `idx_runs_tenant_time` index serves
  the query directly. Response time remains well under 200ms.
- **Ingest overhead is negligible.** Computing `topic_count` and
  percentiles from the in-memory handle list adds < 1ms to the ingest
  pipeline (one pass over the list that is already being iterated for
  COPY record construction).
- **Frontend receives all fields in a single response.** No secondary
  API calls needed for the Hero Card sub-line or sparkline data.

### Negative

- **Historical runs have NULL for the new fields.** Mitigated by: the
  frontend handles nulls (omits percentile sub-line). A backfill script
  can recompute values from `handle_measurements` if needed.
- **Four new columns on `runs`.** Mitigated by: consistent with the six
  existing denormalised total columns. Same pattern, same trade-off.

### Neutral

- An Alembic migration adds the four nullable columns. This is a
  non-destructive change — no data loss, no downtime.
- `pass_rate` is not stored — it is computed per-request from
  `total_passed / total_tests`. If `total_tests` is 0, `pass_rate`
  is 0.0.

### Security Considerations

N/A — this decision adds read-only numeric columns and a computed field.
No new input surfaces, no new query parameters beyond `branch` (which
is already validated by Pydantic). See ADR-0021 §Security Considerations.

---

## Implementation

### 1. Alembic Migration

Add four nullable columns to `runs`:

```python
# migrations/versions/NNN_add_run_stats.py
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("runs", sa.Column("topic_count", sa.Integer, nullable=True))
    op.add_column("runs", sa.Column("p50_ms", sa.Float, nullable=True))
    op.add_column("runs", sa.Column("p95_ms", sa.Float, nullable=True))
    op.add_column("runs", sa.Column("p99_ms", sa.Float, nullable=True))


def downgrade():
    op.drop_column("runs", "p99_ms")
    op.drop_column("runs", "p95_ms")
    op.drop_column("runs", "p50_ms")
    op.drop_column("runs", "topic_count")
```

### 2. ORM Model Update

Add the columns to `models/tables.py`:

```python
class Run(Base):
    # ... existing columns ...

    # Denormalised stats (populated during ingest)
    topic_count: Mapped[int | None] = mapped_column(Integer)
    p50_ms: Mapped[float | None] = mapped_column(Float)
    p95_ms: Mapped[float | None] = mapped_column(Float)
    p99_ms: Mapped[float | None] = mapped_column(Float)
```

### 3. Pydantic Schema Update

Add the new fields to `schemas/runs.py`:

```python
class RunSummary(BaseModel):
    model_config = {"from_attributes": True}

    # ... existing fields ...

    # New fields (PRD-010)
    topic_count: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None

    @computed_field
    @property
    def pass_rate(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return self.total_passed / self.total_tests
```

`pass_rate` is a Pydantic `@computed_field` — it appears in the JSON
response but is not stored in the database. The denominator check
prevents division by zero.

### 4. Ingest Pipeline Update

Compute `topic_count` and percentiles during normalisation. The
normalisation step already iterates all handles to derive `over_budget`
and `diagnosis_kind` — the stats computation piggybacks on this loop.

```python
# services/normalise.py (within the normalisation function)
import statistics

latencies = [
    h.latency_ms
    for h in all_handles
    if h.latency_ms is not None
]
topics = {h.topic for h in all_handles}

normalised.topic_count = len(topics)

if latencies:
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    normalised.p50_ms = _percentile(latencies_sorted, 0.50, n)
    normalised.p95_ms = _percentile(latencies_sorted, 0.95, n)
    normalised.p99_ms = _percentile(latencies_sorted, 0.99, n)

def _percentile(sorted_data: list[float], p: float, n: int) -> float:
    """Linear interpolation percentile, matching PostgreSQL's
    percentile_cont behaviour."""
    k = (n - 1) * p
    f = int(k)
    c = f + 1 if f + 1 < n else f
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])
```

The `_percentile` function uses linear interpolation to match
PostgreSQL's `percentile_cont` behaviour, ensuring that values computed
at ingest match values that would be returned by a direct database query.

### 5. Route Handler Update

The existing `GET /api/v1/runs` list endpoint in `api/runs.py` needs
minimal changes:

```python
# api/runs.py — updated list endpoint

@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    tenant: Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    branch: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RunListResponse:
    """List runs for a tenant, newest first."""
    # Resolve tenant slug to ID
    tenant_row = await run_repo.get_tenant_by_slug(tenant)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    runs, total = await run_repo.list_runs(
        tenant_row.id,
        environment=environment,
        branch=branch,
        limit=limit,
        offset=offset,
    )

    return RunListResponse(
        items=[
            RunSummary(
                id=r.id,
                tenant_slug=tenant,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_ms=r.duration_ms,
                environment=r.environment,
                transport=r.transport,
                branch=r.branch,
                git_sha=r.git_sha,
                project_name=r.project_name,
                total_tests=r.total_tests,
                total_passed=r.total_passed,
                total_failed=r.total_failed,
                total_errored=r.total_errored,
                total_skipped=r.total_skipped,
                total_slow=r.total_slow,
                topic_count=r.topic_count or 0,
                p50_ms=r.p50_ms,
                p95_ms=r.p95_ms,
                p99_ms=r.p99_ms,
            )
            for r in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
```

Key additions:
- `tenant` query parameter (required) — resolves slug to UUID, returns
  404 if not found. This replaces the previous pattern where routes did
  not validate tenant existence.
- `branch` query parameter (optional) — passed through to `list_runs`
  for the "Filter by branch" interaction.
- New fields mapped from the ORM object.

### 6. Repository Addition

Add a `get_tenant_by_slug` method to `RunRepository`:

```python
async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
    stmt = select(Tenant).where(Tenant.slug == slug)
    result = await self.session.execute(stmt)
    return result.scalar_one_or_none()
```

This is a single indexed lookup (`tenants.slug` has a UNIQUE index).

### Migration Path

Not applicable — additive changes only. No existing behaviour changes.

### Timeline

1. Alembic migration (add 4 columns).
2. ORM model update (add 4 mapped columns).
3. Pydantic schema update (add 5 fields including computed `pass_rate`).
4. Normalisation update (compute stats during ingest).
5. Route handler update (add `tenant`/`branch` params, map new fields).
6. Repository addition (`get_tenant_by_slug`).
7. Tests.

---

## Validation

### Success Metrics

- **`GET /api/v1/runs?tenant=X&limit=50` responds in < 200ms p99.**
  Measured by: integration test timing against a real TimescaleDB with
  100+ runs. The query must be a single SELECT with no joins.
- **New fields are present in the response.** Measured by: API test
  that ingests a report with handles, fetches the run list, and asserts
  `pass_rate`, `topic_count`, `p50_ms`, `p95_ms`, `p99_ms` are populated
  with correct values.
- **`pass_rate` matches `total_passed / total_tests`.** Measured by:
  unit test on the Pydantic schema.
- **Percentile values match PostgreSQL's `percentile_cont`.** Measured
  by: integration test that compares the denormalised values against a
  direct `percentile_cont` query on `handle_measurements`.
- **Historical runs return null for new fields.** Measured by: test
  that queries a run ingested before the migration and asserts
  `p50_ms` is null, `topic_count` is 0.
- **`branch` filter works.** Measured by: API test that ingests runs
  on two branches, filters by one, and asserts only matching runs return.

### Monitoring

- Query timing on `GET /api/v1/runs` logged via SQLAlchemy
  `after_cursor_execute` hook. Alert if p99 exceeds 150ms (80% of
  the 200ms NFR target).

---

## Related Decisions

- [ADR-0021](0021-chronicle-api-structure.md) — Repository + Service
  Layer pattern. This ADR follows the same structure: route handlers are
  thin, repositories own SQL, computed fields live in Pydantic schemas.
- [ADR-0023](0023-chronicle-run-summary-view.md) — Frontend component
  architecture. Defines the `HeroCardProps` and `RunRow` interfaces
  that consume the fields added here.
- [PRD-010](../prd/PRD-010-chronicle-dashboard-views.md) — View
  specifications. Defines which fields the Hero Card and Run Table need.

---

## References

- [PostgreSQL `percentile_cont`](https://www.postgresql.org/docs/current/functions-aggregate.html#FUNCTIONS-ORDEREDSET-TABLE) —
  the aggregate function whose behaviour the Python `_percentile` helper
  matches
- [Pydantic `computed_field`](https://docs.pydantic.dev/latest/concepts/fields/#computed-fields) —
  used for `pass_rate`

---

## Notes

- **Open follow-up — backfill script.** Runs ingested before this
  migration have `NULL` for `topic_count`, `p50_ms`, `p95_ms`, `p99_ms`.
  A one-time backfill script can recompute these from
  `handle_measurements`:
  ```sql
  UPDATE runs SET
      topic_count = (SELECT count(DISTINCT topic) FROM handle_measurements WHERE run_id = runs.id),
      p50_ms = (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms) FROM handle_measurements WHERE run_id = runs.id AND latency_ms IS NOT NULL),
      p95_ms = (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FROM handle_measurements WHERE run_id = runs.id AND latency_ms IS NOT NULL),
      p99_ms = (SELECT percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) FROM handle_measurements WHERE run_id = runs.id AND latency_ms IS NOT NULL)
  WHERE topic_count IS NULL;
  ```
  Run this manually after migration if historical data needs populating.
  **Owner:** Chronicle maintainers.

- **Open follow-up — `topic_count` accuracy for runs without handles.**
  Some runs (e.g. the Chronicle test suite itself) have tests but no
  Choreo scenarios/handles. For these runs, `topic_count` is 0, which
  is correct — they have no message bus topics. The frontend renders
  "0" in the Topics column, which accurately communicates "this run did
  not test any topics." **Owner:** N/A — documented for clarity.

**Last Updated:** 2026-04-19
