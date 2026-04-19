# 0025. Chronicle Topic Drilldown Backend — Query Implementation and Data Serving

**Status:** Proposed
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-010 — Chronicle Dashboard Views](../prd/PRD-010-chronicle-dashboard-views.md), View 3 and View 6; [ADR-0021 — Chronicle API Structure](0021-chronicle-api-structure.md)

---

## Context

The Topic Drilldown is the analytical core of Chronicle. It answers "is
this topic getting slower?" by showing per-topic latency trends
(p50/p95/p99), outcome distribution, budget compliance, and a table of
recent runs for a topic. The Topic List (View 6) is the gateway to it: a
searchable table of all topics with their current stats.

The backend infrastructure for these views exists in stub form:

- `TopicRepository` (`repositories/topic_repo.py`) — four methods, all
  raising `NotImplementedError`. Internal row types (`LatencyBucketRow`,
  `TopicSummaryRow`, `TopicRunSummaryRow`) and their shapes are defined.
- `ResolutionService` (`services/resolution_service.py`) — fully
  implemented. Auto-selects raw/hourly/daily source based on time span.
  Unions raw data for the most recent partial bucket. Uses
  `asyncio.gather` for concurrent aggregate + partial queries.
- Pydantic schemas (`schemas/topics.py`) — `TopicLatencyResponse`,
  `TopicListResponse`, `TopicRunListResponse`, and their item types are
  defined and match the PRD-009 API surface.

What does not exist:

- SQL queries inside `TopicRepository` — the methods are interfaces only.
- `api/topics.py` — no route file. The topics router is not registered
  in `app.py`.
- Dependency injection providers — `dependencies.py` has no
  `get_topic_repo` or `get_resolution_service`.
- Tests — no unit, integration, or e2e coverage for topic queries.

### Problem Statement

How should the `TopicRepository` SQL queries be implemented, given that
they must query both ORM-mapped tables (`handle_measurements`) and
non-ORM materialized views (`topic_latency_hourly`, `topic_latency_daily`),
using TimescaleDB-specific functions (`time_bucket`, `percentile_cont`)
that SQLAlchemy does not natively support?

### Goals

- **Implement all four `TopicRepository` methods** with correct SQL that
  produces `LatencyBucketRow`, `TopicSummaryRow`, and `TopicRunSummaryRow`.
- **Register three new API endpoints** — `GET /topics`,
  `GET /topics/{topic}/latency`, `GET /topics/{topic}/runs` — following
  the thin-route pattern from ADR-0021.
- **Wire dependency injection** for `TopicRepository` and
  `ResolutionService`.
- **Keep `GET /topics/{topic}/latency` under 500ms p99** for a 30-day
  range (PRD-009 NFR target).
- **Keep `GET /topics` under 200ms p99** for the topic list.

### Non-Goals

- Implementing the regression endpoint (`GET /api/v1/regression`) —
  separate ADR.
- Frontend component architecture — ADR-0026 covers that.
- Caching beyond what ADR-0021 already specifies.
- Topic search/filtering beyond environment — deferred.

---

## Decision Drivers

- **The continuous aggregates are not ORM-mapped.** `topic_latency_hourly`
  and `topic_latency_daily` are TimescaleDB materialized views created in
  migration SQL. They have no SQLAlchemy `DeclarativeBase` model. Queries
  against them cannot use `select(SomeModel)`.
- **TimescaleDB functions are not SQLAlchemy builtins.** `time_bucket()`
  and `percentile_cont() WITHIN GROUP ... FILTER` require either raw SQL
  or custom function registration.
- **The `query_raw()` method must aggregate.** Raw `handle_measurements`
  stores one row per handle per run. The latency chart needs time-bucketed
  aggregates with percentiles. For <24h data, this aggregation happens at
  query time, not via a continuous aggregate.
- **The topic list needs "latest stats" per topic.** Showing p50/p95/p99
  for each topic requires either a subquery per topic or a window function
  over the continuous aggregate.
