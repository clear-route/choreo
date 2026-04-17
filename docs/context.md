# Choreo — Context for Implementation

## Purpose of this document

This document provides full context for contributors and coding agents working on
Choreo. It captures architecture, design decisions, DSL behaviour, and conventions.
A reader who has absorbed this document should be able to implement new features,
extend transport support, or add matcher types without needing further clarification
on intent.

---

## 1. What Choreo is

Choreo is an async Python test framework for event-driven systems. It lets you
write scenario tests that declare _"when I publish X, I expect Y to appear on the
wire"_ and handles the subscribe-before-publish ordering, correlation-based
dispatch, deadline enforcement, and structured failure reporting for you.

The framework is **transport-agnostic**. You construct a transport — in-memory
`MockTransport` for unit tests, `NatsTransport` for end-to-end, or your own
implementation for LBM / Kafka / RabbitMQ / MQTT / Redis / anything else — and
the same scenario DSL works against all of them. There is no test-framework-level
coupling to any particular messaging backend.

---

## 2. Why it exists

The two obvious alternatives for testing event-driven systems each carry a
significant cost.

**Mocking at the library level** means your tests are coupled to internal method
calls rather than observable wire behaviour. A refactor that preserves behaviour
breaks the tests. Worse, the mock is a simplified stand-in that does not exercise
the actual dispatch model — race conditions, ordering, and latency budgets are
invisible.

**Spinning up a real broker for every test** is slow, flaky, and adds a hard
infrastructure dependency to every developer's local setup. It also makes parallel
test execution fragile because messages from different concurrent tests can cross
over.

Choreo takes a third path. The unit suite runs entirely in-memory via
`MockTransport` — no network, no broker, no Docker. The in-memory transport
follows exactly the same five-method `Transport` Protocol as every real backend,
so the same test code runs unchanged against a live broker in the end-to-end
suite. Isolation between concurrently-running scenarios is guaranteed by
correlation IDs, not by topic isolation or locks.

---

## 3. Core concepts

### Harness

`core.Harness` is the session-scoped coordinator. Construct it with a transport
(and optionally a codec), call `await harness.connect()`, and it is ready. The
harness holds the transport, drives the codec for encoding and decoding payloads,
and acts as the factory for `Scenario` scopes. It does not know which backend the
transport wraps.

### Transport

A `Transport` is anything implementing the five-method `Transport` Protocol
defined in `core.transports.base`:

```python
async def connect(self) -> None: ...
async def disconnect(self) -> None: ...
def subscribe(self, topic: str, callback: TransportCallback) -> None: ...
def unsubscribe(self, topic: str, callback: TransportCallback) -> None: ...
def publish(self, topic: str, payload: bytes, *, on_sent: Optional[OnSent] = None) -> None: ...
```

All queue-specific concerns — allowlist enforcement, credential handling, socket
management, thread safety — live inside the transport. The harness sees only these
five methods.

### Scenario

A `Scenario` is a per-test scope, entered via `async with harness.scenario("name")
as s`. Opening a scope generates a fresh correlation ID (`TEST-{uuid4}`). The
scenario owns its expectations, reply handlers, and timeline. When the scope
exits, all subscriptions it registered are torn down.

Scenarios follow a strict linear state machine: **BUILDER → EXPECTING → TRIGGERED**.
`expect()` and `on()` are available in BUILDER and EXPECTING states. `publish()`
advances to TRIGGERED. `await_all()` is only available in TRIGGERED. Calling a
method in the wrong state raises `AttributeError` — illegal transitions are caught
at runtime, not at type-check time (ADR-0012).

### Handle

`expect()` returns a `Handle`. A handle is a future-backed result object that
resolves when a matching message arrives on the expected topic, or when the
deadline fires. Inspect it after `await_all()` returns:

| Attribute | Description |
|---|---|
| `outcome` | `PASS` / `FAIL` / `TIMEOUT` / `SLOW` / `PENDING` |
| `message` | decoded payload (raises if still `PENDING`) |
| `latency_ms` | elapsed time from expectation registration to match |
| `attempts` | count of messages that matched the correlation but failed the matcher |
| `last_rejection_reason` | prose from the most recent near-miss |
| `was_fulfilled()` | `True` iff outcome is `PASS` |

A `TIMEOUT` with `attempts == 0` is a routing failure: nothing arrived. A
`TIMEOUT` with `attempts > 0` is a matcher failure: messages arrived but none
satisfied the predicate. The distinction drives different debugging strategies
and the test report surfaces it explicitly.

Declare a latency budget with `handle.within_ms(50)` after `expect()` and before
`publish()`. If the matcher accepts a message but the elapsed time exceeds the
budget, the outcome becomes `SLOW` (a distinct failure — the system-under-test
responded but too slowly).

### Matcher

A `Matcher` is any object implementing the `Matcher` Protocol: a `description`
string and a `match(payload) -> MatchResult` method. Built-ins live in
`core.matchers`:

