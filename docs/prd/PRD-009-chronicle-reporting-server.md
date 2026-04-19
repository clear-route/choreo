# Chronicle — Reporting Server for Longitudinal Test Performance Analytics — Product Requirements Document

**Status:** Draft (v1)
**Created:** 2026-04-19
**Last Updated:** 2026-04-19 (v2 — post-review)
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering, Consumer Teams

---

## Revision note (v2 vs. v1)

v2 incorporates a four-actor parallel review (architecture, code quality,
security, performance). Material changes from v1:

- **Continuous aggregates.** Fixed silent bug where `WHERE latency_ms IS NOT NULL`
  zeroed out all timeout/fail counts. Percentile functions now use inline
  `FILTER` clauses instead.
- **`over_budget` derivation.** Documented that `over_budget` is computed during
  ingest from `diagnosis.kind == "over_budget"`, not a source-schema field.
- **Ingest pipeline.** Specified single-transaction bulk insert via `COPY`
  protocol, request body size limit (20 MB), array cardinality caps, and
  idempotency support.
- **Hypertable partitioning.** Removed space partitioning by `tenant_id` (wrong
  for UUID distribution on single-node). Added missing composite index for
  anomaly baseline queries and explicit compression segment/order config.
- **Anomaly detection.** Added minimum sample count, stored `baseline_stddev`
  for resolution checks, and documented statistical assumptions.
- **SSE.** Specified in-memory broadcast, connection limit (100), 30s heartbeat,
  sequence-based event IDs, and single-worker constraint.
- **Security.** New section covering threat model, tenant slug validation, rate
  limiting, CORS/CSP policy, request size limits, and XSS prevention.
- **NFRs.** New section with response time targets, throughput targets, storage
  projections, and connection pool sizing.
- **Field mapping.** New table documenting which `test-report-v1` fields are
  extracted into relational columns vs. retained only in JSONB.
- **Schema version negotiation.** Stated acceptance policy for v1.x vs. v2.
- **User stories.** Added three user stories with acceptance criteria.
- **Open questions.** Three resolved (moved to decisions); two new ones added.

---

## Executive Summary

Choreo's `choreo-reporter` (PRD-007) produces a structured `results.json` at
the end of every pytest run. That file captures per-handle latencies, outcomes,
timelines, and matcher diagnostics — then it sits on disk and is never looked
at again.

Chronicle is a **reporting server** that ingests those JSON reports over time,
stores them in TimescaleDB, and exposes a React dashboard for longitudinal
performance analytics. It answers questions no single-run report can:

- "Is `orders.created` getting slower on staging?"
- "Did p95 latency for any topic regress after last Tuesday's deploy?"
- "Which topics have the most budget violations this month?"
- "Is our event bus still performing within expected bounds right now?"

Chronicle operates in two modes with identical data paths:

1. **CI-attached** — a pipeline POSTs `results.json` after each test run.
   Teams see trends across builds.
2. **Continuous / synthetic** — Choreo runs on a schedule against a standing
   environment and POSTs results every N minutes. Chronicle becomes a
   **synthetic monitoring dashboard for message-based infrastructure** — active
   probing of async systems, not passive observation of production traffic.

No existing tool occupies this space. Test reporters (Allure, ReportPortal)
think in pass/fail binaries. Performance platforms (k6, Locust, Gatling) target
request/response patterns. Synthetic monitors (Datadog Synthetics, Checkly)
probe HTTP endpoints. None understand message bus topics, matcher semantics,
latency budgets, or Choreo's outcome model (PASS / FAIL / TIMEOUT / SLOW).

---

## Problem Statement

### Current State

A consumer team runs Choreo tests in CI. Each run produces a `results.json`
(PRD-007) with rich structured data: per-handle latencies, outcomes, budgets,
matcher diagnostics, timelines. That data is consumed once — by one engineer
reading one HTML report — and discarded.

There is no way to answer "is this getting worse?" without manually comparing
report files. There is no alerting when a topic's latency drifts. There is no
visibility into whether a standing environment's message bus is healthy between
deploys.

### User Pain Points

- **No longitudinal view.** Each report is an island. A topic that was 12ms
  last week and 85ms today produces no signal unless someone notices manually.
- **No regression detection.** A deploy that degrades message delivery by 30%
  is invisible until a user complains or a timeout fires in production.
- **No continuous health signal.** Between deploys, teams have no active probe
  of their async infrastructure. They discover bus issues when production
  traffic fails, not before.
- **Multi-team blindness.** Several teams may test against the same environment.
  No shared view exists to correlate their experiences.

### Business Impact

- Performance regressions in message-based systems are caught late — often in
  production. A longitudinal view shifts detection left.
- Standing environments (staging, pre-prod) go unmonitored between deploys.
  Active probing fills the gap at far lower cost than full APM.
- Teams operating on the same infrastructure cannot share observations.
  Multi-tenant Chronicle gives them a common lens.

---

## Goals and Objectives

### Primary Goals

1. **Ingest `test-report-v1` JSON** via a REST endpoint and persist it in
   TimescaleDB with handle-level time-series metrics and full raw JSON for
   drill-down (see Field Mapping table in §Data Model for what is extracted
   vs. retained as JSONB).
2. **Provide a React dashboard** with four core views: run summary, topic
   drilldown, regression timeline, and anomaly feed.
3. **Support multi-tenancy** — multiple teams and environments report into the
   same instance with isolated views.
4. **Stream live results to the UI** via Server-Sent Events as runs complete.
5. **Retain data indefinitely** with automatic compression and pre-computed
   aggregates for fast queries over long time ranges.

### Success Metrics

- A consumer team adds a single `curl` to their CI pipeline and sees run
  history in the dashboard within seconds.
- A team running continuous Choreo probes every 5 minutes can view the last 30
  days of per-topic p95 latency with sub-second page load.
- A topic whose p95 latency increases by 40% compared to the previous 10 runs
  appears in the anomaly feed without any manual threshold configuration.

### Non-Goals

- **Production traffic monitoring.** Chronicle observes test/probe results, not
  live application traffic. It is not an APM.
- **Distributed tracing.** It does not collect or correlate spans. Teams
  should use Jaeger/Zipkin/OpenTelemetry for that.
- **General test reporting.** Pass/fail tracking, flake detection, and test
  management are served by Allure/ReportPortal. Chronicle focuses on the
  performance and anomaly dimension they lack.
- **Load testing.** It does not generate load. k6/Locust/Gatling do that.
  Chronicle could ingest their output later, but that is not in scope.
- **Alerting infrastructure.** v1 surfaces anomalies visually. Webhooks, Slack,
  PagerDuty, and CI-status integrations are deferred.
- **Authentication and authorisation.** v1 has no auth. Multi-tenancy is
  enforced by convention (tenant label on ingest), not access control.
- **Data export to Grafana/Prometheus.** A future version may expose a
  Prometheus scrape endpoint or Grafana data source. v1 has its own UI.

---

## Decision Drivers

* **Operational simplicity** — self-hosted open-source tool; one `docker compose up`
* **Data fidelity** — preserve the full `test-report-v1` JSON as JSONB; extract time-series metrics into relational columns for fast queries
* **Query speed over long ranges** — months of per-handle latency data, sub-second response
* **Familiar stack** — Python/FastAPI to match Choreo; React for the dashboard
* **No reinvention** — build on what Choreo already produces; don't rebuild general test reporting

