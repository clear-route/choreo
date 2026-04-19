# 0021. Chronicle API Structure -- Repository + Service Layer for a RESTful Reporting Server

**Status:** Accepted
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-009 -- Chronicle Reporting Server](../prd/PRD-009-chronicle-reporting-server.md)

---

## Revision note (v3 -- post-implementation)

v3 updates the ADR to match the code that was built, reviewed, and tested.
Material changes from v2:

- **Status changed to Accepted.** Phase 1 is complete and passing tests.
- **Package layout** updated to match reality. Unbuilt items marked with
  phase numbers. `api/tenants.py` added (not in v2). `api/topics.py`,
  `api/anomalies.py`, `api/streaming.py` moved to Phase 2/4. Middleware
  files (security headers, request size, rate limit, request ID, GZip)
  moved to Phase 3. `models/hypertables.py` moved to Phase 3.
- **Code examples** replaced with the actual implementation code. Key
  changes: `create_app` accepts optional `Settings` and stores on
  `app.state` (no module-level singleton); `BroadcastChannel` constructor
  has 4 parameters; `IngestService` does not receive `anomaly_repo`
  directly; error handlers include `ReportValidationError`; health endpoint
  returns `Response` not `dict`; detection service is synchronous.
- **`normalise.py`** is a free function (`normalise_report()`), not a
  method on a service. `NewAnomaly` dataclass lives in this module.
- **Second migration** (`06014e2d1b77`) added for `topic_count`/percentile
  denormalisation columns on `runs`.
- **Timeline** updated to reflect what was delivered vs. what remains.
- **Validation** updated with actually-achieved metrics: property-based
  tests, fuzz tests, mutation testing, e2e roundtrip tests.
- **Caching, pool monitoring, structured logging** moved to future phases.
- **Resolution service** shown as implemented but with `NotImplementedError`
  stubs in `TopicRepository` -- the service logic is complete, the SQL is
  not yet written.

---

## Context

Chronicle is a reporting server that ingests Choreo `test-report-v1` JSON,
stores it in TimescaleDB, runs anomaly detection, streams events via SSE, and
serves a React dashboard. PRD-009 specifies the data model, API surface, and
non-functional requirements. This ADR records the internal structure of the
FastAPI application: how code is organised, which abstractions exist, where
business logic lives, and how the components connect.

### Background

- [PRD-009](../prd/PRD-009-chronicle-reporting-server.md) defines the API
  surface (6 query endpoints, 1 ingest endpoint, 1 SSE endpoint, 1 health
  endpoint), the TimescaleDB schema, and the ingest pipeline.
- The stack is FastAPI + SQLAlchemy 2.0 async + asyncpg + Pydantic v2 +
  Alembic + TimescaleDB.
- The core `choreo` library uses the Strategy pattern (ADR-0013), Protocol-
  based abstractions (Transport Protocol), and dataclass-based models. Chronicle
  should feel like a natural extension of the project's design vocabulary.

### Problem Statement

How should Chronicle's FastAPI application be structured internally so that
business logic is testable without a database, the ingest pipeline is composable,
and contributors can navigate the codebase within minutes?

### Goals

- **Testable business logic.** Anomaly detection, field derivation, and schema
  validation should be unit-testable without a running TimescaleDB instance.
- **Clear responsibility boundaries.** A contributor should know where to find
  ingest logic, database queries, API routes, and Pydantic models without
  searching.
- **Composable ingest pipeline.** The steps (validate, normalise, persist,
  detect, broadcast) should be explicit functions, not a single 300-line handler.
- **Minimal abstraction.** No layers that exist only to satisfy a pattern.
  Every file should contain code that would otherwise be duplicated or entangled.
- **Consistent with the rest of the monorepo.** Use Python Protocols where the
  core library does; use dataclasses where appropriate; follow the same naming
  and testing conventions.

### Non-Goals

- Swappable database backends. Chronicle is built on TimescaleDB. There is no
  need to abstract over the database engine.
- Plugin architecture. Chronicle is a closed application, not a framework.
- Microservice decomposition. Chronicle is a single service.
- GraphQL or gRPC. The API is strict REST + SSE.

---

## Decision Drivers

- **Team size.** A small team maintaining a focused tool. Cognitive overhead
  must be low.
- **TimescaleDB specifics.** Bulk COPY ingest, hypertable queries, continuous
  aggregates, and compression are not representable through a generic ORM
  abstraction. The database layer must be Postgres-aware.
- **Ingest complexity.** A single POST triggers: validation, normalisation,
  bulk insert (COPY protocol), anomaly detection, and SSE broadcast. These
  steps have different failure modes and should be independently testable.
- **Read-heavy dashboard.** Most API calls are reads against continuous
  aggregates. The read path is simpler than the write path and should not
  carry write-path complexity.
- **FastAPI's dependency injection.** The framework provides `Depends()` for
  request-scoped resources (sessions, services). The structure should use this
  idiom, not fight it.

---

## Considered Options

### Option 1: Flat module structure (no layers)

**Description:** One module per resource (`runs.py`, `topics.py`,
`anomalies.py`). Each module contains route handlers, Pydantic models, and
database queries. No service or repository abstraction. FastAPI `Depends()`
for database sessions only.