- Flat field: `field_equals`, `field_in`, `field_gt`, `field_lt`, `field_exists`
- Shape: `contains_fields` — recursive subset match over dicts and lists
- Scalar: `eq`, `in_`, `gt`, `lt`
- Composition: `all_of`, `any_of`, `not_`
- Escape hatch: `payload_contains` — substring check on raw bytes

---

## 4. How a test reads

```python
from pathlib import Path

import pytest_asyncio
from core import Harness
from core.transports import MockTransport
from core.matchers import contains_fields, field_equals, gt


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness():
    transport = MockTransport(allowlist_path=Path("config/allowlist.yaml"))
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


# E-commerce: item reserved → stock-level message published
async def test_reserving_an_item_should_publish_a_stock_update(harness):
    async with harness.scenario("reserve item") as s:
        h = s.expect("inventory.stock_level", contains_fields({
            "item_id": "SKU-001",
            "reserved": gt(0),
        }))
        s.publish("inventory.reserve", {"item_id": "SKU-001", "qty": 5})
        result = await s.await_all(timeout_ms=500)
    result.assert_passed()
```

The pattern is always: `expect` (subscribe), then `publish` (trigger), then
`await_all` (collect). The framework enforces this sequence at runtime.
`result.assert_passed()` is the canonical assertion — its message distinguishes
a silent timeout from a near-miss.

### Fan-out

One published message may cause the system-under-test to publish to several
topics. Register multiple expectations before `publish()`:

```python
# IoT: a sensor reading triggers both a data-store write and an alert
async def test_a_threshold_breach_should_store_the_reading_and_raise_an_alert(harness):
    async with harness.scenario("threshold breach") as s:
        s.expect("readings.stored", field_equals("sensor_id", "TEMP-42"))
        s.expect("alerts.raised", contains_fields({"severity": "HIGH"}))
        s.publish("readings.inbound", {"sensor_id": "TEMP-42", "value": 98.6})
        result = await s.await_all(timeout_ms=500)
    result.assert_passed()
```

### Staging a fake upstream service with replies

`on(trigger_topic).publish(reply_topic, builder)` registers a reply handler: when
a message matching the correlation arrives on `trigger_topic`, the framework
publishes `builder(received_payload)` on `reply_topic`. This lets a test stand in
for an upstream dependency without a second process:

```python
# Saga: payment service approves a payment when it sees a payment request
async def test_an_approved_payment_should_advance_the_saga(harness):
    async with harness.scenario("payment approved") as s:
        h = s.expect("saga.payment_confirmed", field_equals("saga_id", "S-99"))

        # Fake payment service: receive the request, reply with approval
        s.on("payments.request").publish(
            "payments.response",
            lambda msg: {"saga_id": msg["saga_id"], "status": "APPROVED"},
        )

        s.publish("payments.request", {"saga_id": "S-99", "amount": 49.99})
        result = await s.await_all(timeout_ms=500)
    result.assert_passed()
```

Reply rules:

- Must be registered before `publish()` — it is a pre-trigger arrangement.
- Fires once per scope. Calling `.publish()` a second time on the same
  `ReplyChain` raises `ReplyAlreadyBoundError`.
- The `payload` can be a static `dict`, static `bytes`, or a callable
  `Callable[[decoded_trigger], dict | bytes]`.
- Builder exceptions are captured as the exception class name only — never
  `str(e)` — so payload-derived error messages cannot leak into the report
  (ADR-0017).

---

## 5. Transports

### `MockTransport`

In-memory, no network. Subscriber callbacks fire synchronously inside `publish()`.
Optionally validates a configured endpoint against an allowlist at connect time.

```python
from pathlib import Path
from core.transports import MockTransport

transport = MockTransport(
    allowlist_path=Path("config/allowlist.yaml"),   # optional
    lbm_resolver="lbmrd:15380",                     # optional; validated against allowlist
)
```

Use in every unit test. Speed is the goal — there is no scheduler, no socket,
no I/O. `transport.sent()` and `transport.active_subscription_count()` expose
diagnostic views for tests that inspect the transport itself.

### `NatsTransport`

Talks to a real NATS broker. Lazy-imported; `nats-py` is only pulled in when you
construct one (`pip install 'core[nats]'`). Good for exercising the Transport
Protocol against a real network without standing up production infrastructure.

```python
from pathlib import Path
from core.transports import NatsTransport

transport = NatsTransport(
    servers=["nats://localhost:4222"],
    allowlist_path=Path("config/allowlist.yaml"),
    name="my-suite",
    connect_timeout_s=5.0,
)
```

Validates every entry in `servers` against the `nats_servers` category in the
allowlist. If `nats-py` is not installed, `connect()` raises `TransportError`
with an install hint.

### Writing your own transport