---

## User Stories

**US-1. As a** CI pipeline operator,
**I want to** POST `results.json` to Chronicle and have my team's run history
appear in the dashboard,
**So that** we can track performance trends without building custom tooling.

**Acceptance Criteria:**

- [ ] A `curl -X POST` with a valid `test-report-v1` JSON and `X-Chronicle-Tenant` header returns 201 with a `run_id`.
- [ ] The run appears in the Run Summary view within 2 seconds of ingest.
- [ ] A duplicate POST with the same `Idempotency-Key` returns 200 with the existing `run_id` instead of creating a duplicate.
- [ ] An invalid JSON body returns 422 with a structured error listing schema violations.
- [ ] A body exceeding 20 MB is rejected with 413.

---

**US-2. As a** platform engineer investigating a latency regression,
**I want to** view the p50/p95/p99 latency trend for a specific topic over the
last 30 days,
**So that** I can determine when the regression started and correlate it with
deploys.

**Acceptance Criteria:**

- [ ] The Topic Drilldown view loads within 1 second for a 30-day range.
- [ ] p50/p95/p99 lines are rendered as a time-series chart with run markers.
- [ ] The most recent partial time bucket includes data from raw measurements (not stale aggregate data).
- [ ] Clicking a run marker navigates to the full run detail.

---

**US-3. As a** team lead reviewing the health of our staging environment,
**I want to** see a feed of detected anomalies without configuring thresholds,
**So that** I can spot regressions without manual monitoring.

**Acceptance Criteria:**

- [ ] The Anomaly Feed shows detected anomalies newest-first.
- [ ] Each anomaly card shows topic, metric, current vs. baseline, change %, severity, and a link to the triggering run.
- [ ] Anomalies are only generated when the baseline has at least 5 data points.
- [ ] Resolved anomalies are greyed out; a toggle hides/shows them.
- [ ] An SSE event fires to connected clients when a new anomaly is detected.

---

## Architecture

### Package Layout

```
packages/
  chronicle/
    pyproject.toml
    src/
      chronicle/
        __init__.py
        app.py              # FastAPI application factory
        config.py            # Settings via pydantic-settings
        models.py            # SQLAlchemy + TimescaleDB models
        ingest.py            # JSON parsing, validation, bulk insert
        detection.py         # Anomaly detection (decoupled from ingest)
        broadcast.py         # In-memory SSE fan-out channel
        api/
          runs.py            # /runs endpoints
          topics.py          # /topics endpoints
          anomalies.py       # /anomalies endpoints
          stream.py          # SSE endpoint
          health.py          # GET /api/v1/health
        resolution.py        # Auto-selects raw/hourly/daily source for queries
        migrations/          # Alembic
    frontend/
      package.json
      src/
        App.tsx
        views/
          RunSummary.tsx
          TopicDrilldown.tsx
          RegressionTimeline.tsx
          AnomalyFeed.tsx
    Dockerfile
    docker-compose.yml       # Chronicle + TimescaleDB
```

Lives in the monorepo under `packages/chronicle`. The `choreo` core library
has no knowledge of Chronicle. The only contract is the `test-report-v1` JSON
schema (PRD-007).

### Component Overview

```
┌──────────────┐      POST /api/v1/runs       ┌──────────────────┐
│  CI pipeline │ ──────────────────────────────▶│                  │
│  (curl)      │                               │  Chronicle API   │
└──────────────┘                               │  (FastAPI)       │
                                               │                  │
┌──────────────┐      POST /api/v1/runs       │                  │
│  Scheduled   │ ──────────────────────────────▶│                  │
│  Choreo run  │                               └────────┬─────────┘
└──────────────┘                                        │
                                                        │ write
                                                        ▼
                                               ┌──────────────────┐
                                               │   TimescaleDB    │
                                               │   (PostgreSQL)   │
                                               └────────┬─────────┘
                                                        │ read
                                                        ▼
┌──────────────┐      SSE /api/v1/stream      ┌──────────────────┐
│  React UI    │ ◀─────────────────────────────│  Chronicle API   │
│  (browser)   │ ──────────────────────────────▶│  (FastAPI)       │
└──────────────┘      GET /api/v1/*            └──────────────────┘
```

### Why SSE, Not WebSockets

The communication is unidirectional: the server pushes new-run notifications
and live result updates to the UI. The browser never sends commands back
through the streaming channel — it uses standard REST for queries.

SSE advantages for this use case:

- Simpler protocol; works through HTTP/1.1 proxies and load balancers without
  upgrade negotiation.
- Native browser reconnection with `Last-Event-ID`.
- No additional library on either side (FastAPI's `StreamingResponse` +
  browser `EventSource`).

WebSockets would only be justified if the UI needed to send real-time commands
to the server (e.g. controlling a running test), which is not a use case.

---

## Data Model

### Storage: TimescaleDB

TimescaleDB is a PostgreSQL extension. Users get one container
(`timescale/timescaledb-ha:pg18`) that is fully PostgreSQL-compatible.

Why TimescaleDB over alternatives:

| Concern | TimescaleDB | ClickHouse | InfluxDB | Plain PostgreSQL |
|---------|-------------|------------|----------|------------------|
| Python drivers | asyncpg, SQLAlchemy | thin client only | less mature | asyncpg, SQLAlchemy |
| Deployment | one container | separate service | separate service | one container |
| Continuous aggregates | native | different semantics | limited in OSS | manual |
| Compression | automatic, 10-20x | excellent | good | manual |
| Relational model | full PostgreSQL | no foreign keys | no relations | full PostgreSQL |
| Retention policies | built-in | manual | built-in | manual |

The run/scenario hierarchy maps to standard relational tables. The handle-level
measurements go into a hypertable partitioned by time only (no space
partitioning — see Decision 15). Best of both worlds.

### Field Mapping: `test-report-v1` to Relational Schema

The `raw_report` JSONB column on `runs` is the system of record and preserves
every field. The relational tables extract only the fields needed for
time-series queries, filtering, and aggregation. Fields not listed below are
accessible only via the raw JSON.

| Source field | Extracted to | Notes |
|-------------|-------------|-------|
| `run.started_at`, `finished_at`, `duration_ms` | `runs` columns | |
| `run.environment`, `transport`, `branch`, `git_sha` | `runs` columns | |
| `run.hostname`, `harness_version`, `reporter_version` | `runs` columns | |
| `run.python_version`, `project_name` | `runs` columns | |
| `run.totals.*` | `runs.total_*` columns | Denormalised for list queries |
| `run.allowlist_path` | JSONB only | Not analytically useful |
| `run.xdist` | JSONB only | Not analytically useful |
| `run.truncated` | JSONB only | Could flag unreliable data; extract if needed |
| `run.redactions` | JSONB only | Informational |
| `test.choreo_meta.tags` | JSONB only | Future: extract for tag-based filtering |
| `test.choreo_meta.timeout_ms` | JSONB only | |
| `scenario.replies` | JSONB only | PRD-008 reply reports; deferred |
| `scenario.timeline` | JSONB only | Per-scenario detail, not time-series |
| `scenario.summary_text` | JSONB only | Human-readable, not queryable |
| `handle.topic`, `outcome`, `latency_ms`, `budget_ms` | `handle_measurements` | Core time-series metrics |
| `handle.attempts`, `matcher_description` | `handle_measurements` | |
| `handle.diagnosis.kind` | `handle_measurements.diagnosis_kind` | Derived; enables richer anomaly detection |
| `handle.over_budget` (derived) | `handle_measurements.over_budget` | Computed: `diagnosis.kind == "over_budget"` |
| `handle.expected`, `actual` | JSONB only | Arbitrarily large; drill-down via raw report |
| `handle.reason`, `failure`, `failures` | JSONB only | Diagnostic detail, not time-series |
| `handle.failures_dropped`, `truncated` | JSONB only | |