- **Index coverage.** All queries must be served by existing indexes:
  `idx_handles_topic (tenant_id, topic, time DESC)` and
  `idx_handles_baseline (tenant_id, environment, topic, time DESC)`.

---

## Considered Options

### Decision 1: SQL dialect for topic queries

#### Option A: Raw SQL via `text()` (chosen)

Use SQLAlchemy's `text()` construct with named bind parameters for all
topic repository queries. The results are mapped to `NamedTuple` row
types using `session.execute(text(...), params).fetchall()`.

```python
stmt = text("""
    SELECT
        time_bucket(:interval, time) AS bucket,
        count(*) AS sample_count,
        avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
        ...
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND topic = :topic
      AND time >= :start AND time < :end
    GROUP BY bucket
    ORDER BY bucket
""")
result = await self.session.execute(stmt, {
    "interval": "15 minutes",
    "tenant_id": tenant_id,
    "topic": topic,
    "start": start,
    "end": end,
})
```

**Pros:**
- **Direct expression of TimescaleDB-specific SQL.** `time_bucket`,
  `percentile_cont ... WITHIN GROUP ... FILTER` are written exactly as
  they appear in the continuous aggregate definitions. No abstraction
  layer to learn, debug, or maintain.
- **Copy-paste verifiable against the migration.** The `query_aggregate()`
  SELECT mirrors the continuous aggregate's `SELECT` list identically.
  A reviewer can diff them.
- **Named bind parameters prevent SQL injection.** `text()` with `:param`
  syntax uses asyncpg's parameterised query protocol. No string
  interpolation of user input.
- **Consistent with existing COPY usage.** The `RunRepository` already
  bypasses the ORM for bulk inserts (asyncpg COPY). Bypassing the ORM
  for complex reads is the same pragmatic trade-off.

**Cons:**
- No compile-time column name checking. A typo in a column name produces
  a runtime error, not a type error. Mitigated by: integration tests
  against a real database verify every query.
- The `source` parameter in `query_aggregate()` selects between two view
  names. The view name must be interpolated into the SQL string (not a
  bind parameter — SQL does not support parameterised table names). This
  requires a safelist check. See §Security Considerations.

#### Option B: SQLAlchemy expression language with custom functions

Register `func.time_bucket` and `func.percentile_cont` as SQLAlchemy
generic functions, then build queries using the expression language:

```python
from sqlalchemy import func

time_bucket = func.time_bucket("1 hour", HandleMeasurement.time)
stmt = (
    select(
        time_bucket.label("bucket"),
        func.count().label("sample_count"),
        func.percentile_cont(0.50)
            .within_group(HandleMeasurement.latency_ms)
            .filter(HandleMeasurement.latency_ms.isnot(None))
            .label("p50_ms"),
        ...
    )
    .where(HandleMeasurement.tenant_id == tenant_id)
    .group_by(time_bucket)
)
```

**Pros:**
- Compile-time column references (via the ORM model). A renamed column
  is caught by the type checker.
- Consistent with `RunRepository`'s read path, which uses the expression
  language for all SELECTs.
- No table-name interpolation needed — the expression language handles
  it.

**Cons:**
- **Does not work for continuous aggregates.** There is no ORM model for
  `topic_latency_hourly` or `topic_latency_daily`. The expression
  language requires a mapped class or a `Table` object. Creating dummy
  mapped classes for read-only materialized views adds maintenance
  burden — the views are created in raw SQL migrations, and a schema
  drift between the migration and the dummy model would produce silent
  query errors.
- **`WITHIN GROUP ... FILTER` is cumbersome in SQLAlchemy.** The
  expression `percentile_cont(0.50).within_group(col).filter(col.isnot(None))`
  has poor IDE support, is hard to read, and the `filter` method on
  ordered-set aggregates is not well-documented.
- **The resulting Python is harder to review than the equivalent SQL.**
  For these queries, the expression language adds verbosity without
  adding safety.

#### Option C: Hybrid (ORM for simple, raw for complex)

