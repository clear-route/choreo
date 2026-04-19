# Project conventions — Choreo

This file captures project-specific conventions enforced in code review. Global style ([docs/context.md](docs/context.md) §15) still applies on top: UK English, no em-dashes in code, banned weasel words (`leverage`, `seamless`, `robust` as praise, `well-understood`, `easy to X`).

## Test style

### Names: behaviour over implementation, "should" / "should not"

Every test name describes an observable behaviour of the system from a caller's point of view, using **"should"** or **"should not"**.

Good:

- `test_a_newly_constructed_bus_should_be_disconnected`
- `test_transport_should_refuse_to_connect_with_an_endpoint_outside_the_allowlist`
- `test_publishing_should_deliver_the_payload_to_every_subscribed_callback`
- `test_production_environment_names_should_not_be_valid`

Bad (implementation / attribute-focused):

- `test_bus_is_disconnected_by_default` — doesn't read as a behaviour
- `test_environment_enum_has_expected_values` — tests literal enum values; implementation detail
- `test_dispatch_method_cannot_be_monkey_patched_undetected` — asserts on internal sentinel; what's the observable behaviour?

### Assertions: observable effect, not internal state

Tests should assert on what a caller can see — return values, published messages, raised exceptions, log entries, Future resolutions. Avoid asserting on private attributes (`obj._foo`) or implementation sentinels.

If the only way to verify a behaviour is to peek at internals, the behaviour probably isn't really a behaviour — or the API is missing a public way to observe it.

### One behaviour per test

A test asserts one thing. "Transport connects and disconnects idempotently" is two behaviours — split them. Shared setup goes in fixtures; shared shape goes in parametrisation.

## Runtime configuration

### The library is a pure tool

`choreo.Harness` is a transport-agnostic coordinator. The **transport** owns its
own config, its own connection logic, and its own allowlist enforcement. The
library doesn't know or care whether you're talking to NATS, Kafka, RabbitMQ,
Redis, or a mock.

### Library surface

```python
from pathlib import Path
from choreo import Harness
from choreo.transports import MockTransport, NatsTransport
from choreo.codecs import JSONCodec                         # default; or RawCodec, or yours

transport = MockTransport(
    allowlist_path=Path("config/allowlist.yaml"),
    endpoint="mock://localhost",
)
harness = Harness(transport)                 # codec defaults to JSON
await harness.connect()
```

Each transport implementation defines its own constructor fields. Built-ins
today: `MockTransport` (in-memory, with optional allowlist enforcement),
`NatsTransport` (`servers=[...]`, validates the `nats_servers` allowlist
category), `KafkaTransport` (`bootstrap_servers=[...]`, validates
`kafka_brokers`), `RabbitTransport` (`url`, validates `amqp_brokers`) and
`RedisTransport` (`url`, validates `redis_servers`). All are lazy-imported
so the corresponding extras are only required when you actually use them.
The Harness never sees these; it just delegates `connect()` / `subscribe()` /
`publish()`.

### Adding a new queue backend

Write a module under `choreo/transports/` implementing the five-method `Transport`
Protocol. Your `connect()` is responsible for any allowlist enforcement,
credential handling, and socket opening specific to that backend. Follow the
pattern in [packages/core/src/choreo/transports/mock.py](packages/core/src/choreo/transports/mock.py).

### Allowlist files

One YAML file per deployment. Flat mapping; categories are transport-defined:

```yaml
# Categories a NATS + Kafka deployment cares about:
nats_servers:  [...]
kafka_brokers: [...]

# A RabbitMQ deployment would have a different category:
# amqp_brokers: [...]
```

The library's `Allowlist.get(category)` returns an empty tuple for unknown
categories, so one file can cover several transports. Each transport validates
only the categories it cares about.

The repo ships [config/allowlist.yaml](config/allowlist.yaml) for the framework's
own tests. Consumers ship their own allowlist file(s) — one per deployment
they support. ADR-0006 explains why production endpoints never appear in any
checked-in allowlist.