### Schema

```sql
-- ─── Relational tables ───

CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT UNIQUE NOT NULL,       -- "team-alpha", "platform-eng"
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),

    -- slug validation: ^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$ enforced at API layer
    CONSTRAINT chk_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$')
);

CREATE TABLE runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT UNIQUE,            -- optional; prevents duplicate ingestion
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- from run metadata
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    duration_ms     DOUBLE PRECISION NOT NULL,
    environment     TEXT,                   -- "dev", "staging", "prod-like"
    transport       TEXT NOT NULL,          -- "MockTransport", "NatsTransport"
    branch          TEXT,
    git_sha         TEXT,
    hostname        TEXT,
    harness_version TEXT,
    reporter_version TEXT,
    python_version  TEXT,
    project_name    TEXT,

    -- totals (denormalised for list queries)
    total_tests     INTEGER NOT NULL,
    total_passed    INTEGER NOT NULL,
    total_failed    INTEGER NOT NULL,
    total_errored   INTEGER NOT NULL,
    total_skipped   INTEGER NOT NULL,
    total_slow      INTEGER NOT NULL,

    -- raw JSON kept for full replay / re-processing
    raw_report      JSONB NOT NULL
);

CREATE INDEX idx_runs_tenant_time ON runs (tenant_id, started_at DESC);
CREATE INDEX idx_runs_environment ON runs (tenant_id, environment, started_at DESC);
CREATE INDEX idx_runs_branch ON runs (tenant_id, branch, started_at DESC);
CREATE UNIQUE INDEX idx_runs_idempotency ON runs (idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE scenarios (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_nodeid     TEXT NOT NULL,
    name            TEXT NOT NULL,
    correlation_id  TEXT,
    outcome         TEXT NOT NULL,          -- "pass", "fail", "timeout", "slow"
    duration_ms     DOUBLE PRECISION NOT NULL,
    completed_normally BOOLEAN NOT NULL
);

CREATE INDEX idx_scenarios_run ON scenarios (run_id);
CREATE INDEX idx_scenarios_name ON scenarios (name, run_id);

-- ─── Hypertable for time-series handle measurements ───

CREATE TABLE handle_measurements (
    time            TIMESTAMPTZ NOT NULL,   -- run.started_at (partition key)
    tenant_id       UUID NOT NULL,
    run_id          UUID NOT NULL,
    scenario_id     UUID NOT NULL,
    environment     TEXT,
    transport       TEXT NOT NULL,
    branch          TEXT,

    -- handle fields
    topic           TEXT NOT NULL,
    outcome         TEXT NOT NULL,          -- "pass", "fail", "timeout", "slow", "pending"
    latency_ms      DOUBLE PRECISION,      -- null for timeout/pending
    budget_ms       DOUBLE PRECISION,      -- null if no budget set
    attempts        INTEGER NOT NULL,
    matcher_description TEXT NOT NULL,
    diagnosis_kind  TEXT,                   -- "matched", "over_budget", "near_miss",
                                           -- "silent_timeout", "pending"; from diagnosis.kind
    over_budget     BOOLEAN NOT NULL DEFAULT false
                                           -- derived at ingest: diagnosis.kind == 'over_budget'
);

-- Time-only partitioning. No space partitioning by tenant_id — UUID hash
-- distribution adds chunk proliferation without improving query locality
-- on a single-node deployment. Tenant filtering uses indexes instead.
SELECT create_hypertable('handle_measurements', 'time');

CREATE INDEX idx_handles_topic ON handle_measurements (tenant_id, topic, time DESC);
CREATE INDEX idx_handles_outcome ON handle_measurements (tenant_id, outcome, time DESC);
CREATE INDEX idx_handles_baseline ON handle_measurements (tenant_id, environment, topic, time DESC);

-- All handles within a run share the run's started_at as their time
-- dimension. Per-handle resolution timestamps are available in the raw
-- JSON but are not extracted, because the unit of observation is the
-- run, not the individual handle.

-- ─── Continuous aggregates ───

-- Hourly per-topic latency stats.
-- Note: no top-level WHERE clause. Percentile functions use inline FILTER
-- to exclude null latencies. Outcome counts include all rows (including
-- timeout handles that have latency_ms = null).
CREATE MATERIALIZED VIEW topic_latency_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time)     AS bucket,
    tenant_id,
    environment,
    transport,
    topic,
    count(*)                        AS sample_count,
    avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
    min(latency_ms)                 AS min_ms,
    max(latency_ms)                 AS max_ms,
    count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
    count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
    count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
    count(*) FILTER (WHERE over_budget)         AS budget_violation_count
FROM handle_measurements
GROUP BY bucket, tenant_id, environment, transport, topic;

SELECT add_continuous_aggregate_policy('topic_latency_hourly',
    start_offset    => INTERVAL '3 hours',
    end_offset      => INTERVAL '30 minutes',
    schedule_interval => INTERVAL '30 minutes'
);

-- Daily rollup (queries over months)
CREATE MATERIALIZED VIEW topic_latency_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time)      AS bucket,
    tenant_id,
    environment,
    transport,
    topic,
    count(*)                        AS sample_count,
    avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
    min(latency_ms)                 AS min_ms,
    max(latency_ms)                 AS max_ms,
    count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
    count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
    count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
    count(*) FILTER (WHERE over_budget)         AS budget_violation_count
FROM handle_measurements
GROUP BY bucket, tenant_id, environment, transport, topic;

SELECT add_continuous_aggregate_policy('topic_latency_daily',
    start_offset    => INTERVAL '3 days',
    end_offset      => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour'
);

-- ─── Compression ───

ALTER TABLE handle_measurements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tenant_id, topic',
    timescaledb.compress_orderby = 'time DESC'
);

-- Compress raw measurements older than 7 days
SELECT add_compression_policy('handle_measurements', INTERVAL '7 days');

-- Raw data retained forever (compressed). Aggregates retained forever.
-- For deployments where storage is a concern, enable retention:
-- SELECT add_retention_policy('handle_measurements', INTERVAL '90 days');
-- The raw_report JSONB column on runs also grows unboundedly. For 10
-- continuous-testing tenants at 5-min intervals, expect ~100 GB/year.
-- A retention policy on the runs table may also be warranted:
-- DELETE FROM runs WHERE ingested_at < now() - INTERVAL '90 days';
```

### Schema Evolution Strategy

Chronicle validates ingested reports against the `test-report-v1` schema.
The version acceptance policy:

