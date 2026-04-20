# Chronicle — Longitudinal Test Performance Analytics

A reporting server that ingests [Choreo](../core/) test results over time,
stores them in TimescaleDB, detects latency regressions automatically, and
serves an interactive dashboard.  It answers questions no single test report
can: *"Is this topic getting slower?"*, *"What regressed after Tuesday's
deploy?"*, *"Which topics have the most budget violations this month?"*

- Python 3.11+ / FastAPI / SQLAlchemy 2.0 async / asyncpg
- TimescaleDB for time-series storage with continuous aggregates
- React + Recharts dashboard (pre-built, included in the wheel — no Node.js needed)

![Runs dashboard — pass rate, failures, and duration over time](../../docs/images/chronicle/runs.png)

![Topic comparison — overlay P50/P95/P99 latency across topics](../../docs/images/chronicle/compare.png)

![Topics list — per-topic latency stats with sorting](../../docs/images/chronicle/topics.png)

## Install

```bash
pip install choreo-chronicle
```

For development from source:

```bash
pip install -e 'packages/chronicle[test]'
```

## Quick start

```bash
# 1. Start TimescaleDB
docker run -d --name chronicle-db \
  -p 5433:5432 \
  -e POSTGRES_USER=chronicle \
  -e POSTGRES_PASSWORD=chronicle \
  -e POSTGRES_DB=chronicle \
  timescale/timescaledb-ha:pg18

# 2. Run migrations
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle migrate

# 3. Start the server
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle

# 4. Ingest a test report
curl -X POST http://localhost:5173/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-Chronicle-Tenant: my-team" \
  -d @test-report/results.json

# 5. Open the dashboard
open http://localhost:5173
```

## CLI

```bash
python -m chronicle                  # start dev server (hot reload on :5173)
python -m chronicle --no-reload      # start without reload
python -m chronicle migrate          # run database migrations (upgrade to head)
python -m chronicle migrate status   # show current migration revision
python -m chronicle migrate downgrade -1  # rollback one migration
```

All commands read `DATABASE_URL` or `CHRONICLE_DATABASE_URL` from the
environment.

## CI integration

Add one line to your pipeline after `pytest` completes:

```bash
curl -X POST https://chronicle.internal/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-Chronicle-Tenant: my-team" \
  -d @test-report/results.json
```

## API

All endpoints under `/api/v1/`.  Interactive docs at `/api/v1/docs`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/runs` | Ingest a `test-report-v1` JSON report |
| `GET` | `/runs` | List runs for a tenant |
| `GET` | `/runs/{id}` | Run detail with scenarios |
| `GET` | `/runs/{id}/raw` | Original JSON as ingested |
| `GET` | `/tenants` | List tenants |
| `GET` | `/topics` | List topics with latency stats |
| `GET` | `/topics/{topic}/latency` | Time-series p50/p95/p99 data |
| `GET` | `/topics/{topic}/runs` | Runs containing a topic |
| `GET` | `/anomalies` | Anomaly feed |
| `GET` | `/anomalies/topics/{topic}` | Anomalies for a topic |
| `GET` | `/stream` | SSE live events |
| `GET` | `/health` | Health check (shallow + deep) |

## Architecture

Repository + Service Layer (ADR-0021).  Three layers with strict import
direction:

```
api/           → services/       → repositories/     → models/
(thin routes)    (business logic)   (database access)   (ORM tables)
```

The ingest pipeline flows through `IngestService.ingest()`:
validate → normalise → persist (single transaction with COPY) →
detect anomalies (separate session) → broadcast SSE events.

## Configuration

All config via environment variables with `CHRONICLE_` prefix.
`DATABASE_URL` accepts both prefixed and unprefixed forms.

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/chronicle
CHRONICLE_ENVIRONMENT=production    # triggers startup validation
CHRONICLE_LOG_FORMAT=json           # required in production
CHRONICLE_DB_POOL_SIZE=10
CHRONICLE_MAX_SSE_CONNECTIONS=200
```

The app refuses to start in production with default localhost credentials.
See `config.py` for the full list.

## Distribution

The React dashboard is pre-built in CI and included in the Python wheel
via Hatchling `force-include`.  When a consumer runs `pip install
choreo-chronicle`, they get a working dashboard without Node.js:

```
choreo_chronicle-0.1.0.whl
  chronicle/
    app.py, config.py, ...
    static/                ← pre-built React app
      index.html
      assets/index-*.js
      assets/index-*.css
```

FastAPI serves the static files at `/` and the API at `/api/v1/`.

## Tests

```bash
# Unit + integration (no database needed)
pytest packages/chronicle/tests/

# Unit only
pytest packages/chronicle/tests/unit/

# E2E against real TimescaleDB
docker compose -f docker/compose.chronicle.yaml up -d
python -m chronicle migrate
pytest packages/chronicle/tests/ -m chronicle_db
```

The test suite includes Hypothesis property-based tests and API fuzz
testing.  DB tests truncate all tables after each test for isolation.

## Design docs

- [PRD-009](../../docs/prd/PRD-009-chronicle-reporting-server.md) — product requirements
- [ADR-0021](../../docs/adr/0021-chronicle-api-structure.md) — API architecture
- [ADR-0025](../../docs/adr/0025-chronicle-topic-drilldown-backend.md) — topic query design