### Production config does not live in test files

Production hosts do not appear in any repo allowlist, by design.
Never inline allowlist content into `conftest.py` or test fixtures. Tests
that need malformed YAML for negative-path coverage create a tmp file in the
test body.

Negative test pattern (fine):

```python
def test_an_allowlist_missing_required_lists_should_be_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("dev: {}")
    with pytest.raises(AllowlistConfigError):
        load_allowlist(bad)
```

Fixture with inlined production values (wrong):

```python
# DON'T DO THIS
@pytest.fixture
def allowlist_yaml_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("env") / "environments.yaml"
    path.write_text("""
dev:
  nats_servers: ["nats://prod.example.com:4222"]
  ...
""")
    return path
```

## Downstream consumer usage

The `choreo` package is designed to be installed by separate repos that test
their own services. Those repos build their own pytest fixture around the
Harness — the library itself does not ship fixtures or read env vars.

### Canonical consumer pattern

```python
# In the consumer repo's conftest.py
import os
from pathlib import Path

import pytest_asyncio

from choreo import Harness
from choreo.transports import MockTransport     # or NatsTransport / KafkaTransport / ...


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness():
    # The consumer chooses which transport and where its config comes from.
    # Env vars, TOML, vault, hardcoded — library has no opinion.
    transport = MockTransport(
        allowlist_path=Path(os.environ.get("MY_APP_ALLOWLIST", "config/allowlist.yaml")),
        endpoint=os.environ.get("MY_APP_MOCK_ENDPOINT", "mock://localhost"),
    )
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


# In the consumer's tests
async def test_a_created_event_should_produce_a_state_change(harness):
    async with harness.scenario("state_change") as s:
        s.expect("state.changed", contains_fields({"count": 1000}))
        s = s.publish("events.created", event_fixture)
        result = await s.await_all(timeout_ms=500)
        result.assert_passed()
```

The library has no opinion about which transport you use, what you name your
env vars, where you keep your allowlist files, or how you decide which
allowlist applies. Those are deployment concerns.

---

## Scenario DSL

`expect()` returns a `Handle` (ADR-0014). After `await_all()` the handle
exposes:

- `handle.was_fulfilled()` — True if the matcher accepted a message in time
  and within any declared budget.
- `handle.message` — decoded payload. Raises if the handle is still `PENDING`.
- `handle.latency_ms` — elapsed time from expect registration to match.
- `handle.attempts` — count of routed messages the matcher rejected. Zero
  means nothing arrived; non-zero means routing worked but the matcher said no.
- `handle.reason` — diagnostic string once resolved.
- `handle.within_ms(budget_ms)` — declare a latency budget before `publish()`.
  Matched-but-over-budget resolves as `Outcome.SLOW`.

Matchers live in `choreo.matchers`: `eq`, `in_`, `gt`, `lt`, `field_equals`,
`field_in`, `field_gt`, `field_lt`, `field_exists`, `payload_contains`,
`all_of`, `any_of`, `not_`, and `contains_fields` (the shape-matcher used in
the canonical consumer example above).

---

## End-to-end tests (real broker)

The unit suite runs entirely in-memory via `MockTransport`. A separate e2e
suite exercises the Transport Protocol against real networks by pointing
`NatsTransport`, `KafkaTransport`, `RabbitTransport`, or `RedisTransport`
at disposable brokers under Docker Compose. This validates the Transport
Protocol contract itself — not any production wire, just the contract that
a consumer's own transport implementation would also need to honour.

### Running e2e locally

```bash
# 1. Install the NATS extra
pip install -e 'packages/core[nats]'

# 2. Bring up the broker (port 4222)
docker compose -f docker/compose.e2e.yaml up -d

# 3. Run just the e2e suite
pytest -m e2e

# 4. Tear the broker down
docker compose -f docker/compose.e2e.yaml down
```

The default `pytest` invocation runs everything bar e2e. CI can gate the
e2e job behind `pytest -m e2e` with the compose stack brought up as a step.

### Skip behaviour