- **`schema_version: "1"` or `"1.x"`** — accepted. Unknown fields within a
  v1.x report are ignored by the relational extraction but preserved in the
  `raw_report` JSONB column.
- **`schema_version: "2"` or higher** — rejected with 422 until Chronicle is
  updated to support the new schema.

When the relational schema evolves (new columns on `handle_measurements`):

- Additive changes (new nullable columns) are applied via Alembic migrations.
  TimescaleDB compressed chunks must be decompressed before ALTER, then
  recompressed. The migration handles this automatically.
- Destructive changes (column renames, type changes) require a new hypertable
  and data migration. This is operationally heavy and should be avoided.

### Data Lifecycle

```
Raw handle_measurements
  │
  ├── < 7 days ──── uncompressed, full granularity
  │
  ├── 7+ days ───── compressed (10-20x via segmentby tenant/topic), still queryable
  │
  ├── hourly ─────── topic_latency_hourly (continuous aggregate, auto-refreshed)
  │
  └── daily ──────── topic_latency_daily (continuous aggregate, auto-refreshed)
```

The API automatically selects the appropriate source:

- Last 24 hours → raw `handle_measurements`
- 1-30 days → `topic_latency_hourly`, with raw data unioned for the most
  recent partial bucket to avoid stale-data gaps
- 30+ days → `topic_latency_daily`, same partial-bucket union

Reports with `started_at` more than 24 hours in the past are accepted but
logged with a warning, since they may miss the continuous aggregate refresh
window. The ingest response includes a `warning` field in this case.

---

## API Surface

Strict RESTful. All endpoints under `/api/v1/`. JSON request/response bodies.
All database queries use parameterised statements. No string interpolation or
concatenation of user-supplied values into SQL.

### Input Validation

All filter parameters are validated before use:

| Parameter | Constraint |
|-----------|-----------|
| `tenant` | `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$` (normalised to lowercase on ingest) |
| `environment` | `^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$` |
| `branch` | max 256 characters |
| `topic` | max 256 characters |
| `limit` | 1-500 (default 50) |
| `offset` | non-negative integer |
| `from`, `to` | valid ISO 8601 timestamps |

### Request Size Limits

- Maximum request body: **20 MB** (enforced at the HTTP layer).
- Maximum `tests` array: 10,000 items.
- Maximum `scenarios` per test: 100 items.
- Maximum `handles` per scenario: 100 items.
- Maximum `timeline` entries per scenario: 256 (matching PRD-006 ring buffer).

Reports exceeding these limits are rejected with 413.

### Rate Limiting

- Per-tenant ingest: 60 requests/minute.
- Global ingest: 300 requests/minute.
- Query endpoints: 600 requests/minute per client IP.

Rate limits are configurable via `CHRONICLE_RATE_LIMIT_*` environment variables.

### Error Response Format

All errors use a consistent JSON structure. Stack traces and internal paths
are never included in responses.

```json
{
  "error": "validation_error",
  "detail": "schema_version '2' is not supported; expected '1' or '1.x'"
}
```

### Response Envelope (List Endpoints)

All list endpoints return a paginated envelope:

```json
{
  "items": [...],
  "total": 1234,
  "limit": 50,
  "offset": 0
}
```

### Health

```
GET /api/v1/health

Response: 200 OK
{"status": "ok", "database": "connected"}

Response: 503 Service Unavailable
{"status": "degraded", "database": "unreachable"}
```

### Ingest

```
POST /api/v1/runs
Content-Type: application/json
X-Chronicle-Tenant: team-alpha
Idempotency-Key: optional-uuid          (optional)

Body: test-report-v1 JSON (as produced by choreo-reporter)

Response: 201 Created
{
  "run_id": "uuid",
  "handles_ingested": 42,
  "scenarios_ingested": 12
}

Response: 200 OK (duplicate Idempotency-Key)
{
  "run_id": "existing-uuid",
  "duplicate": true
}
```

The ingest pipeline:

1. Validates request body size (reject > 20 MB with 413).
2. Validates the JSON against `test-report-v1` schema. Accepts
   `schema_version: "1"` or `"1.x"`; rejects `"2"` with 422.
3. Validates `X-Chronicle-Tenant` slug format. Normalises to lowercase.
4. Checks `Idempotency-Key` if provided; returns existing run if duplicate.
5. **In a single database transaction:**
   a. Auto-creates tenant if the slug does not exist.
   b. Creates a `run` row with extracted metadata + raw JSON.
   c. Bulk-inserts scenarios via multi-row `INSERT`.
   d. Bulk-inserts handle measurements via PostgreSQL `COPY` protocol
      (asyncpg `copy_records_to_table`) for throughput. The `over_budget`
      column is derived from `diagnosis.kind == "over_budget"` during
      normalisation. The `diagnosis_kind` column is extracted from
      `diagnosis.kind`.
6. Runs anomaly detection (see §Anomaly Detection). **Anomaly detection
   failure does not fail the ingest request.** The run is persisted and
   the SSE event is emitted regardless.
7. Emits `run.completed` and (if applicable) `anomaly.detected` SSE events
   to connected clients via the broadcast channel.

### Query Endpoints

```
GET /api/v1/tenants
  List all tenants.

GET /api/v1/runs?tenant={slug}&environment={env}&branch={branch}&limit=50&offset=0
  List runs, newest first. Filterable by environment, branch, date range.

GET /api/v1/runs/{run_id}
  Full run detail including scenarios and handles.

GET /api/v1/runs/{run_id}/raw
  Raw test-report-v1 JSON as originally ingested (verbatim JSONB).

GET /api/v1/topics?tenant={slug}&environment={env}
  List all topics seen, with latest latency stats.

GET /api/v1/topics/{topic}/latency?tenant={slug}&environment={env}&from={iso}&to={iso}&resolution={hourly|daily|raw}
  Time-series latency data for a specific topic. Returns p50/p95/p99,
  sample count, slow/timeout/fail counts per bucket.
  Resolution auto-selected if omitted. For aggregate resolutions, the
  most recent partial bucket is filled from raw data to avoid stale gaps.

GET /api/v1/topics/{topic}/runs?tenant={slug}&limit=20
  Recent runs that included this topic, with per-run latency summary.

GET /api/v1/anomalies?tenant={slug}&limit=50
  Recent detected anomalies across all topics.

GET /api/v1/anomalies/topics/{topic}?tenant={slug}
  Anomaly history for a specific topic.
```

### Server-Sent Events

```
GET /api/v1/stream?tenant={slug}
Accept: text/event-stream

Events:
  id: 42                                   (monotonic sequence from PostgreSQL)
  event: run.completed
  data: {"run_id": "uuid", "started_at": "...", "totals": {...}, "environment": "..."}

  id: 43
  event: anomaly.detected
  data: {"topic": "...", "metric": "p95_ms", "current": 84.2, "baseline": 52.1, "change_pct": 61.7}
```

**Implementation details:**

- v1 runs as a **single uvicorn worker**. SSE fan-out uses an in-memory
  `asyncio.Queue` per connected client (via `broadcast.py`).
- Maximum concurrent SSE connections: 100 (configurable via
  `CHRONICLE_MAX_SSE_CONNECTIONS`). Beyond this, new connections receive 503.
- A `:heartbeat` comment is sent every 30 seconds to keep connections alive
  through proxies and load balancers.