```
chronicle/
  app.py
  config.py
  models.py         # all SQLAlchemy models
  runs.py           # routes + queries + schemas for /runs
  topics.py         # routes + queries + schemas for /topics
  anomalies.py      # routes + queries + schemas for /anomalies
  stream.py         # SSE endpoint
  ingest.py         # POST /runs handler + all ingest logic
```

**Pros:**
- Fewest files and directories. A contributor reads one file to understand one
  resource end-to-end.
- No abstraction overhead. Queries are inline with their route handlers.
- Requires no upfront directory structure decisions; a new contributor writes
  code in the first file they open.

**Cons:**
- Ingest logic (validation, normalisation, COPY, detection, broadcast) lives in
  one file that grows to 400+ lines and mixes HTTP concerns with business logic.
- Anomaly detection is entangled with database queries. Unit testing detection
  logic requires a running database or heavy mocking.
- Pydantic models, SQLAlchemy models, and query logic share a namespace. Name
  collisions and import tangles emerge as the API grows.
- Route handlers become integration-test-only. No unit tests for business logic
  without standing up the full stack.

### Option 2: Repository + Service Layer (chosen)

**Description:** Three layers: API routes (thin), services (business logic),
repositories (database access). Pydantic schemas and SQLAlchemy models are
separate modules. FastAPI `Depends()` wires services into route handlers.

```
chronicle/
  app.py                    # FastAPI application factory
  config.py                 # pydantic-settings
  dependencies.py           # Depends providers (session, services)
  broadcast.py              # SSE fan-out channel (single class, app-scoped)
  exceptions.py             # Domain exceptions (ReportValidationError)
  __main__.py               # Dev server entry point
  middleware/
    __init__.py
    error_handlers.py       # Global exception handlers (no stack traces)
    security_headers.py     # Phase 3
    request_size.py         # Phase 3
    rate_limit.py           # Phase 3
    request_id.py           # Phase 3
  models/
    __init__.py
    tables.py               # SQLAlchemy 2.0 mapped classes (Mapped, mapped_column)
    hypertables.py          # Phase 3 -- TimescaleDB DDL helpers for migrations
  schemas/
    __init__.py
    ingest.py               # IngestRequest, IngestResponse
    runs.py                 # RunSummary, RunDetail, RunListResponse, TenantSummary
    topics.py               # TopicSummary, LatencyBucket, TopicLatencyResponse
    anomalies.py            # AnomalyCard, AnomalyListResponse
    common.py               # PagedResponse[T], ErrorResponse
  repositories/
    __init__.py
    run_repo.py             # Run + scenario CRUD, bulk COPY, tenant queries
    topic_repo.py           # Topic latency queries (stubs -- Phase 2)
    anomaly_repo.py         # Anomaly CRUD + baseline queries
  services/
    __init__.py
    ingest_service.py       # Orchestrates the ingest pipeline
    detection_service.py    # Anomaly detection logic (pure, no DB imports)
    normalise.py            # normalise_report() free function + shared types
    resolution_service.py   # Query resolution selection (raw/hourly/daily)
  api/
    __init__.py
    runs.py                 # POST /runs, GET /runs, GET /runs/{id}, GET /runs/{id}/raw
    tenants.py              # GET /tenants
    health.py               # GET /health, GET /health?deep=true
    topics.py               # Phase 2 -- GET /topics, GET /topics/{topic}/latency
    anomalies.py            # Phase 2 -- GET /anomalies
    streaming.py            # Phase 4 -- GET /stream (SSE)
  migrations/               # Alembic
    env.py
    versions/
      001_initial_schema.py
      06014e2d1b77_add_run_stats_columns.py
  static/                   # Pre-built React frontend (Phase 5)
```

**Pros:**
- **Testable business logic.** `detection_service.py` receives data, not a
  session. Unit tests pass in-memory data without a database.
- **Composable ingest.** `ingest_service.py` calls repository methods, then
  detection, then broadcast. Each step is a function call with a clear
  contract. A detection failure is caught and logged without rolling back
  the ingest transaction.
- **Clear boundaries.** Route handlers are 10-20 lines: parse request,
  call service, return schema. Repositories own SQL. Services own logic.
- **TimescaleDB-specific code is isolated.** `run_repo.py` contains the
  `COPY`-protocol bulk insert. `topic_repo.py` queries continuous aggregates
  and unions raw data for partial buckets. This Postgres-specific code does
  not leak into services.
- **Pydantic and SQLAlchemy models do not collide.** Separate namespaces
  prevent the common FastAPI confusion of `Run` (ORM) vs `Run` (schema).

**Cons:**
- More files than Option 1. ~30 files vs ~10.
- Repositories are thin pass-throughs for simple queries (`get_anomalies`
  is a single SELECT). The abstraction pays off only for complex queries.
- Contributors must learn the layer convention. Mitigated by the directory
  names being self-documenting.

### Option 3: Clean Architecture / Hexagonal

**Description:** Domain entities, application use-cases with port interfaces,
infrastructure adapters, and a presentation layer.