Implement the five-method `Transport` Protocol. That is all the harness needs.
Your `connect()` is the right place for allowlist enforcement, credential
handling, and socket setup. `disconnect()` tears it all down. `publish()` accepts
an optional `on_sent` callback that you must invoke on the asyncio loop thread
at the post-wire moment — scenarios use this to timestamp PUBLISHED events
accurately for the Jaeger-style waterfall.

Thread-safety: if your backend delivers messages on its own thread (LBM, AMQP
client libraries, …) you must hop them onto the asyncio loop before calling the
subscriber callbacks. Use `loop.call_soon_threadsafe` for this. The pattern is
captured in `core.transports.base` as `LoopPoster`. Without it, race conditions
in callback delivery are likely, and they are the kind that vanish when you add
a `print()`.

See `packages/core/src/core/transports/mock.py` for the synchronous pattern and
`packages/core/src/core/transports/nats.py` for the asyncio-native pattern.
Planned and contributed transports for LBM / Ultra Messaging, Kafka, RabbitMQ,
Redis, and MQTT follow the same shape — the Harness never sees the difference.

---

## 6. Allowlist model

The allowlist is a flat YAML file that controls which endpoints a transport is
permitted to connect to. It is a deployment-environment boundary: a test suite
connecting to the wrong environment is a real operational risk, and the allowlist
makes that impossible at connect time rather than relying on developer discipline.

```yaml
# config/allowlist.yaml
lbm_resolvers: ["lbmrd:15380", "localhost:15380"]
nats_servers:  ["nats://localhost:4222"]
kafka_brokers: ["localhost:9092"]
```

Categories are transport-defined. A transport calls `allowlist.get("nats_servers")`
and validates its configured servers against the returned tuple. Unknown categories
return an empty tuple; a transport that calls `get()` for an unrecognised category
simply gets nothing permitted. A file can cover multiple transports — they each
validate only the categories they care about.

`core.environment` provides `load_allowlist(path)` and the exception hierarchy
`HostNotInAllowlist` / `AllowlistConfigError`. Every built-in transport calls
`load_allowlist` in `connect()` when `allowlist_path` is supplied.

**Production endpoints must not appear in any checked-in allowlist** (ADR-0006).
The correlation ID prefix `TEST-` stamps every scenario-originated publish so
downstream systems can recognise and filter test traffic at their own boundary,
independently of which allowlist is in play.

---

## 7. Concurrency model

Choreo runs on a single `asyncio` event loop, pinned at session scope by
`pytest-asyncio` (ADR-0005). All matcher callbacks, reply handlers, and timeline
recordings execute on the loop thread. Scenarios run concurrently with
`pytest-xdist` (`-n auto` by default in `addopts`); isolation is by correlation
ID, not by topic.

When a message arrives on a topic shared by multiple live scenarios, the
dispatcher routes it by matching the `correlation_id` field in the decoded
payload against the correlation IDs held by active expectations. A message
whose correlation ID matches no live scope goes to the surprise log (metadata
only; payload redacted per ADR-0009).

Scenarios inject their correlation ID automatically when `publish()` receives a
`dict` without an explicit `correlation_id` key. The ID is always `TEST-`-prefixed
(a `CorrelationIdNotInTestNamespaceError` is raised if the caller provides one
that is not). This prefix enforcement exists so downstream systems can enforce a
boundary: any message carrying a non-`TEST-` correlation ID is assumed to be
production traffic; any message carrying a `TEST-` prefix can be filtered or
rejected at the consumer's edge (ADR-0006).

Network backends (LBM, AMQP libraries, and other non-asyncio-native clients)
deliver messages on their own threads. These must not call asyncio objects
directly. The loop-poster pattern (`loop.call_soon_threadsafe`) hops inbound
messages onto the loop thread before any callback sees them.

Timeline entries are bounded at 256 per scope to prevent runaway scenarios from
pinning memory. Near-miss `MatchFailure` records are capped at 20 per handle for
the same reason.

---

## 8. Writing style for code and docs

- Warm, direct, honest. Evidence before conclusion.
- Short sentences at impact. Vary length deliberately.
- No em dashes in code comments or docstrings.
- UK English spelling throughout.
- Plain verbs. Never use: leverage (verb), seamless, robust as vague praise,
  game-changing, transformative, well-understood, easy to X.
- Comments explain why, not what. Code should be self-documenting for what.

---

## 9. Related docs

- [docs/framework-design.md](framework-design.md) — architecture overview,
  operating modes, and the package layout
- [docs/guides/matchers.md](guides/matchers.md) — full matcher cookbook with
  examples across multiple domains
- [docs/adr/](adr/) — architectural decision records; numbered sequentially.
  Key decisions: ADR-0001 (single session harness), ADR-0006 (environment
  boundary), ADR-0012 (type-state scenario builder), ADR-0013 (matcher
  strategy), ADR-0014 (handle result model), ADR-0015 (deadline timeouts),
  ADR-0016 (reply lifecycle), ADR-0017 (reply reporting and secrets)
- [docs/prd/](prd/) — product requirements documents that drove each feature
  increment