Use the expression language for `list_topic_runs()` (which JOINs
ORM-mapped `runs` and `handle_measurements`) and raw SQL for the
aggregate/raw queries.

**Pros:**
- Best tool for each job. The JOIN query benefits from ORM column
  references; the aggregate queries are cleaner as raw SQL.

**Cons:**
- Two SQL dialects in one repository. A contributor must understand both
  patterns to work on topic queries.
- The consistency argument from Option A is weakened.

### Decision: Option A — Raw SQL via `text()`

The continuous aggregate queries are the dominant case (3 of 4 methods).
Writing them as raw SQL is clearer, more maintainable, and directly
verifiable against the migration DDL. The `list_topic_runs()` JOIN is
straightforward enough as raw SQL. Consistency within the repository
outweighs the marginal benefit of ORM column references for one method.

---

### Decision 2: Raw bucket interval for `query_raw()`

When the resolution is `"raw"` (time span <= 24 hours), `query_raw()`
must aggregate `handle_measurements` into time buckets. The continuous
aggregates use 1-hour and 1-day intervals. The raw query needs a
shorter interval to provide useful granularity for <24h views.

#### Option A: 15-minute buckets (chosen)

```sql
time_bucket('15 minutes', time) AS bucket
```

**Rationale:** Chronicle's target deployment is continuous synthetic
probes every 5 minutes ($\approx$288 runs/day). With 15-minute buckets,
each bucket contains $\approx$3 probe runs, providing enough data points
for meaningful percentiles while keeping the bucket count manageable (96
buckets for 24 hours). This matches the design in PRD-009 §Data
Lifecycle where the resolution service selects raw data for "last 24
hours" — 96 points renders well on a line chart without overcrowding.

#### Option B: 5-minute buckets

96 x 3 = 288 buckets for 24 hours. Most buckets would contain data from
a single probe run, making percentile computation within a bucket
meaningless (percentile of one value equals that value). The chart would
show 288 points — visually noisy without statistical benefit.

#### Option C: Dynamic interval

Compute interval from actual time range (5-min for <6h, 15-min for
6-24h). Adds complexity for marginal benefit. The 15-minute fixed
interval works well across the full <24h range — 6 buckets for a 1.5h
view, 96 for 24h. The chart library (Recharts) handles both scales.

### Decision: 15-minute fixed interval

The interval is a named constant in the repository:

```python
_RAW_BUCKET_INTERVAL = "15 minutes"
```

---

### Decision 3: Topic list query strategy

#### Option A: `DISTINCT ON` against hourly aggregate (chosen)

```sql
SELECT DISTINCT ON (topic)
    topic,
    bucket AS latest_run_at,
    sample_count AS run_count,
    p50_ms AS latest_p50_ms,
    p95_ms AS latest_p95_ms,
    p99_ms AS latest_p99_ms,
    slow_count,
    timeout_count
FROM topic_latency_hourly
WHERE tenant_id = :tenant_id
ORDER BY topic, bucket DESC
```

Then wrap with a count query for pagination.

**Pros:**
- Single pass over the index. `DISTINCT ON` with `ORDER BY topic,
  bucket DESC` picks the latest bucket per topic efficiently.
- Uses the pre-computed continuous aggregate — no `percentile_cont` at
  query time.