If `nats-py` is not installed, or if no broker is reachable at `NATS_URL`
(default `nats://localhost:4222`), the e2e tests **skip** rather than fail.
This keeps local dev green when the dependency is absent; CI should treat
a skip as a signal that the compose stack did not come up.

Override the broker URL with the `NATS_URL` env var if you're pointing at
a non-default NATS instance — remember to add the URL to the allowlist's
`nats_servers` category first, or `connect()` will refuse.

### Writing a new e2e test

- Put the file under `tests/e2e/`.
- Decorate the module with `pytestmark = pytest.mark.e2e`.
- Depend on `nats_url` and `_nats_available` fixtures from `tests/e2e/conftest.py`.
- Use `_unique_topic(prefix)` for subject names so concurrent runs don't collide.
- Use the Scenario DSL exactly as you would with `MockTransport` — the whole
  point of e2e is that the surface is identical.

---

---

## Chronicle — Reporting Server

Chronicle is a FastAPI reporting server in `packages/chronicle/` that ingests
Choreo `test-report-v1` JSON, stores it in TimescaleDB, runs anomaly detection,
and exposes a dashboard. See [PRD-009](docs/prd/PRD-009-chronicle-reporting-server.md)
and [ADR-0021](docs/adr/0021-chronicle-api-structure.md) for design documentation.

### Architecture: Repository + Service Layer

Three layers with strict import direction (ADR-0021):

```
api/           → services/       → repositories/     → models/
(thin routes)    (business logic)   (database access)   (ORM tables)
     ↓               ↓                   ↓
schemas/        normalise.py         SQLAlchemy 2.0
(Pydantic)      (shared types)       asyncpg COPY
```

**Rules:**
- Route handlers never import `sqlalchemy`. They call services or repositories
  via `Depends()`.
- Services raise domain exceptions (`ReportValidationError`), not
  `HTTPException`. Middleware translates to HTTP status codes.
- Repositories return ORM objects or `NamedTuple`/dataclass rows, not Pydantic
  schemas. Mapping to response schemas happens in the route handler.
- `services/normalise.py` is the shared types module — `NormalisedReport`,
  `NormalisedHandle`, `NormalisedScenario`, `NewAnomaly` live here to avoid
  circular imports between services and repositories.

### Ingest pipeline

`POST /api/v1/runs` flows through `IngestService.ingest()`:

1. Validate schema version (Pydantic level 1).
2. Validate against `test-report-v1` JSON Schema (level 2).
3. Normalise — `normalise_report()` extracts fields, derives `over_budget`
   from `diagnosis.kind`, flattens test→scenario→handle hierarchy.
4. Persist in a single transaction — `upsert_tenant()`, `create_run()`,
   `bulk_insert_scenarios()`, `copy_handle_measurements()` (asyncpg COPY).
5. Detect anomalies in a separate session — baseline data fetched from DB,
   passed into pure `DetectionService.detect()`. Detection failure never
   fails the ingest.
6. Broadcast SSE events — `run.completed` + any `anomaly.detected`, scoped
   by tenant.

Idempotency via `Idempotency-Key` header. Duplicate key returns existing
`run_id` with `duplicate: true`. Uses optimistic concurrency (catch
`IntegrityError`, re-query with fresh session).

### Configuration (12-factor)

All config via environment variables with `CHRONICLE_` prefix. `DATABASE_URL`
is the exception (accepts both prefixed and unprefixed via `AliasChoices`).

Priority order: constructor kwargs > env vars > `/run/secrets/*` > `.env` file > defaults.

```python
from chronicle.config import Settings
settings = Settings(environment="production", database_url="...")
settings.validate_production_config()  # fails fast if using dev defaults
```

`validate_production_config()` is called during lifespan startup. It rejects
`localhost` in `DATABASE_URL` and `text` in `log_format` when `environment`
is `staging` or `production`.

### Running Chronicle locally