- `Last-Event-ID` is supported for reconnection. Event IDs are monotonically
  increasing integers from a PostgreSQL sequence. On reconnection, the server
  replays events from a bounded in-memory ring buffer (last 1,000 events).
  Events outside the buffer are not replayed; the client should refresh.
- Slow clients whose per-client queue exceeds 100 pending events are
  disconnected.
- Multi-worker deployments require a PostgreSQL `LISTEN`/`NOTIFY` bridge or
  Redis Pub/Sub for cross-worker fan-out. This is not supported in v1.

The UI subscribes on page load and debounces re-renders (batching updates
every 2 seconds) to handle burst ingest gracefully.

---

## Anomaly Detection

v1 provides **visual regression detection**, not automated alerting. The server
computes anomaly signals on ingest; the UI displays them. No notifications
leave the system.

Anomaly detection runs as a separate step after the ingest transaction commits
(see §API Surface, ingest pipeline step 6). Detection adds O(topics_per_run)
queries per ingest. For the target scale (tens of tenants, probes every 5
minutes), this is acceptable. If ingest throughput becomes a bottleneck,
detection can be moved to an async background task.

### Detection Methods

**1. Rolling baseline comparison**

For each topic in the ingested run, query the last N runs (default 10) on the
same tenant + environment + topic. Compare the current run's per-topic p95
latency against the baseline mean + 2 standard deviations.

Flagged when: `current_p95 > baseline_mean + 2 * baseline_stddev`

This catches: sudden regressions after a deploy or config change.

**Statistical assumptions and limitations (v1):**

- The baseline uses the arithmetic mean and standard deviation of the raw
  (untransformed) p95 values. This assumes approximately normal distribution.
  Latency distributions are typically right-skewed (log-normal), so raw 2σ
  detection produces asymmetric behaviour. A future version may use
  log-transformation or median/MAD (median absolute deviation) for more
  appropriate outlier detection on skewed data.
- **Minimum sample count:** No anomaly detection is performed when the
  baseline has fewer than 5 data points.
- With N=10, a single outlier can shift the mean by 10% and inflate σ
  significantly. This is a known limitation; the minimum sample count and
  the 2σ/3σ threshold provide reasonable (not perfect) sensitivity.

**2. Budget violation rate**

Track the percentage of handles with `over_budget = true` per topic per run.
Flag when the violation rate exceeds 20% (configurable) or increases by more
than 10 percentage points compared to the baseline.

This catches: gradual degradation where latency stays under timeout but
exceeds declared budgets.

**3. Outcome shift**

Track the ratio of non-PASS outcomes per topic. Flag when timeout or fail
rate increases by more than 5 percentage points compared to baseline. The
`diagnosis_kind` column enables distinguishing `near_miss` (messages arrived
but did not match) from `silent_timeout` (no messages at all), providing
richer diagnostics in the anomaly card.

This catches: reliability regressions (messages not arriving, matchers
breaking).

### Anomaly Storage

```sql
CREATE TABLE anomalies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    run_id          UUID NOT NULL REFERENCES runs(id),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic           TEXT NOT NULL,
    detection_method TEXT NOT NULL,        -- "rolling_baseline", "budget_violation", "outcome_shift"
    metric          TEXT NOT NULL,         -- "p95_ms", "budget_violation_pct", "timeout_rate"
    current_value   DOUBLE PRECISION NOT NULL,
    baseline_value  DOUBLE PRECISION NOT NULL,
    baseline_stddev DOUBLE PRECISION NOT NULL,  -- stored for resolution checks
    change_pct      DOUBLE PRECISION NOT NULL,
    severity        TEXT NOT NULL,         -- "warning", "critical"
    resolved        BOOLEAN NOT NULL DEFAULT false,
    resolved_at     TIMESTAMPTZ
);

CREATE INDEX idx_anomalies_tenant_time ON anomalies (tenant_id, detected_at DESC);
CREATE INDEX idx_anomalies_topic ON anomalies (tenant_id, topic, detected_at DESC);
```

Severity thresholds (v1 defaults):

- **Warning**: current > baseline_mean + 2σ
- **Critical**: current > baseline_mean + 3σ

### Anomaly Resolution

An anomaly is auto-resolved when the next 3 consecutive runs for the same
tenant + environment + topic return to within 1σ of **the baseline that
existed at the time the anomaly was detected** (stored as `baseline_value`
and `baseline_stddev` on the anomaly row). This prevents the resolution
check from drifting as the rolling baseline absorbs the anomalous values.

The intentional asymmetry between detection (2σ) and resolution (1σ) creates
hysteresis: a topic must genuinely return to normal, not merely stop getting
worse.

---

## UI Views

React + Recharts (or Tremor). Four core views accessible from a top nav.

### 1. Run Summary

The landing page. Shows recent runs for the selected tenant and environment.

**Elements:**

- Tenant/environment selector (persisted in URL params)
- Run list table: timestamp, branch, duration, pass/fail/slow/timeout counts,
  handle count, anomaly badge
- Click a run to see full detail: test list, scenario breakdown, per-handle
  outcomes matching the structure of the HTML report (PRD-007) but within the
  Chronicle UI
- Sparkline column showing last 20 runs' pass rate trend

### 2. Topic Drilldown

Per-topic performance over time.

**Elements:**

- Topic selector (searchable dropdown of all topics seen)
- Time range picker (last 24h, 7d, 30d, 90d, custom)
- Latency chart: p50/p95/p99 lines over time, with run markers on the x-axis
- Outcome distribution stacked bar chart per time bucket
- Budget violation rate line (if any handles on this topic use `within_ms`)
- Table of recent handles on this topic with outcome, latency, budget, matcher

### 3. Regression Timeline

Cross-topic regression view.

**Elements:**

- Time range picker (maximum 90 days for the heatmap view)
- Table of topics sorted by "largest p95 increase vs. baseline", limited to
  the **top 100 topics** by regression magnitude (server-side limit to avoid
  unbounded payloads). A "show all" option is available with a performance
  warning for tenants with > 100 topics.
- Per-topic row: current p95, baseline p95, delta, sample count, trend arrow
- Click a topic row to navigate to its Topic Drilldown
- Heatmap: topics on y-axis, time buckets on x-axis, colour by latency
  percentile (green → yellow → red). Rendered on HTML Canvas (not SVG) to
  handle large topic counts without DOM element overhead.

### 4. Anomaly Feed

Chronological feed of detected anomalies.

**Elements:**

- Filterable by severity (warning/critical), detection method, topic, resolved status
- Each anomaly card shows: topic, metric, current vs. baseline, change %,
  severity badge, detection method, run link, timestamp
- Resolved anomalies shown greyed out; toggle to hide/show resolved
- Click through to the specific run and topic drilldown

---

## Deployment

### Container

Single `Dockerfile` producing a container that serves both the API and the
built React frontend (FastAPI static file mount).