```
chronicle/
  domain/
    entities.py             # Run, Scenario, Handle (domain objects)
    events.py               # RunIngested, AnomalyDetected
    ports.py                # RunRepository (Protocol), Broadcaster (Protocol)
  application/
    ingest_use_case.py
    query_latency_use_case.py
    detect_anomalies_use_case.py
  infrastructure/
    postgres/
      run_adapter.py        # implements RunRepository
      topic_adapter.py
    sse/
      broadcast_adapter.py  # implements Broadcaster
  presentation/
    fastapi/
      routes/
        runs.py
        topics.py
```

**Pros:**
- Maximum testability. Domain logic has zero framework imports.
- Adapters are swappable (e.g. replace Postgres with ClickHouse by writing
  a new adapter).
- Enforces dependency inversion; inner layers never import outer layers.

**Cons:**
- Disproportionate overhead at this scale. Chronicle has one database
  (TimescaleDB) and one transport (SSE). The abstraction to swap either is
  waste.
- Domain entities duplicate ORM models. `domain.Run` and
  `infrastructure.postgres.RunModel` carry the same fields. Mapping between
  them adds boilerplate with no benefit.
- 4-5 layers deep. A contributor tracing a request from route to database
  crosses domain, use-case, port, adapter, SQL. For a 10-endpoint API this
  is disproportionate.
- Port Protocols need maintaining. Every repository method change touches
  the Protocol, the adapter, and the use-case. Three files for one query.
- Does not match the rest of the monorepo's pragmatic style (the core library
  uses Protocols where there are genuinely multiple implementations, not as
  a blanket policy).

### Option 4: CQRS-lite

**Description:** Separate command (write) and query (read) paths. Commands go
through a pipeline with middleware steps. Queries go directly to optimised
read models.

```
chronicle/
  commands/
    ingest_command.py       # dataclass
    ingest_handler.py       # handler
    middleware/
      validation.py
      normalisation.py
      anomaly_detection.py
      broadcast.py
  queries/
    run_queries.py
    topic_queries.py
    anomaly_queries.py
  models/
  schemas/
  api/
```

**Pros:**
- Conceptually clean: the ingest path (write) and the dashboard queries (read)
  have different concerns and optimisation profiles.
- Middleware pipeline makes the ingest steps explicit and reorderable.
- Matches the data model naturally: writes go to `handle_measurements`,
  reads come from continuous aggregates.

**Cons:**
- Pipeline abstraction is overkill. The ingest pipeline has 5 fixed steps.
  A sequence of function calls in a service method achieves the same thing
  without a middleware framework.
- CQRS vocabulary is unfamiliar to contributors who know REST. "Command
  handler" and "query" map to "POST handler" and "GET handler" -- renaming
  without adding value.
- Two parallel hierarchies (commands + queries) for a 10-endpoint API.
  The cognitive map is harder, not simpler.
- The read side (queries directly hitting aggregates) is identical to what
  Option 2's repositories provide. The write side's pipeline is identical to
  what Option 2's service provides. CQRS adds terminology without adding
  capability at this scale.

---

## Decision

**Chosen Option:** Option 2 -- Repository + Service Layer.

### Rationale

- **Option 1** is too flat. The ingest pipeline's 5 steps (validate, normalise,
  persist, detect, broadcast) with different failure modes cannot live in one
  function without becoming untestable. Anomaly detection logic must be
  unit-testable without a database.
- **Option 3** is too deep. Chronicle has one database, one broadcast mechanism,
  and no plans to swap either. Hexagonal architecture's value comes from
  genuine multi-adapter needs; here it adds 3 extra layers of indirection and
  domain-model duplication for zero benefit.
- **Option 4** renames the same separation that Option 2 provides (services
  handle writes, repositories handle reads/writes) using CQRS vocabulary that
  adds learning cost without adding capability at this endpoint count.
- **Option 2** hits the sweet spot: business logic is testable without a
  database (services receive data, not sessions), TimescaleDB-specific code is
  isolated in repositories, and route handlers stay thin. The structure is
  self-documenting (directory names are the layers) and matches the pragmatic
  style of the rest of the monorepo.

---

## Consequences

### Positive

- Anomaly detection can be unit-tested with in-memory data: pass a list of
  p95 values, assert that a warning anomaly is returned. No database, no
  fixtures, sub-second test runs.
- The ingest pipeline reads as a sequence of named steps in
  `ingest_service.py`. Each step's contract is a function signature. A new
  step (e.g. tag extraction in a future version) is one function call added
  to the sequence.
- Repository methods are the only code that imports `sqlalchemy` or calls
  asyncpg. Service tests never need to mock SQL.
- Route handlers are 10-20 lines. They parse the request, call a service
  method, and return a Pydantic schema. Reviewing a route change is trivial.

### Negative

- Thin repository methods (e.g. `list_anomalies` wrapping a single
  SELECT) feel like unnecessary indirection. Mitigated by: consistency -- all
  database access goes through the same layer, so contributors always know
  where to look.
- ~30 source files vs ~10 for the flat approach. Mitigated by: directory
  structure is self-documenting; `api/`, `services/`, `repositories/`,
  `schemas/`, `models/` are industry-standard names.