```bash
# 1. Start TimescaleDB
docker compose -f docker/compose.chronicle.yaml up -d

# 2. Install the package
pip install -e 'packages/chronicle[test]'

# 3. Run migrations
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle migrate

# 4. Start the dev server (hot reload)
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle

# 5. Ingest a test report
curl -X POST http://localhost:5173/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-Chronicle-Tenant: my-team" \
  -d @packages/core/test-report/results.json
```

API docs at `http://localhost:8000/api/v1/docs`.

### Test structure

```
tests/
├── conftest.py              # Shared fixtures, DB_URL, teardown
├── factories.py             # make_report(), make_scenario(), make_handle()
├── unit/                    # Pure logic — no HTTP, no mocks, no DB
│   ├── test_normalise.py
│   ├── test_normalise_properties.py   # Hypothesis property-based
│   ├── test_detection.py
│   ├── test_detection_properties.py   # Hypothesis property-based
│   ├── test_late_reports.py
│   └── test_config.py
├── integration/             # TestClient with mocked services — no DB
│   ├── test_health.py
│   ├── test_ingest.py
│   └── test_ingest_fuzz.py           # Hypothesis fuzz testing
└── e2e/                     # Real TimescaleDB (chronicle_db marker)
    ├── test_ingest_db.py
    ├── test_ingest_roundtrip.py
    └── test_late_reports.py
```

Run commands:

```bash
pytest packages/chronicle/tests/                    # unit + integration
pytest packages/chronicle/tests/unit/               # unit only
pytest packages/chronicle/tests/integration/        # integration only
pytest packages/chronicle/tests/ -m chronicle_db    # e2e (needs TimescaleDB)
```

Tests use `factories.py` for building reports — never inline report dicts in
test files. DB tests truncate all tables after each test via the `db_client`
fixture.

### Chronicle test conventions

All conventions from the core library apply (§Test style above), plus:

- **Use `factories.make_report()`** for test data. Override only the fields
  the test cares about. Never duplicate report structures across files.
- **Shared fixtures in `conftest.py`** — `settings`, `client`, `db_client`,
  `real_report`, `skip_no_db`. Never redefine these in individual test files.
- **DB tests use the `chronicle_db` marker** and are excluded by default.
  They skip gracefully when TimescaleDB is not reachable.
- **Property-based tests** use Hypothesis with
  `suppress_health_check=[HealthCheck.differing_executors]` for mutmut
  compatibility.
- **Fuzz tests** in `integration/test_ingest_fuzz.py` verify no endpoint
  returns 500 for any generated input.

### Adding a new Chronicle endpoint

1. Add the route handler in `api/<resource>.py` (thin — validate, delegate, return).
2. Add Pydantic schemas in `schemas/<resource>.py` with `from_attributes = True`.
3. Add repository methods in `repositories/<resource>_repo.py`.
4. Add a DI provider in `dependencies.py` if a new service is needed.
5. Register the router in `app.py`.
6. Write tests: unit (logic), integration (mocked HTTP), e2e (real DB).

### Database

- **TimescaleDB** (`timescale/timescaledb-ha:pg18`) on port 5433 for local dev.
- **Alembic** for migrations. TimescaleDB-specific DDL (hypertables, continuous
  aggregates, compression) via explicit `op.execute()` in migration scripts.
  `env.py` overrides `sqlalchemy.url` from `DATABASE_URL` env var.
- **COPY protocol** for bulk handle measurement inserts (asyncpg
  `copy_records_to_table`). Column list in `HANDLE_COPY_COLUMNS` constant.

---

## Related docs

- [docs/context.md](docs/context.md) §15 — global writing style rules
- [docs/framework-design.md](docs/framework-design.md) — architecture overview
- [docs/adr/](docs/adr/) — architectural decisions
- [docs/adr/0021-chronicle-api-structure.md](docs/adr/0021-chronicle-api-structure.md) — Chronicle API architecture
- [docs/prd/](docs/prd/) — product requirements
- [docs/prd/PRD-009-chronicle-reporting-server.md](docs/prd/PRD-009-chronicle-reporting-server.md) — Chronicle PRD