```dockerfile
# Build stage: React frontend
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY packages/chronicle/frontend/package.json packages/chronicle/frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY packages/chronicle/frontend/ .
RUN npm run build

# Runtime stage: FastAPI + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY packages/chronicle/ .
COPY --from=frontend /app/frontend/dist ./static
RUN pip install .
CMD ["uvicorn", "chronicle.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

The frontend build requires a committed `package-lock.json` for reproducible
installs. `--ignore-scripts` prevents post-install script execution from
compromised dependencies.

### Docker Compose

```yaml
# WARNING: Default credentials below are for LOCAL DEVELOPMENT ONLY.
# Production deployments MUST use strong, unique database credentials
# via secrets management (Docker secrets, Vault, etc.).
services:
  chronicle:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://chronicle:chronicle@db:5432/chronicle
    depends_on:
      db:
        condition: service_healthy

  db:
    # Pin to a specific version for reproducible deployments.
    image: timescale/timescaledb-ha:pg18
    environment:
      POSTGRES_USER: chronicle
      POSTGRES_PASSWORD: chronicle
      POSTGRES_DB: chronicle
    volumes:
      - chronicle_data:/var/lib/postgresql/data
    # Database port not exposed to host — accessible only via Docker network.
    # Uncomment for local debugging: ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U chronicle"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  chronicle_data:
```

### Configuration

Via environment variables (pydantic-settings):

```
# ─── Core ───
DATABASE_URL              — required; asyncpg connection string
CHRONICLE_HOST            — default 0.0.0.0
CHRONICLE_PORT            — default 8000
CHRONICLE_LOG_LEVEL       — default INFO
CHRONICLE_LOG_FORMAT      — "json" (production) or "text" (default, development)

# ─── Connection pool ───
CHRONICLE_DB_POOL_MIN     — default 5
CHRONICLE_DB_POOL_MAX     — default 20
CHRONICLE_DB_POOL_TIMEOUT — default 30 (seconds)

# ─── Request limits ───
CHRONICLE_MAX_BODY_SIZE   — default 20971520 (20 MB)

# ─── Rate limiting ───
CHRONICLE_RATE_LIMIT_INGEST_PER_TENANT  — default 60 (per minute)
CHRONICLE_RATE_LIMIT_INGEST_GLOBAL      — default 300 (per minute)
CHRONICLE_RATE_LIMIT_QUERY              — default 600 (per minute per IP)

# ─── SSE ───
CHRONICLE_MAX_SSE_CONNECTIONS — default 100

# ─── Tenant management ───
CHRONICLE_AUTO_CREATE_TENANTS — default true (set false in production)

# ─── Anomaly detection ───
CHRONICLE_BASELINE_WINDOW      — default 10 (runs)
CHRONICLE_BASELINE_MIN_SAMPLES — default 5 (minimum for detection)
CHRONICLE_BASELINE_SIGMA       — default 2.0
CHRONICLE_BUDGET_VIOLATION_PCT — default 20.0
CHRONICLE_OUTCOME_SHIFT_PCT    — default 5.0