- The index on `topic_latency_hourly` (implicit from the materialized
  view's `GROUP BY`) supports this query pattern.

**Cons:**
- **Staleness.** The hourly aggregate has a 30-minute `end_offset` —
  the latest bucket may be up to 90 minutes old (30-minute offset + up
  to 60 minutes of accumulation). For the topic list (a summary/index
  view), this staleness is acceptable — the user clicks through to the
  Topic Drilldown for fresh data. This trade-off is documented
  explicitly.
- `run_count` from the hourly aggregate is the sample count for the
  latest hour, not the total run count for the topic. The
  `TopicSummaryRow` field name is `run_count`, but semantically it
  should be renamed or clarified as "sample count in latest bucket".

**Refinement:** To get accurate `run_count` (total runs containing this
topic) and `latest_run_at` (actual timestamp, not bucket), join the
`DISTINCT ON` result with a subquery against `handle_measurements`:

```sql
WITH latest_stats AS (
    SELECT DISTINCT ON (topic)
        topic, p50_ms, p95_ms, p99_ms, slow_count, timeout_count
    FROM topic_latency_hourly
    WHERE tenant_id = :tenant_id
      AND (:environment IS NULL OR environment = :environment)
    ORDER BY topic, bucket DESC
),
topic_counts AS (
    SELECT topic,
           count(DISTINCT run_id) AS run_count,
           max(time) AS latest_run_at
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND (:environment IS NULL OR environment = :environment)
    GROUP BY topic
)
SELECT tc.topic, tc.latest_run_at, tc.run_count,
       ls.p50_ms AS latest_p50_ms,
       ls.p95_ms AS latest_p95_ms,
       ls.p99_ms AS latest_p99_ms,
       ls.slow_count, ls.timeout_count
FROM topic_counts tc
LEFT JOIN latest_stats ls ON tc.topic = ls.topic
ORDER BY tc.latest_run_at DESC
LIMIT :limit OFFSET :offset
```

This gives accurate run counts from the raw data and latest percentiles
from the aggregate. The CTE structure keeps each concern isolated.

#### Option B: Window function over hourly aggregate

```sql
ROW_NUMBER() OVER (PARTITION BY topic ORDER BY bucket DESC) = 1
```

Semantically identical to `DISTINCT ON` but more verbose. PostgreSQL
typically optimises `DISTINCT ON` more efficiently than a filtered window
function. No advantage.

#### Option C: Lateral join from distinct topics to latest bucket

More complex syntax for the same result. `DISTINCT ON` is the idiomatic
PostgreSQL approach.

### Decision: `DISTINCT ON` with CTE for run counts

---

### Decision 4: `list_topic_runs()` query strategy

This method returns recent runs that included handles on a given topic,
with per-run percentile summaries. It requires data from both `runs`
(metadata) and `handle_measurements` (per-handle latencies).

#### Approach: Aggregate then join

```sql
WITH run_stats AS (
    SELECT
        run_id,
        count(*) AS handle_count,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
        percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
        count(*) FILTER (WHERE outcome = 'slow') AS slow_count,
        count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND topic = :topic
    GROUP BY run_id
    ORDER BY run_id
)
SELECT
    rs.run_id, r.started_at, r.environment, r.branch,
    rs.handle_count, rs.p50_ms, rs.p95_ms, rs.p99_ms,
    rs.slow_count, rs.timeout_count
FROM run_stats rs
JOIN runs r ON r.id = rs.run_id
ORDER BY r.started_at DESC
LIMIT :limit OFFSET :offset
```

Aggregate first, then join. The CTE computes per-run stats from the
indexed `handle_measurements` table (`idx_handles_topic` covers
`tenant_id, topic, time DESC`). The join to `runs` fetches only the
metadata columns needed for the response. The limit is applied after
the join to respect the sort order.

This method accepts an optional `environment` parameter (added to the
CTE's WHERE clause) to support the filter bar visible in PRD-010
View 3's layout.

---

## Decision

**Chosen approach:**

1. **Raw SQL via `text()`** for all `TopicRepository` queries, with
   named bind parameters for all user-supplied values.
2. **15-minute fixed interval** for raw bucket queries.
3. **`DISTINCT ON` with CTE** for the topic list query.
4. **Aggregate-then-join** for the topic runs query.

---

## Consequences

### Positive

- **Queries are verifiable against the migration DDL.** The `SELECT`
  column lists in `query_aggregate()` mirror the continuous aggregate
  definitions in `001_initial_schema.py`. A reviewer can diff them.
- **No ORM model maintenance for materialized views.** The views are
  defined once in the migration. The repository queries them directly.
  Schema drift is caught by integration tests, not by keeping two
  definitions in sync.
- **`GET /topics/{topic}/latency` leverages the resolution service.**
  The route calls `ResolutionService.get_topic_latency()`, which
  handles source selection, concurrent queries, and partial-bucket
  union. The route handler is ~15 lines.
- **All queries are index-covered.** `idx_handles_topic` serves
  `query_raw()`, `list_topic_runs()`, and the topic counts CTE.
  `idx_handles_baseline` serves environment-filtered variants.

### Negative

- **Raw SQL has no compile-time column checking.** A typo in a column
  name produces a runtime error. Mitigated by: every query has an
  integration test against a real TimescaleDB.
- **View name interpolation in `query_aggregate()`.** The `source`
  parameter ("hourly" or "daily") maps to a view name that is
  interpolated into the SQL string. This is safe because `source` is
  validated against a safelist (see §Security Considerations), but it
  is a pattern that must not be extended to user-supplied values.

### Neutral

- The `ResolutionService` is unchanged — it already handles the
  orchestration logic. This ADR only fills in the SQL that the service
  calls.
- Three new endpoints are thin route handlers following the established
  pattern. No new abstractions.

### Security Considerations

- **SQL injection prevention.** All user-supplied values (`tenant_id`,
  `topic`, `environment`, `start`, `end`, `limit`, `offset`) are passed
  as named bind parameters via `text()`. No string interpolation.
- **View name safelist.** The `source` parameter in `query_aggregate()`
  is validated against a literal set before interpolation:
  ```python
  _AGGREGATE_VIEWS = {"hourly": "topic_latency_hourly", "daily": "topic_latency_daily"}

  def query_aggregate(self, source: str, ...):
      view = _AGGREGATE_VIEWS.get(source)
      if view is None:
          raise ValueError(f"Unknown aggregate source: {source!r}")
      stmt = text(f"SELECT ... FROM {view} WHERE ...")
  ```
  The view name comes from a hardcoded dict, never from user input.
  The `source` value itself originates from `ResolutionService`'s
  internal logic, not from the request. An additional safelist check
  in the repository is defence in depth.
- **Query parameter validation.** The route handler validates `tenant`,
  `topic`, `environment`, `limit`, `offset`, `from`, and `to` via
  Pydantic `Query()` annotations before they reach the repository.
  Constraints match PRD-009 §Input Validation.

---

## Implementation

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `api/topics.py` | Route handlers for `/topics`, `/topics/{topic}/latency`, `/topics/{topic}/runs` | ~80 |

### Modified Files

| File | Change |
|------|--------|
| `repositories/topic_repo.py` | Replace `NotImplementedError` stubs with SQL queries |
| `dependencies.py` | Add `get_topic_repo()` and `get_resolution_service()` providers |
| `app.py` | Register `topics.router` |

### Dependency Injection Wiring

```python
# dependencies.py — additions

from chronicle.repositories.topic_repo import TopicRepository
from chronicle.services.resolution_service import ResolutionService


async def get_topic_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TopicRepository:
    return TopicRepository(session)


async def get_resolution_service(
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
) -> ResolutionService:
    return ResolutionService(topic_repo)
```

The `ResolutionService` depends on `TopicRepository`. FastAPI's
`Depends()` resolves the chain: `get_session` → `get_topic_repo` →
`get_resolution_service`. Each is request-scoped.

### Route Handlers

```python
# api/topics.py

router = APIRouter(tags=["topics"])


@router.get("/topics", response_model=TopicListResponse)
async def list_topics(
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
    tenant: Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TopicListResponse:
    tenant_row = await run_repo.get_tenant_by_slug(tenant)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    topics, total = await topic_repo.list_topics(
        tenant_row.id, environment=environment, limit=limit, offset=offset,
    )
    return TopicListResponse(
        items=[TopicSummary(**t._asdict()) for t in topics],
        total=total, limit=limit, offset=offset,
    )


@router.get("/topics/{topic}/latency", response_model=TopicLatencyResponse)
async def get_topic_latency(
    topic: Annotated[str, Path(max_length=256)],
    resolution_svc: Annotated[ResolutionService, Depends(get_resolution_service)],
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    tenant: Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    resolution: Annotated[str | None, Query(pattern=r"^(raw|hourly|daily)$")] = None,
) -> TopicLatencyResponse:
    tenant_row = await run_repo.get_tenant_by_slug(tenant)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    end = to or datetime.now(timezone.utc)
    start = from_ or (end - timedelta(days=7))

    buckets, resolution_used = await resolution_svc.get_topic_latency(
        topic, tenant_row.id, environment, start, end, resolution,
    )
    return TopicLatencyResponse(
        topic=topic, tenant=tenant, environment=environment,
        resolution=resolution_used,
        buckets=[LatencyBucket(**b._asdict()) for b in buckets],
    )


@router.get("/topics/{topic}/runs", response_model=TopicRunListResponse)
async def list_topic_runs(
    topic: Annotated[str, Path(max_length=256)],
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    tenant: Annotated[str, Query(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")],
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TopicRunListResponse:
    tenant_row = await run_repo.get_tenant_by_slug(tenant)
    if tenant_row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    runs, total = await topic_repo.list_topic_runs(
        topic, tenant_row.id, limit=limit, offset=offset,
    )
    return TopicRunListResponse(
        items=[TopicRunSummary(**r._asdict()) for r in runs],
        total=total, limit=limit, offset=offset,
    )
```

Each handler is under 25 lines. Validation is in Pydantic annotations.
Business logic is in the resolution service. SQL is in the repository.

### TopicRepository SQL Implementation

#### `query_raw()`

```python
_RAW_BUCKET_INTERVAL = "15 minutes"

_RAW_QUERY = text("""
    SELECT
        time_bucket(:interval, time) AS bucket,
        count(*)                     AS sample_count,
        avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
        percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
        min(latency_ms)              AS min_ms,
        max(latency_ms)              AS max_ms,
        count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
        count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
        count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
        count(*) FILTER (WHERE over_budget)          AS budget_violation_count
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND topic = :topic
      AND time >= :start AND time < :end
      AND (:environment IS NULL OR environment = :environment)
    GROUP BY bucket
    ORDER BY bucket
""")
```

The `FILTER` clauses mirror the continuous aggregate definition exactly.
This ensures that raw-resolution data and aggregate-resolution data
produce the same metrics for the same underlying rows.

#### `query_aggregate()`

```python
_AGGREGATE_VIEWS: dict[str, str] = {
    "hourly": "topic_latency_hourly",
    "daily": "topic_latency_daily",
}

async def query_aggregate(self, source, topic, tenant_id, environment, start, end):
    view = _AGGREGATE_VIEWS.get(source)
    if view is None:
        raise ValueError(f"Unknown aggregate source: {source!r}")

    stmt = text(f"""
        SELECT bucket, sample_count, avg_ms,
               p50_ms, p95_ms, p99_ms, min_ms, max_ms,
               slow_count, timeout_count, fail_count,
               budget_violation_count
        FROM {view}
        WHERE tenant_id = :tenant_id
          AND topic = :topic
          AND bucket >= :start AND bucket < :end
          AND (:environment IS NULL OR environment = :environment)
        ORDER BY bucket
    """)
    result = await self.session.execute(stmt, {...})
    return [LatencyBucketRow(*row) for row in result.fetchall()]
```

The `{view}` interpolation is safe because `view` comes from the
`_AGGREGATE_VIEWS` dict, never from user input.

### Migration Path

Not applicable — no schema changes. The continuous aggregates and
indexes already exist. This ADR implements queries against them.

### Timeline

1. Implement `TopicRepository` SQL queries (4 methods).
2. Add `get_topic_repo` and `get_resolution_service` to `dependencies.py`.
3. Create `api/topics.py` with three route handlers.
4. Register `topics.router` in `app.py`.
5. Write integration tests (mocked repo) and e2e tests (real DB).

---

## Validation

### Success Metrics

- **`GET /api/v1/topics/{topic}/latency` responds in < 500ms p99 for a
  30-day range.** Measured by: e2e test timing against a real TimescaleDB
  with 500+ handle measurement rows across 30 days. The query hits
  `topic_latency_hourly` (pre-computed), so the response should be well
  under the target.
- **`GET /api/v1/topics` responds in < 200ms p99 for 100 topics.**
  Measured by: e2e test with 100 distinct topics ingested.
- **Raw and aggregate queries produce identical metrics for the same data.**
  Measured by: e2e test that ingests data, waits for the aggregate
  refresh, then compares `query_raw()` output against `query_aggregate()`
  for the same time range. Values should match within floating-point
  tolerance.
- **Partial-bucket union produces fresh data.** Measured by: e2e test
  that ingests a run within the last 30 minutes, queries with hourly
  resolution, and asserts the latest bucket contains the just-ingested
  data (from the raw union, not the stale aggregate).
- **Route handlers are under 25 lines each.** Measured by: line count.
- **No string interpolation of user input into SQL.** Measured by: CI
  grep check — `grep -rn "f\".*{.*}.*FROM\|f\".*{.*}.*WHERE"
  repositories/topic_repo.py` returns only the safelist-controlled
  `{view}` interpolation.

### Monitoring

- Query timing on all three topic endpoints, logged via SQLAlchemy
  `after_cursor_execute` hook. Alert if p99 exceeds 80% of the NFR
  target (400ms for latency, 160ms for topic list).
- Cache hit rate for topic list (30s TTL per ADR-0021 caching table).

---

## Related Decisions

- [ADR-0021](0021-chronicle-api-structure.md) — Repository + Service
  Layer pattern. This ADR follows the same structure and extends the
  caching table.
- [ADR-0024](0024-chronicle-run-summary-backend.md) — Run Summary
  backend. Same thin-route, denormalise-for-speed philosophy. Topic
  queries differ in that they use raw SQL instead of ORM expressions,
  because the data sources are materialized views.
- [PRD-009](../prd/PRD-009-chronicle-reporting-server.md) — API surface,
  NFR targets, continuous aggregate definitions.
- [PRD-010](../prd/PRD-010-chronicle-dashboard-views.md) — View 3 and
  View 6 specifications.

---

## References

- [TimescaleDB `time_bucket` documentation](https://docs.timescale.com/api/latest/hyperfunctions/time_bucket/) —
  the bucketing function used in all queries
- [PostgreSQL ordered-set aggregates](https://www.postgresql.org/docs/current/functions-aggregate.html#FUNCTIONS-ORDEREDSET-TABLE) —
  `percentile_cont ... WITHIN GROUP`
- [PostgreSQL `DISTINCT ON`](https://www.postgresql.org/docs/current/sql-select.html#SQL-DISTINCT) —
  used for the topic list query

---

## Notes

- **Open follow-up — `TopicSummaryRow.run_count` semantics.** The
  `run_count` field currently represents the total number of runs that
  included handles on this topic (computed from `handle_measurements`
  via `count(DISTINCT run_id)`). If this proves too expensive for
  tenants with thousands of runs, consider denormalising a
  `topic_run_count` into a summary table populated during ingest. For
  the target scale (tens of tenants, hundreds of runs), the CTE
  approach is sufficient. **Owner:** Chronicle maintainers.

- **Open follow-up — topic list staleness indicator.** The topic list
  shows `latest_p50_ms` etc. from the hourly aggregate, which may be
  up to 90 minutes stale. Consider adding a `data_age_seconds` field
  to `TopicSummary` so the frontend can show a "data from X minutes
  ago" indicator. Deferred to v1.1. **Owner:** Chronicle frontend.

- **Open follow-up — `environment` filter on `list_topic_runs()`.** The
  current implementation passes `environment` through to the CTE's
  WHERE clause. PRD-010 shows an environment filter in the Topic
  Drilldown header but does not explicitly say it applies to the Recent
  Runs table. The filter is included for consistency — if it proves
  unwanted, remove the parameter. **Owner:** Chronicle frontend.

**Last Updated:** 2026-04-19