- Services own transaction boundaries (see "Session Management"). This means
  the service reaches through the repository to control the session's
  transaction lifecycle -- a deliberate pragmatic choice documented as a
  design convention, not a leaky abstraction.

### Neutral

- Pydantic schemas and SQLAlchemy models are separate types. There is no
  `model_validate` shortcut from ORM to response. Explicit mapping in the
  route handler is required. This is intentional -- the API response shape
  is not the database row shape (e.g. `RunSummary` includes `anomaly_count`
  which is a JOIN, not a column).
- Alembic migrations live in `migrations/` and contain raw SQL for
  TimescaleDB-specific DDL (`create_hypertable`, `add_compression_policy`,
  continuous aggregate creation). The ORM models define the table structure;
  the migrations add the TimescaleDB overlay.

### Security Considerations

- **SQL injection.** All database access goes through repositories, which use
  SQLAlchemy's parameterised query builder or asyncpg's `$1`-style parameters.
  No string interpolation of user input into SQL anywhere in the codebase.
  This is enforced by convention (route handlers never import `sqlalchemy`)
  and verified by a grep-based CI check: `grep -r "from sqlalchemy" api/`
  must return zero matches.
- **Request validation.** Three-layer validation strategy (see "Request
  Validation"). Pydantic v2 validates structure, JSON Schema validates
  `test-report-v1` compliance, database constraints enforce invariants.
  Invalid input never reaches a repository.
- **Error leakage.** A global exception handler returns `ErrorResponse` for
  all unhandled exceptions. Stack traces and internal paths are never included
  in API responses. `debug=False` in production. Input values are stripped
  from Pydantic validation errors to prevent reflecting submitted data.
- **Cross-tenant SSE isolation.** The BroadcastChannel filters events by
  `tenant_id`. A client subscribed to tenant A never receives events from
  tenant B.
- **SSE session safety.** The SSE route handler does NOT depend on
  `get_session`. Long-lived SSE connections never hold database connections.

---

## Implementation

### Application Factory

`app.py` creates the FastAPI application, registers routers, and configures
error handlers. `Settings` is accepted as an optional constructor argument
(for testing) and stored on `app.state` -- no module-level singleton:

```python
# app.py (abbreviated -- full source in packages/chronicle/src/chronicle/app.py)
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.validate_production_config()
    engine, sessionmaker = create_engine_and_sessionmaker(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.broadcast = BroadcastChannel(
        max_connections=settings.max_sse_connections,
    )
    yield
    app.state.broadcast.shutdown()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    app = FastAPI(
        title="Chronicle",
        description="Longitudinal reporting server for Choreo test performance analytics",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        debug=False,
    )
    app.state.settings = settings

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(tenants.router, prefix="/api/v1")
    register_error_handlers(app)

    # Static frontend mounted last so API routes take precedence
    if _STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    return app
```

### Configuration (12-Factor)

`config.py` uses `pydantic-settings` with the `CHRONICLE_` env prefix.
`DATABASE_URL` accepts both prefixed and unprefixed via `AliasChoices`.
Production validation rejects dev defaults at startup:

```python
# config.py (abbreviated)
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHRONICLE_",
        env_nested_delimiter="__",
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",
    )

    environment: str = "local"
    database_url: str = Field(
        default="postgresql+asyncpg://chronicle:chronicle@localhost:5432/chronicle",
        validation_alias=AliasChoices("DATABASE_URL", "CHRONICLE_DATABASE_URL"),
    )
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_format: str = "text"  # "json" in production
    db_pool_size: int = 5
    db_pool_max: int = 20
    db_pool_timeout: int = 30
    max_body_size: int = 20_971_520  # 20 MB
    max_sse_connections: int = 100

    # Anomaly detection thresholds
    baseline_window: int = 10
    baseline_min_samples: int = 5
    baseline_sigma: float = 2.0
    budget_violation_pct: float = 20.0
    outcome_shift_pct: float = 5.0

    def validate_production_config(self) -> None:
        if not self.is_production():
            return
        if "chronicle:chronicle@localhost" in self.database_url:
            raise ValueError("DATABASE_URL contains default local credentials ...")
        if self.log_format != "json":
            raise ValueError("Production requires CHRONICLE_LOG_FORMAT=json ...")
```

### Dependency Injection

`dependencies.py` provides request-scoped resources via `Depends()`. The
`IngestService` receives the `sessionmaker` (not an `anomaly_repo`) so it
can create a separate session for detection:

```python
# dependencies.py (abbreviated)
def create_engine_and_sessionmaker(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max - settings.db_pool_size,
        pool_timeout=settings.db_pool_timeout,
        echo=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_ingest_service(
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    detection: Annotated[DetectionService, Depends(get_detection_service)],
    broadcast: Annotated[BroadcastChannel, Depends(get_broadcast)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
) -> IngestService:
    return IngestService(
        run_repo=run_repo,
        detection=detection,
        broadcast=broadcast,
        sessionmaker=sessionmaker,
    )
```

### Request Validation Strategy

Chronicle validates inbound data at three levels:

**Level 1: Pydantic v2 (structural validation)**

```python
# schemas/ingest.py
class IngestRequest(BaseModel):
    schema_version: str
    run: dict
    tests: list[dict]

    @field_validator("schema_version")
    @classmethod
    def must_be_v1(cls, v: str) -> str:
        if not v.startswith("1"):
            raise ValueError(f"schema_version '{v}' is not supported; expected '1' or '1.x'")
        return v
```

**Level 2: JSON Schema (semantic validation)** -- the ingest service validates
the full report body against `docs/schemas/test-report-v1.json`. Errors are
capped at 10 and raised as `ReportValidationError`.

**Level 3: Database constraints (invariant enforcement)** -- PostgreSQL CHECK
constraints, UNIQUE indexes, and foreign keys serve as a final safety net.

### Normalisation

`normalise_report()` is a free function in `services/normalise.py`, not a
method on a service class. It extracts fields from the raw JSON, derives
`over_budget` from `diagnosis.kind`, and flattens the test->scenario->handle
hierarchy into frozen dataclasses:

```python
# services/normalise.py (key types)
@dataclass(frozen=True)
class NormalisedHandle:
    topic: str
    outcome: str
    latency_ms: float | None
    budget_ms: float | None
    attempts: int
    matcher_description: str
    diagnosis_kind: str | None
    over_budget: bool

@dataclass(frozen=True)
class NormalisedScenario:
    test_nodeid: str
    name: str
    correlation_id: str | None
    outcome: str
    duration_ms: float
    completed_normally: bool
    handles: tuple[NormalisedHandle, ...]

@dataclass(frozen=True)
class NormalisedReport:
    # Run metadata + totals + scenarios
    # ... (see full source)
    scenarios: list[NormalisedScenario]

@dataclass(frozen=True)
class NewAnomaly:
    """An anomaly to be persisted. No id or detected_at -- generated by DB."""
    tenant_id: UUID
    run_id: UUID
    topic: str
    detection_method: str  # "rolling_baseline", "budget_violation", "outcome_shift"
    metric: str            # "p95_ms", "budget_violation_pct", "timeout_rate"
    current_value: float
    baseline_value: float
    baseline_stddev: float
    change_pct: float
    severity: str          # "warning", "critical"

def normalise_report(report: object) -> NormalisedReport:
    """Extract and normalise fields from the raw report."""
    # Derives over_budget from handle.diagnosis.kind == "over_budget"
    # Flattens test->scenario->handle hierarchy
    # ... (see full source)
```

### Ingest Pipeline

`IngestService.ingest()` orchestrates six steps. The service creates a
separate session for anomaly detection so the ingest connection is released
before running baseline queries:

```python
# services/ingest_service.py (abbreviated)
class IngestService:
    def __init__(self, run_repo, detection, broadcast, sessionmaker, report_schema=None):
        self._run_repo = run_repo
        self._detection = detection
        self._broadcast = broadcast
        self._sessionmaker = sessionmaker
        self._report_schema = report_schema

    async def ingest(self, report: IngestRequest, tenant_slug: str, idempotency_key: str | None) -> IngestResponse:
        # 1. Validate against JSON Schema (level 2)
        self._validate_report(report.model_dump())

        # 2. Normalise
        normalised = normalise_report(report)

        # 3. Check for late reports (> 1 day old)
        warning: str | None = None
        age = datetime.now(timezone.utc) - normalised.started_at
        if age > _LATE_REPORT_THRESHOLD:
            warning = f"Report started_at is {age.days}d {age.seconds // 3600}h ago; ..."

        # 4. Persist in a single transaction
        try:
            async with self._run_repo.session.begin():
                tenant = await self._run_repo.upsert_tenant(tenant_slug)
                run = await self._run_repo.create_run(tenant, normalised=normalised, ...)
                db_scenarios = await self._run_repo.bulk_insert_scenarios(run, scenario_dicts)
                rows_inserted = await self._run_repo.copy_handle_measurements(run, db_scenarios, handles_by_scenario)
        except IntegrityError:
            # Optimistic concurrency: re-query with fresh session
            if idempotency_key is None:
                raise
            async with self._sessionmaker() as fresh_session:
                existing = await RunRepository(fresh_session).find_by_idempotency_key(idempotency_key)
            return IngestResponse(run_id=existing.id, duplicate=True, ...)

        # 5. Detect anomalies in a SEPARATE session
        detected: list[NewAnomaly] = []
        try:
            async with self._sessionmaker() as detection_session:
                anomaly_repo = AnomalyRepository(detection_session)
                baselines = {topic: await anomaly_repo.get_baseline_values(...) for topic in normalised.topics}
                detected = self._detection.detect(tenant_id=tenant.id, run_id=run.id, normalised=normalised, baselines=baselines)
                if detected:
                    await anomaly_repo.bulk_create_from_new(detected)
                    await detection_session.commit()
        except Exception:
            logger.exception("anomaly detection failed for run %s", run.id, ...)

        # 6. Broadcast SSE events -- always fires
        await self._broadcast.emit(tenant_id=tenant.id, event_type="run.completed", data={...})
        for anomaly in detected:
            await self._broadcast.emit(tenant_id=tenant.id, event_type="anomaly.detected", data={...})

        return IngestResponse(run_id=run.id, duplicate=False, handles_ingested=rows_inserted, ...)
```

**Key design decisions:**
- **Two sessions.** The detection session is separate from the ingest session.
  The ingest connection returns to the pool before baseline queries run.
- **`AnomalyRepository` created inside the method**, not injected. It uses
  the detection session, not the request-scoped session.
- **`IntegrityError` recovery** uses a fresh session because SQLAlchemy
  invalidates the session after an integrity error.
- **Late report warning** is returned in the response but does not block ingest.

### Anomaly Detection

`DetectionService` is a pure synchronous service with no database imports.
The ingest service provides baseline data; detection returns `NewAnomaly`
dataclasses:

```python
# services/detection_service.py (abbreviated)
class DetectionService:
    def __init__(self, config: DetectionConfig | None = None):
        self._config = config or DetectionConfig()

    def detect(self, *, tenant_id, run_id, normalised: NormalisedReport,
               baselines: dict[str, list[float]]) -> list[NewAnomaly]:
        # For each topic:
        #   1. Rolling baseline comparison (p95 latency vs mean + sigma * stddev)
        #   2. Budget violation rate (% of over_budget handles > threshold)
        # Returns NewAnomaly dataclasses for the caller to persist
```

### Error Handling

Global exception handlers ensure all responses use `ErrorResponse` format.
Includes a handler for `ReportValidationError` (JSON Schema failures):

```python
# middleware/error_handlers.py (abbreviated)
def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        safe_errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": str(safe_errors)})

    @app.exception_handler(ReportValidationError)
    async def report_validation_error(request, exc):
        return JSONResponse(status_code=422, content={
            "error": "report_validation_error",
            "detail": f"Report failed schema validation: {'; '.join(exc.messages)}",
        })

    @app.exception_handler(HTTPException)
    async def http_error(request, exc): ...

    @app.exception_handler(Exception)
    async def unhandled_error(request, exc):
        logger.exception("unhandled exception", extra={"request_id": ..., "path": ...})
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "An unexpected error occurred."})
```

### Route Handlers

Route handlers are thin -- validate, delegate, return:

```python
# api/runs.py (abbreviated -- POST handler)
@router.post("/runs", status_code=201, response_model=IngestResponse)
async def ingest_run(
    body: IngestRequest,
    x_chronicle_tenant: Annotated[str, Header()],
    service: Annotated[IngestService, Depends(get_ingest_service)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    tenant_slug = x_chronicle_tenant.lower()
    if not _TENANT_SLUG_RE.match(tenant_slug):
        raise HTTPException(status_code=422, detail=f"Invalid tenant slug: '{tenant_slug}'. ...")
    return await service.ingest(body, tenant_slug, idempotency_key)


# api/runs.py (GET handler)
@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: UUID,
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
) -> RunDetail:
    run = await run_repo.get_run_with_scenarios(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    # Maps ORM objects to Pydantic schemas in the route handler
    return RunDetail(id=run.id, tenant_slug=run.tenant.slug, scenarios=[...], ...)
```

### Health Endpoint

```python
# api/health.py (abbreviated)
@router.get("/health", response_model=None)
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
    deep: Annotated[bool, Query()] = False,
) -> Response:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unreachable"})

    result = {"status": "ok", "database": "connected"}
    if deep:
        # Verify TimescaleDB extension and hypertable exist
        row = await session.execute(text(
            "SELECT count(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'handle_measurements'"
        ))
        hypertable_count = row.scalar_one()
        result["hypertable"] = "present" if hypertable_count > 0 else "missing"
        if hypertable_count == 0:
            return JSONResponse(status_code=503, content=result)
    return JSONResponse(status_code=200, content=result)
```

### SSE Broadcast Channel

`broadcast.py` manages in-memory fan-out with tenant isolation. Constructor
accepts four parameters; all have defaults:

```python
# broadcast.py (abbreviated)
class BroadcastChannel:
    def __init__(
        self,
        max_connections: int = 100,
        event_buffer_size: int = 1000,
        client_queue_size: int = 100,
        heartbeat_interval_seconds: float = 30,
    ) -> None:
        self._max_connections = max_connections
        self._clients: dict[int, tuple[UUID, asyncio.Queue]] = {}
        self._event_buffer: deque[SSEEvent] = deque(maxlen=event_buffer_size)
        # ...

    async def subscribe(self, tenant_id: UUID, last_event_id: int | None = None) -> AsyncGenerator[...]:
        # Replay missed events, then yield new events with heartbeat on timeout

    async def emit(self, tenant_id: UUID, event_type: str, data: dict) -> None:
        # Fan-out to clients subscribed to the given tenant

    def shutdown(self) -> None:
        # Send None sentinels to all client queues, then clear
        for _, (_, queue) in self._clients.items():
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._clients.clear()
```

### Bulk Insert via COPY Protocol

```python
# repositories/run_repo.py (abbreviated)
HANDLE_COPY_COLUMNS: list[str] = [
    "time", "tenant_id", "run_id", "scenario_id", "environment", "transport",
    "branch", "topic", "outcome", "latency_ms", "budget_ms", "attempts",
    "matcher_description", "diagnosis_kind", "over_budget",
]

async def copy_handle_measurements(self, run, scenarios, handles_by_scenario) -> int:
    records = []
    for scenario in scenarios:
        for handle_fields in handles_by_scenario.get(scenario.id, []):
            records.append((run.started_at, run.tenant_id, run.id, scenario.id,
                            run.environment, run.transport, run.branch, *handle_fields))
    if not records:
        return 0

    sa_connection = await self.session.connection()
    raw_connection = await sa_connection.get_raw_connection()
    asyncpg_conn = raw_connection.driver_connection
    await asyncpg_conn.copy_records_to_table("handle_measurements", records=records, columns=HANDLE_COPY_COLUMNS)
    return len(records)
```

### ORM Models

`models/tables.py` defines five mapped classes: `Tenant`, `Run`, `Scenario`,
`HandleMeasurement`, and `Anomaly`. All use SQLAlchemy 2.0 `Mapped` /
`mapped_column` style. Key design notes:

- `HandleMeasurement` uses a composite primary key (`time`, `tenant_id`)
  because TimescaleDB hypertables do not support unique constraints across
  chunks.
- `Run` includes denormalised stats columns (`topic_count`, `p50_ms`,
  `p95_ms`, `p99_ms`) added in migration `06014e2d1b77`.
- Relationships: `Tenant` -> `Run` (one-to-many), `Run` -> `Scenario`
  (one-to-many with cascade delete).

See full source: `packages/chronicle/src/chronicle/models/tables.py`

### Migration Strategy: Alembic + TimescaleDB

The initial migration (`001_initial_schema.py`) creates all tables, indexes,
the hypertable, compression policy, and continuous aggregates. The continuous
aggregate statements require a `COMMIT` workaround because TimescaleDB cannot
create materialized views inside a transaction:

```python
# migrations/versions/001_initial_schema.py (key workaround)
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    # ... create tenants, runs, scenarios, handle_measurements tables ...
    op.execute("SELECT create_hypertable('handle_measurements', 'time')")
    # ... compression policy ...

    # TimescaleDB continuous aggregates cannot be created inside a
    # transaction block. Commit the current transaction first.
    op.execute(sa.text("COMMIT"))
    op.execute("""
        CREATE MATERIALIZED VIEW topic_latency_hourly
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 hour', time) AS bucket, ...
    """)
    # ... hourly + daily aggregates and refresh policies ...
    # ... anomalies table ...

def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS topic_latency_daily CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS topic_latency_hourly CASCADE")
    op.drop_table("anomalies")
    op.drop_table("handle_measurements")
    op.drop_table("scenarios")
    op.drop_table("runs")
    op.drop_table("tenants")
```

A second migration (`06014e2d1b77`) adds denormalised stats columns
(`topic_count`, `p50_ms`, `p95_ms`, `p99_ms`) to the `runs` table.

### Session Management

One `AsyncSession` per request, obtained via `Depends(get_session)`. Services
own transaction boundaries.

The ingest service uses **two sessions** to prevent connection pool starvation.
After the ingest transaction commits (step 4), the connection returns to the
pool. A separate session is created for anomaly detection (step 5), which runs
O(topics) baseline queries.

**Scalability trigger:** If p99 ingest latency exceeds the 1-second NFR target,
move detection to `asyncio.create_task` and decouple it from the request
lifecycle entirely. The service interface does not change.

### Resolution Service

`resolution_service.py` auto-selects the data source based on time range and
unions raw data for partial buckets. The logic is fully implemented; the
underlying `TopicRepository` SQL methods are stubs (`NotImplementedError`)
awaiting Phase 2:

- <= 24 hours: raw `handle_measurements`
- <= 30 days: `topic_latency_hourly` + partial-bucket union
- > 30 days: `topic_latency_daily` + partial-bucket union

### Migration Path

Not applicable -- Chronicle is a new package within the monorepo.

### Timeline

- **Phase 1 (COMPLETED):** Core ingest pipeline + data model. `POST /runs`,
  `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/raw`, `GET /tenants`,
  `GET /health`. Bulk COPY, transaction handling, idempotency. Alembic
  migrations (initial schema + stats columns). Normalisation, detection
  service. Error handlers. Unit tests (property-based, fuzz), integration
  tests, e2e roundtrip tests against real TimescaleDB. Mutation testing.
- **Phase 2 (NEXT):** Query endpoints. `GET /topics`, `GET /topics/{topic}/latency`,
  `GET /topics/{topic}/runs`, `GET /anomalies`. Resolution service SQL
  implementation. Caching layer for read-path queries.
- **Phase 3:** Middleware stack (security headers, request size limit, rate
  limiting, request ID, GZip). Structured JSON logging. Connection pool
  monitoring. `models/hypertables.py` helper for compressed hypertable
  migrations.
- **Phase 4:** SSE streaming endpoint (`GET /stream`). BroadcastChannel
  wiring to route handler. `sse-starlette` integration. Reconnection replay
  via `Last-Event-ID`.
- **Phase 5:** React dashboard. Run Summary, Topic Drilldown, Regression
  Timeline, Anomaly Feed views.

---

## Validation

### Success Metrics

- **Anomaly detection unit tests run without a database.** Achieved: the
  `tests/unit/test_detection.py` and `tests/unit/test_detection_properties.py`
  files pass in-memory data to `DetectionService.detect()`. Suite runs in
  < 5 seconds without Docker.
- **Route handlers are under 25 lines each.** Achieved: the longest handler
  (`get_run`) is ~40 lines including ORM-to-schema mapping, but the business
  logic delegation is a single call.
- **Ingest pipeline steps are independently testable.** Achieved: normalisation,
  detection, and validation each have dedicated unit tests. Fuzz tests in
  `tests/integration/test_ingest_fuzz.py` verify no endpoint returns 500 for
  any Hypothesis-generated input.
- **Property-based tests validate core logic.** Achieved:
  `test_normalise_properties.py` and `test_detection_properties.py` use
  Hypothesis to exercise edge cases (empty scenarios, zero latencies, boundary
  sigma values).
- **Mutation testing covers services and API.** Achieved: `mutmut` configured
  to mutate `src/chronicle/services/` and `src/chronicle/api/` with tests from
  `tests/unit/` and `tests/integration/`.
- **E2e roundtrip tests against real TimescaleDB.** Achieved:
  `tests/e2e/test_ingest_roundtrip.py` ingests a report, queries it back, and
  verifies row counts and field values. Uses `chronicle_db` marker and skips
  when TimescaleDB is unavailable.
- **COPY participates in the ingest transaction.** Verified by e2e tests:
  a rollback after COPY results in no persisted handle rows.
- **SSE tenant isolation.** Unit-tested: subscribe two clients to different
  tenants, emit for tenant A, assert tenant B receives nothing.

### Monitoring

- **Response times.** To be added in Phase 3 (request ID middleware with
  duration logging).
- **Query timing.** To be added in Phase 3 (SQLAlchemy event hooks).
- **Connection pool saturation.** To be added in Phase 3 (pool event hooks).
- **SSE connection count.** `BroadcastChannel` exposes `len(self._clients)`
  for monitoring.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) -- Scoped registry pattern;
  Chronicle's tenant-scoped SSE follows the same isolation principle.