# ─── Retention (optional) ───
CHRONICLE_RAW_RETENTION_DAYS   — default null (forever); set to e.g. 90 to enable
```

---

## Integration with Existing Tooling

### What Chronicle replaces

Nothing. It fills a gap.

### What Chronicle complements

| Tool | Relationship |
|------|-------------|
| **Allure / ReportPortal** | They handle general pass/fail test reporting. Chronicle handles the performance/latency dimension they lack. A team can use both. |
| **Grafana** | Future versions may expose a Prometheus endpoint or Grafana data source. v1 has its own UI to keep deployment simple. |
| **k6 / Locust / Gatling** | They generate load against request/response systems. Chronicle observes Choreo's message-bus test results. Different input, different domain. |
| **Datadog Synthetics / Checkly** | They probe HTTP endpoints. Chronicle probes async message infrastructure. Complementary. |
| **choreo-reporter (PRD-007)** | Chronicle's sole data source. The reporter produces the JSON; Chronicle stores and analyses it. |

### What Chronicle explicitly will not build

- A general test report viewer (Allure does this)
- A dashboarding framework (Grafana does this)
- A load testing engine (k6/Locust do this)
- A distributed tracing system (Jaeger/Zipkin do this)
- Flaky test detection on pass/fail (ReportPortal/Buildkite do this)

---

## Security Considerations

### Threat Model

Chronicle is designed as an **internal tool deployed on a private network**.
The v1 threat model assumes:

- All clients on the network are trusted (no public internet exposure).
- The primary realistic threats are accidental misuse, not sophisticated attack.
- A misconfigured CI job or curious engineer is the likely adversary, not a
  malicious external actor.

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Storage exhaustion via oversized reports | Medium | High | 20 MB body limit, array cardinality caps |
| Tenant impersonation / cross-tenant reads | Medium (insider) | Medium | No auth in v1; deploy behind VPN |
| Baseline poisoning via fabricated reports | Medium (insider) | Medium | Idempotency keys; audit logging |
| SSE connection exhaustion | Low-Medium | High | 100 connection limit, 30s heartbeat |
| Ingest flooding | Low-Medium | Medium | Rate limiting (60/min per tenant) |
| SQL injection | Low (stack mitigates) | Critical | Parameterised queries only |
| Stored XSS in dashboard | Low (React mitigates) | Medium | No `dangerouslySetInnerHTML` |

### Security Requirements

1. **No `dangerouslySetInnerHTML`.** The React frontend must not use
   `dangerouslySetInnerHTML`, `eval`, or equivalent mechanisms to render
   report data. All user-supplied strings (topic names, matcher descriptions,
   stdout/stderr, timeline details) are rendered as text content, not HTML.

2. **Parameterised queries only.** All database queries must use parameterised
   statements via SQLAlchemy/asyncpg. No string interpolation of user-supplied
   values into SQL.

3. **Security headers.** The FastAPI application sets:
   - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'`
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`

4. **CORS policy.** No CORS headers in v1. The React frontend is served from
   the same origin. If cross-origin access is needed, CORS must be explicitly
   configured with an allowlist of permitted origins. A wildcard (`*`) CORS
   policy is not acceptable.

5. **Debug mode.** `debug=False` in production FastAPI configuration. Error
   responses never include stack traces or internal file paths.

6. **Tenant slug validation.** Slugs are normalised to lowercase and validated
   against `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$` before any database operation.

### Data Sensitivity

Test reports may contain:

- **Hostnames** — reveals internal infrastructure naming.
- **Git SHAs and branch names** — reveals development activity.
- **File paths** (test nodeids) — reveals internal directory structures.
- **Stdout/stderr/log** — may contain secrets or PII that leaked into test output.
- **Matcher expected/actual values** — may contain business data from fixtures.

The upstream `choreo-reporter` performs credential-shape redaction (PRD-007
§12) before the report reaches Chronicle. Chronicle performs **no additional
redaction** in v1. Operators should verify that reporter redaction
configuration is appropriate for their data sensitivity requirements.

### Known Limitations (v1)

- No authentication — deployment must be network-isolated.
- Cross-tenant read access is unrestricted — any client can query any tenant.
- No data redaction at the Chronicle layer.
- Indefinite retention without configurable purge (unless `CHRONICLE_RAW_RETENTION_DAYS` is set).

When authentication is introduced in a future version, tenant scoping will
move from query parameter to auth-token-derived routing, and PostgreSQL
Row-Level Security will be enabled on all tenant-scoped tables to prevent
data leaks from missing WHERE clauses.

---

## Non-Functional Requirements

### Response Time Targets

| Endpoint | Target (p99) |
|----------|-------------|
| `POST /api/v1/runs` (10,000 handles) | < 1 second |
| `POST /api/v1/runs` (100 handles) | < 200 ms |
| `GET /api/v1/runs` (list) | < 200 ms |
| `GET /api/v1/runs/{id}` (detail) | < 300 ms |
| `GET /api/v1/topics/{topic}/latency` (30-day range) | < 500 ms |
| `GET /api/v1/anomalies` (list) | < 200 ms |
| `GET /api/v1/health` | < 50 ms |
| SSE event delivery (ingest to client) | < 2 seconds |
| Dashboard initial page load (including bundle) | < 3 seconds |

### Throughput Targets

- Sustained ingest rate: at least 10 reports/second (burst from CI pipelines).
- Concurrent SSE connections: minimum 100.
- Concurrent API queries: minimum 50.

### Storage Projections

| Scenario | Daily volume | Annual volume |
|----------|-------------|--------------|
| 1 tenant, CI-only (20 runs/day) | ~2 MB raw JSON, ~12k handle rows | ~730 MB |
| 1 tenant, continuous (5-min, ~288 runs/day) | ~28 MB raw JSON, ~17k handle rows | ~10 GB |
| 10 tenants, continuous | ~280 MB raw JSON, ~170k handle rows | ~100 GB |

Compression (10-20x on `handle_measurements`) reduces the time-series storage
significantly. The `raw_report` JSONB on the `runs` table is the primary
storage consumer and benefits only from PostgreSQL TOAST compression (~2-3x).

### Resource Limits

- **Memory:** FastAPI process RSS under load: < 512 MB for the target scale.
- **CPU:** Single core sufficient for the target throughput. Async I/O handles
  concurrency without multi-worker.
- **Disk:** Alert when available disk drops below 20% of projected 30-day
  growth.

### Connection Pool Sizing

The asyncpg pool must handle concurrent ingests, API queries, and anomaly
detection without starvation. SSE connections do NOT hold database connections
— they query on demand and release immediately.

Peak concurrent database operations = max concurrent ingests + active API
queries + anomaly detection tasks. Default pool of 20 connections handles
10 concurrent ingests + 5 dashboard users + 5 anomaly computations.

---

## Decisions Already Made

| # | Decision | Why |
|---|----------|-----|
| 1 | **TimescaleDB** for storage | PostgreSQL compatibility (one mental model, SQLAlchemy, Alembic) + native continuous aggregates, compression, retention. One container for self-hosters. |
| 2 | **FastAPI** for the API | Matches Choreo's Python stack; async-native; strong typing with Pydantic. |
| 3 | **React + Recharts** for the UI | Best ecosystem for interactive time-series charts; standard frontend stack. Canvas-based rendering for heatmaps. |
| 4 | **Strict REST API under `/api/v1/`** | All state changes via POST/PUT/DELETE; all queries via GET. No GraphQL complexity for v1. Versioned prefix allows future breaking changes. |
| 5 | **SSE for live updates, not WebSockets** | Unidirectional push; simpler protocol; works through proxies; native browser reconnection. Single worker in v1; multi-worker needs LISTEN/NOTIFY bridge. |
| 6 | **Ingest = POST of `test-report-v1` JSON** | Zero new serialisation. Consumers already produce this via `choreo-reporter`. |
| 7 | **Multi-tenant by convention** | `X-Chronicle-Tenant` header on ingest; no auth in v1. Slug normalised to lowercase, validated against `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$`. |
| 8 | **Monorepo under `packages/chronicle`** | Shares CI, versioning, and release infrastructure. No cross-package imports; only the JSON schema is the contract. |
| 9 | **No auth in v1** | Reduces scope. Teams deploy internally behind a VPN or firewall. See §Security Considerations for threat model. |
| 10 | **Anomalies are visual only** | No webhooks, Slack, or CI-status integration in v1. Dashboard-only. |
| 11 | **Auto-create tenants on first ingest** | Removes setup friction. A team's first `curl` creates everything needed. Configurable via `CHRONICLE_AUTO_CREATE_TENANTS` (default true). |
| 12 | **Raw report stored as JSONB** | Enables re-processing if the schema evolves. The exploded relational data serves queries; the raw JSON is the system of record. |
| 13 | **API auto-selects query resolution** | The caller does not need to know about hourly vs. daily aggregates. The API picks the right source based on the requested time range. Recent partial bucket filled from raw data. |
| 14 | **Forever retention by default** | Compression makes this practical. Optional retention policy documented and configurable via `CHRONICLE_RAW_RETENTION_DAYS`. |
| 15 | **Time-only hypertable partitioning** | Space partitioning by `tenant_id` (UUID) adds chunk proliferation without improving query locality on a single-node deployment. Tenant filtering uses composite indexes instead. |
| 16 | **Bulk insert via COPY protocol** | Single-row INSERTs for 10k handles are unacceptably slow. asyncpg `copy_records_to_table` provides 10-50x throughput improvement. All inserts within a single transaction. |
| 17 | **`over_budget` derived from `diagnosis.kind`** | The `test-report-v1` schema has no `over_budget` boolean. Chronicle computes it during ingest normalisation from `diagnosis.kind == "over_budget"`. |
| 18 | **Schema version acceptance: `"1"` and `"1.x"`** | Minor additions (new optional fields) are backward-compatible and accepted. Unknown major versions are rejected with 422. |
| 19 | **Anomaly detection decoupled from ingest** | Detection runs after the ingest transaction commits. Detection failure does not fail the ingest request. |
| 20 | **Anomaly resolution uses stored baseline** | `baseline_value` and `baseline_stddev` are stored on the anomaly row. Resolution checks compare against the original baseline, not the current rolling window, to avoid threshold drift. |
| 21 | **Synchronous ingest in v1** | Simpler; run_id returned immediately. Bulk COPY makes synchronous ingest fast enough (< 1s for 10k handles). Move to async queue if throughput demands it. |
| 22 | **Combined frontend + API container in v1** | One container, simpler deployment. Revisit if the UI needs independent deployment or CDN. |
| 23 | **Ingest endpoint renamed from `/report` to `/raw`** | Avoids confusion between a rendered report and the raw JSON. `GET /api/v1/runs/{id}/raw` returns the original `test-report-v1` JSON. |

---

## Open Questions

| # | Question | Options | Impact |
|---|----------|---------|--------|
| 1 | **Package name: `chronicle` or something else?** | `chronicle` is descriptive but common. Alternatives: `choreo-chronicle`. | Needs a PyPI availability check before implementation. Blocking for implementation. |
| 2 | **Should `choreo_meta.tags` be extracted into a relational column?** | Yes: enables tag-based filtering in the UI. No: keeps the schema simpler; tags are accessible via JSONB. | If yes, needs a junction table or array column. Defer to v1.1 if not needed at launch. |

---

## Out of Scope (explicit)

- Authentication, authorisation, RBAC
- Webhook / Slack / PagerDuty alerting
- Grafana data source or Prometheus scrape endpoint
- Dark mode (v1 is light theme)
- Data export (CSV, PDF)
- Comparison mode (side-by-side runs)
- Custom anomaly rules or threshold configuration via UI
- Ingesting non-Choreo data (k6, Locust, custom formats)
- Hosted/SaaS offering
- Mobile-responsive UI
- Internationalisation / localisation
- Data deletion endpoints (purge runs, remove tenant) — deferred
- Multi-worker SSE fan-out (requires Redis or LISTEN/NOTIFY bridge)
- Reply report extraction into relational columns (PRD-008 data; JSONB only)
- Tag-based filtering (see Open Question 2)

---

## Testing Strategy

### API tests (pytest + httpx)

- `test_ingesting_a_valid_report_should_create_run_scenarios_and_handles`
- `test_ingesting_with_a_new_tenant_slug_should_auto_create_the_tenant`
- `test_ingesting_an_invalid_report_should_return_422_with_schema_errors`
- `test_ingesting_a_schema_v2_report_should_return_422`
- `test_ingesting_a_body_exceeding_20mb_should_return_413`
- `test_ingesting_with_idempotency_key_should_return_existing_run_on_duplicate`
- `test_ingesting_with_invalid_tenant_slug_should_return_422`
- `test_tenant_slug_should_be_normalised_to_lowercase`
- `test_listing_runs_should_filter_by_tenant_and_environment`
- `test_list_endpoints_should_return_paginated_envelope_with_total`
- `test_topic_latency_endpoint_should_return_percentiles_per_bucket`
- `test_topic_latency_should_auto_select_resolution_based_on_time_range`
- `test_topic_latency_should_union_raw_data_for_recent_partial_bucket`
- `test_anomaly_feed_should_return_detected_anomalies_newest_first`
- `test_sse_stream_should_emit_run_completed_event_on_ingest`
- `test_health_endpoint_should_return_ok_when_database_is_connected`
- `test_health_endpoint_should_return_503_when_database_is_unreachable`
- `test_error_responses_should_use_consistent_json_format`
- `test_error_responses_should_not_include_stack_traces`
- `test_rate_limiting_should_reject_excess_ingest_requests_with_429`
- `test_regression_timeline_should_limit_to_100_topics`

### Anomaly detection tests

- `test_a_latency_spike_beyond_two_sigma_should_create_a_warning_anomaly`
- `test_a_latency_spike_beyond_three_sigma_should_create_a_critical_anomaly`
- `test_three_consecutive_normal_runs_should_auto_resolve_the_anomaly`
- `test_resolution_should_compare_against_stored_baseline_not_current_rolling`
- `test_budget_violation_rate_exceeding_threshold_should_flag_anomaly`
- `test_outcome_shift_exceeding_threshold_should_flag_anomaly`
- `test_insufficient_baseline_data_should_not_produce_false_anomalies`
- `test_anomaly_detection_failure_should_not_fail_the_ingest_request`

### Ingest pipeline tests

- `test_bulk_insert_should_use_single_transaction`
- `test_partial_ingest_failure_should_roll_back_entire_transaction`
- `test_over_budget_should_be_derived_from_diagnosis_kind`
- `test_diagnosis_kind_should_be_extracted_from_handle_diagnosis`
- `test_pending_outcome_handles_should_be_stored_correctly`

### Database tests (against real TimescaleDB)

- `test_continuous_aggregates_should_produce_correct_percentiles`
- `test_continuous_aggregates_should_count_timeouts_correctly`
- `test_compression_should_not_alter_query_results`
- `test_handle_measurements_hypertable_should_partition_by_time`
- `test_baseline_index_should_support_tenant_environment_topic_queries`

### Frontend tests (Vitest + React Testing Library)

- Component-level tests for each view
- SSE reconnection behaviour and debounced re-rendering
- Time range selection and resolution switching
- Heatmap rendering with canvas (not SVG)
- Verify no use of `dangerouslySetInnerHTML` in the codebase

### Integration tests

- Full ingest-to-dashboard flow with Docker Compose
- Multi-tenant isolation: tenant A's data should not appear in tenant B's queries
- SSE connection limit enforcement
- Idempotent ingest round-trip

---

## Consumer Integration Guide

### Minimal CI integration (one line)

```bash
# In your CI pipeline, after pytest completes:
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Chronicle-Tenant: my-team" \
  https://chronicle.internal/api/v1/runs \
  -d @test-report/results.json