- [ADR-0006](0006-environment-boundary-enforcement.md) -- Environment boundary
  enforcement; Chronicle's allowlist-agnostic design mirrors this.
- [ADR-0012](0012-type-state-scenario-builder.md) -- Type-state builder pattern;
  the ingest pipeline's phased steps follow a similar progression.
- [ADR-0013](0013-matcher-strategy-pattern.md) -- Strategy pattern precedent
  within the monorepo. Chronicle uses the same pragmatic Protocol approach.
- [ADR-0014](0014-handle-result-model.md) -- Handle/Result model that
  Chronicle ingests and stores.

---

## References

- [PRD-009 -- Chronicle Reporting Server](../prd/PRD-009-chronicle-reporting-server.md)
- [test-report-v1 JSON Schema](../schemas/test-report-v1.json) -- the ingest
  contract
- [SQLAlchemy 2.0 Async Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) --
  session-per-request pattern, `async_sessionmaker`
- [FastAPI Dependency Injection](https://fastapi.tiangolo.com/tutorial/dependencies/) --
  `Depends()` for request-scoped resources
- [asyncpg COPY Support](https://magicstack.github.io/asyncpg/current/api/index.html#asyncpg.connection.Connection.copy_records_to_table) --
  bulk COPY protocol
- [sse-starlette](https://github.com/sysid/sse-starlette) -- SSE support for
  Starlette/FastAPI
- [Alembic Documentation](https://alembic.sqlalchemy.org/en/latest/) -- schema
  migrations for SQLAlchemy

---

## Notes

- **Open follow-up -- Protocol interfaces for repositories.** Option 2 as
  built does not use Protocol interfaces on repositories. If testing
  friction increases (e.g. services become hard to test because repository
  mocks are verbose), add Protocols for the repository interfaces. The same
  applies to `BroadcastChannel` -- when multi-worker SSE is needed, the
  channel's public interface (`subscribe`, `emit`, `shutdown`) should be
  stable enough for a `RedisBroadcastChannel` or `PgNotifyBroadcastChannel`
  to implement as a drop-in replacement. **Owner:** Chronicle maintainers.
- **Open follow-up -- async ingest.** PRD-009 Decision 21 chose synchronous
  ingest for v1. If bulk COPY throughput proves insufficient under continuous
  testing load, the ingest service can be refactored to enqueue a background
  task (via `asyncio.create_task` or a proper task queue). The service
  interface does not change; only the implementation behind `ingest()` does.
  **Owner:** Platform.
- **Open follow-up -- `TopicRepository` SQL implementation.** The three query
  methods (`list_topics`, `query_raw`, `query_aggregate`) raise
  `NotImplementedError`. These are Phase 2 -- the service and schema layers
  are ready; only the SQL needs writing. **Owner:** Chronicle maintainers.

**Last Updated:** 2026-04-19 (v3 -- post-implementation)