```

### Continuous testing (cron)

```bash
# Run Choreo tests every 5 minutes and report
*/5 * * * * cd /app && pytest -m probe --harness-report=/tmp/report && \
  curl -s -X POST \
    -H "Content-Type: application/json" \
    -H "X-Chronicle-Tenant: platform" \
    https://chronicle.internal/api/v1/runs \
    -d @/tmp/report/results.json
```

### Environment labelling

The `environment` field in the report comes from `HARNESS_ENV` (set by the
consumer, read by `choreo-reporter`). Chronicle uses it for filtering and
baseline separation — a regression on staging does not pollute the production
baseline.

---

## Related PRDs / ADRs

- [PRD-001 — Framework foundations](PRD-001-framework-foundations.md) — the Harness and transport layer
- [PRD-002 — Scenario DSL](PRD-002-scenario-dsl.md) — defines Handle, ScenarioResult, Outcome, Matcher
- [PRD-006 — Latency observability](PRD-006-latency-observability.md) — timeline, budgets, Outcome.SLOW
- [PRD-007 — Test report output](PRD-007-test-report-output.md) — `choreo-reporter` and the `test-report-v1` JSON schema that Chronicle ingests
- [PRD-008 — Scenario replies](PRD-008-scenario-replies.md) — reply reports included in the ingested data

---

## Appendix A — Positioning Statement

> **For** teams testing message-based systems with Choreo,
> **who** need to track performance trends and catch regressions over time,
> **Chronicle is** a self-hosted reporting server
> **that** ingests structured test results into TimescaleDB and surfaces
> per-topic latency trends, budget violations, and anomaly detection through
> an interactive dashboard.
> **Unlike** Allure (pass/fail reporting), Grafana (generic dashboards),
> or Datadog Synthetics (HTTP probing),
> **Chronicle** understands Choreo's outcome model — topics, matchers,
> latency budgets, and the distinction between PASS, FAIL, TIMEOUT, and SLOW —
> and provides longitudinal analytics purpose-built for async infrastructure.

## Appendix B — Competitive Landscape Summary

| Category | Tools | What they do | What they don't do (Chronicle's gap) |
|----------|-------|-------------|--------------------------------------|
| Test reporting | Allure, ReportPortal, Tesults | Pass/fail tracking, flake detection, test history | No latency analytics, no message-bus semantics, no budget tracking |
| Synthetic monitoring | Datadog Synthetics, Checkly, Grafana Synthetic | HTTP/browser endpoint probing | No message queue / event bus support |
| Performance testing | k6, Locust, Gatling | Request/response load generation + reporting | No pub/sub, no matcher semantics, no longitudinal per-topic tracking |
| Observability | Grafana + Prometheus, Jaeger | Metrics dashboards, distributed tracing | Passive (watches production traffic); no test-result semantics |
| CI analytics | Buildkite Test Analytics, Datadog CI | Test duration trends, flaky detection | Pass/fail only; no latency budgets, no topic-level granularity |

**Chronicle's unique position:** the only tool that combines message-bus-specific
test semantics (topics, matchers, delivery latency, payload shape assertions)
with longitudinal performance analytics and anomaly detection.
