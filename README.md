<p align="center">
  <img src="docs/logo.png" alt="Choreo" width="200">
</p>

<h1 align="center">Choreo</h1>

<p align="center">
  <strong>Python 3.11+</strong>&ensp;|&ensp;<strong>Apache 2.0</strong>&ensp;|&ensp;<a href="https://github.com/clear-route/choreo/actions/workflows/ci.yml">CI</a>&ensp;|&ensp;<a href="https://clear-route.github.io/choreo/">Live Test Report</a>
</p>

<p align="center"><em>Distributed systems need a choreographer.</em></p>

---

Choreo is an async Python test framework for distributed, message-driven systems. You declare the messages you expect, publish the ones that trigger them, and the harness handles the routing, correlation, timing, matching, and reporting. When a test fails, the report tells you *which* hop broke and *why*.

Supporting natively: **NATS**, **Kafka**, **RabbitMQ**, **Redis**. 

---

## When to use Choreo

Use Choreo when your system is message-driven and your tests need to assert on what comes back over the wire: event pipelines, pub/sub fan-out, request/reply services, sagas, workflow orchestrators, IoT telemetry, CQRS read models.

**Not a good fit for:** pure unit tests with no I/O, HTTP request/response testing (use `httpx` + `respx`), or synchronous Python-only fakes.

Three things Choreo does that you will not find combined in any other Python test framework today.

### Near-miss diagnostics: did the message arrive, or was it wrong?

When a scenario times out, the harness has been counting. How many messages landed on the topic, how many passed the matcher, and what the last mismatch looked like.

```
TIMEOUT on 'order.settled': 0 near-misses
```

Zero near-misses means nothing arrived. You have a routing problem. Wrong topic, wrong subscription, wrong correlation ID.

```
TIMEOUT on 'order.settled': 3 near-misses
  last mismatch: status was "PENDING", expected "SETTLED"
  last payload: {"order_id": "ORD-42", "status": "PENDING", ...}
```

Three near-misses means the message arrived and every one failed the matcher. Routing works. The service (or the test) has the wrong shape.

One diagnostic, two very different fixes. No more staring at log files guessing which step got missed.

### Latency is an assertion, not a timeout

Every other async test framework treats latency as noise. You pick a generous timeout, cross your fingers, and a breach fails the test for reasons you cannot read.

```python
h = s.expect("order.settled", field_equals("status", "SETTLED"))
h.within_ms(50)
```

- Match inside the budget: `PASS`
- Match outside the budget, inside the outer timeout: `SLOW` (distinct from `FAIL`)
- Miss the outer timeout entirely: `FAIL`

Three outcomes, not two. You can surface latency regressions in trend reports without blocking CI, or promote `SLOW` to a build breaker once you are ready. Your SLA is in the test.

### One DSL, any boundary

The queue *is* the natural test boundary. What matters is what lands on it. Because the DSL sits above the transport, the same test can cover one service in isolation, two services composed, or a graph of services running together.

```python
# Test one service. The test doesn't care what else is in the system.
async with harness.scenario("single") as s:
    s.expect("orders.processed", field_equals("status", "COMPLETED"))
    s = s.publish("orders.created", {"item_id": "ITEM-42"})
    result = await s.await_all(timeout_ms=500)

result.assert_passed()
```

Need to stand a mannequin in for a dependency you do not want to boot? Register a reply:

```python
s.on("fraud.check").publish(
    "fraud.result",
    lambda msg: {"correlation_id": msg["correlation_id"], "decision": "APPROVE"},
)
```

Want the real upstream in the test instead? Drop the reply, let the service run against the broker, and widen the expectation. The scenario does not care how many dancers are on the floor; it cares about the cues.

### Multi-transport bridges (Stage)

Some services exist to translate between transports: an HTTP listener that pushes events onto Kafka; a gateway that consumes NATS requests and republishes them as RabbitMQ messages; a router that bridges a low-latency NATS edge to a durable Kafka pipeline. The natural test boundary spans both transports.

`Stage` is a coordinator that wraps a named registry of harnesses. One scenario can publish on transport A, register a reactive reply on transport B, and assert on transport A again, in a single deadline-bounded block.

```python
from choreo import Stage, Harness, MappedBridge
from choreo.transports import NatsTransport, KafkaTransport
from choreo.correlation import DictFieldPolicy

stage = Stage(
    harnesses={
        "nats":  Harness(NatsTransport(...),  correlation=DictFieldPolicy()),
        "kafka": Harness(KafkaTransport(...), correlation=DictFieldPolicy()),
    },
    bridge=MappedBridge(forwards={
        "nats":  lambda logical: f"nats-{logical}",
        "kafka": lambda logical: f"kafka-{logical}",
    }),
)

async with stage.scenario("nats-kafka-round-trip") as s:
    s.on("orders.new", on="kafka").publish(
        "orders.processed", on="nats",
        build=lambda trigger: {"forwarded": trigger["payload"]},
    )
    h = s.expect("results", field_equals("kind", "result"), on="nats")
    s.publish("orders.new", {"payload": 42}, on="kafka")
    result = await s.await_all(timeout_ms=100)
```

The full bridge round-trip in 13 lines of scenario body. Each transport keeps its own codec and correlation policy; a `CorrelationBridge` translates per-scope correlation across the wire. Cross-scope traffic between concurrent test scopes is filtered out by construction so 100 scopes can run against shared brokers without cross-talk.

---

## Install

```bash
pip install choreo-harness

# Add the broker extra(s) you need
pip install 'choreo-harness[nats]'      # NATS
pip install 'choreo-harness[kafka]'     # Kafka
pip install 'choreo-harness[rabbitmq]'  # RabbitMQ
pip install 'choreo-harness[redis]'     # Redis

# Optional: pytest reporter plugin (HTML + JSON output)
pip install choreo-reporter

# Optional: longitudinal analytics server (TimescaleDB + React dashboard)
pip install choreo-chronicle
```

- Python 3.11+
- No runtime dependencies (`pytest`, `pytest-asyncio`, and `pyyaml` are test extras only)
- Client libraries (`nats-py`, `aiokafka`, `aio-pika`, `redis`) ship as optional extras per transport and are lazy-imported
- Both packages ship `py.typed` for full mypy coverage

---

## Quickstart

Zero to a passing scenario against a real NATS broker in three steps. NATS is the lightest broker to boot locally (one container, small image), so it makes the easiest first touch.

### 1. Bring up the broker

The repo ships a `docker compose` profile for this.

```bash
docker compose -f docker/compose.e2e.yaml --profile nats up -d
```

### 2. Allow the endpoint

Choreo refuses to connect to anything that is not on an explicit allowlist. This is a safety guard

Create `config/allowlist.yaml`:

```yaml
nats_servers: ["nats://localhost:4222"]
```

### 3. Write and run a scenario

Create `tests/test_happy_path.py`:

```python
from pathlib import Path

from choreo import Harness
from choreo.transports import NatsTransport
from choreo.matchers import contains_fields, gt


async def test_a_created_order_should_be_processed_with_a_positive_count():
    transport = NatsTransport(
        servers=["nats://localhost:4222"],
        allowlist_path=Path("config/allowlist.yaml"),
    )
    harness = Harness(transport)
    await harness.connect()

    async with harness.scenario("happy") as s:
        s.expect("orders.processed", contains_fields({
            "status": "COMPLETED",
            "order": {"item_id": "ITEM-42", "count": gt(0)},
        }))
        s = s.publish("orders.created", {"item_id": "ITEM-42", "count": 1000})
        result = await s.await_all(timeout_ms=500)

    result.assert_passed()
    await harness.disconnect()
```

Run it:

```bash
pytest tests/
```

That is the whole loop. `pytest-asyncio` runs in `auto` mode with a session-scoped event loop, so `async def` tests need no decorator. Swap `NatsTransport` for `KafkaTransport`, `RabbitTransport`, or `RedisTransport` and nothing above the transport constructor changes.

For real consumer repos, wrap `Harness` in a session-scoped `pytest_asyncio` fixture. See [Downstream consumer pattern](#downstream-consumer-pattern) below.

---

## Examples

Self-contained, runnable projects in [examples/](examples/):

- **[examples/01-hello-world/](examples/01-hello-world/)**: the minimum useful test. Publish, expect, assert.
- **[examples/02-request-reply/](examples/02-request-reply/)**: stage a fake upstream service inside the test with `on(trigger).publish(reply)`.
- **[examples/03-parallel-isolation/](examples/03-parallel-isolation/)**: opt into a `CorrelationPolicy` so parallel scenarios don't cross-match.
- **[examples/04-transport-auth/](examples/04-transport-auth/)**: wire a typed `auth=` descriptor into a transport, see credential lifecycle and redaction in action.
- **[examples/05-auth-resolver/](examples/05-auth-resolver/)**: fetch credentials at `connect()` time via sync/async resolvers (env vars, Vault, Secrets Manager pattern).
- **[examples/06-multi-transport-bridge/](examples/06-multi-transport-bridge/)**: testing a service that bridges two transports with `Stage` — a vendor-protocol adapter into the energy domain, a vendor → domain → vendor round-trip, and a dispatch orchestrator fanning out commands to multiple connected devices.

Each example ships its own `README.md` explaining what it shows. Run any of them with `pytest examples/<dir>/`.

---

Four dancers on the floor:

**Harness**: the session-scoped coordinator. You construct one with a Transport and call `connect()`. The transport runs its allowlist check, opens its socket, and reports ready. When the suite ends, `disconnect()` tears everything down.

**Scenario**: the per-test scope. Opening one owns its expectations, replies, and timeline, and cleans them up on exit (normal or exception). Per-scope correlation isolation is opt-in via a `CorrelationPolicy`: the library default is transparent passthrough (`NoCorrelationPolicy`), so `s.publish(topic, payload)` sends `payload` unchanged. Consumers who want the legacy `TEST-`-prefixed stamping pass `correlation=test_namespace()` at Harness construction (ADR-0019).

**Dispatcher**: the router. Every inbound message lands here. It pulls the correlation ID out of the payload and hands the message to the scenario that claimed it. Unmatched messages go to a **surprise log** (metadata only; payloads not retained).

**Loop-poster**: the thread-safe bridge. Stateful client libraries or native-code backends that deliver messages on their own thread use the loop-poster's `loop.call_soon_threadsafe` hop to move those messages onto the asyncio loop before the dispatcher sees them. Without it, you'd get race conditions that vanish when you add a `print()`.

For scenarios that span two transports, `Stage` wraps multiple `Harness` instances behind one scope and adds a `CorrelationBridge` that translates the per-scope id across wires. See [Multi-transport bridges (Stage)](#multi-transport-bridges-stage) above.

---

## The Scenario DSL

A scenario moves through three states. Each state exposes only the methods
valid at that point; illegal transitions raise `AttributeError` at runtime.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> BUILDER
    BUILDER --> EXPECTING : expect() / on()
    EXPECTING --> TRIGGERED : publish()
    TRIGGERED --> ScenarioResult : await_all()
```

The signatures below are the single-`Harness` form. When the scenario runs under `Stage`, every method (`expect`, `publish`, `on`) takes an additional `on=<transport-name>` argument that selects which harness handles the call; omitting it raises `MissingTransportError`. See [Multi-transport bridges (Stage)](#multi-transport-bridges-stage).

### `expect(topic, matcher) → Handle`

Subscribes to `topic`, filters by the scenario's correlation ID, applies
`matcher` to the decoded payload. Returns a **Handle** you can hold for
post-hoc assertions.

```python
h = s.expect("events.processed", field_equals("status", "COMPLETED"))
h.within_ms(50)           # declare a latency SLA (optional)
```

After `await_all()` returns, the Handle exposes:

| Field | What it tells you |
|---|---|
| `outcome` | `PASS` / `FAIL` / `TIMEOUT` / `SLOW` / `PENDING` |
| `message` | decoded payload |
| `latency_ms` | elapsed time from registration to match |
| `attempts` | count of near-misses (messages on correlation that failed matcher) |
| `last_mismatch_reason` | prose from the most recent mismatch |
| `last_mismatch_payload` | decoded payload of that mismatch |
| `failures` | structured `MatchFailure` tree (capped at 20) |
| `was_fulfilled()` | True iff outcome is `PASS` |

Reading `message` or `latency_ms` while `outcome == PENDING` raises
`RuntimeError` (ADR-0014).

### `publish(topic, payload) → Scenario`

Sends a message through the transport. `payload` can be either raw `bytes`
(passed through untouched) or a `dict` (codec-encoded; defaults to JSON).

With the default `NoCorrelationPolicy`, the payload goes to the wire
unchanged, with no library-injected fields. To opt into per-scope correlation
isolation, configure a `CorrelationPolicy` at Harness construction; see
[Correlation policy](#correlation-policy) below.

### `await_all(timeout_ms) → ScenarioResult`

Waits for every registered handle to resolve or the deadline to fire.
Returns a `ScenarioResult`:

| Field / method | Purpose |
|---|---|
| `result.passed` | True iff every handle outcome is `PASS` |
| `result.assert_passed()` | raises `AssertionError` with breakdown on failure |
| `result.handles` | tuple of every handle |
| `result.failing_handles` | handles where `outcome != PASS` |
| `result.timeline` | chronological events: PUBLISHED / RECEIVED / MATCHED / MISMATCHED / DEADLINE / REPLIED / REPLY_FAILED |
| `result.replies` | per-`on()` reply lifecycle reports |
| `result.failure_summary()` | multi-line diagnostic including last 20 timeline entries |
| `result.reply_at(trigger_topic)` | lookup a reply by its trigger topic |

`assert_passed()` is the canonical assertion. Its message distinguishes a
*silent timeout* (nothing arrived, routing bug) from a *near-miss*
(messages arrived but failed the matcher, expectation bug).

---

## Replies: `on().publish()`

A scenario can register a **reply**: when a matching message arrives on
a trigger topic, publish a response. The framework ships the primitive;
consumers compose them into bundles for their own domains. Handy for
staging fake upstream services inside a test without standing up a second
process.

```python
async with harness.scenario("instant_reply") as s:
    h = s.expect(
        "reply.received",
        contains_fields({"status": "COMPLETED", "count": 100}),
    )

    s.on("request.sent").publish(
        "reply.received",
        lambda msg_received: {
            "correlation_id": msg_received["correlation_id"],
            "status": "COMPLETED",
            "count": msg_received["count"],
        },
    )

    s = s.publish("request.sent", {"correlation_id": "REQ-1", "count": 100})
    result = await s.await_all(timeout_ms=500)

result.assert_passed()

report = result.reply_at("request.sent")
assert report.state.name == "REPLIED"
assert report.match_count == 1
```

Rules:

- Must be registered **before** `publish()`. It is a pre-trigger
  arrangement, not a background subscription.
- Fires **once per scope**. The chain is single-use; calling `.publish()`
  a second time on the same `ReplyChain` raises `ReplyAlreadyBoundError`.
- The `matcher` argument is optional; `None` means *every inbound on the
  topic matches*.
- The `payload` can be a static `dict`, static `bytes`, or a callable
  `Callable[[decoded_trigger], dict | bytes]`.
- Failures in the builder function are captured as exception **class name
  only** in the report, never `str(e)`, so secrets from a failing builder
  never leak into the report (ADR-0017).

Each registered reply produces a `ReplyReport` in `result.replies`:

| Field | Meaning |
|---|---|
| `state` | `ARMED_NO_MATCH` / `ARMED_MATCHER_MISMATCHED` / `REPLIED` / `REPLY_FAILED` |
| `candidate_count` | messages that arrived on the trigger topic |
| `match_count` | how many passed the optional matcher |
| `reply_published` | whether the reply actually went out |
| `builder_error` | exception class name if the builder raised |

### Cross-transport replies

Under `Stage`, the trigger and the response can live on different transports. The same `on().publish()` chain takes an `on=<name>` argument on both ends:

```python
async with stage.scenario("bridge") as s:
    s.on("orders.new", on="kafka").publish(
        "orders.processed", on="nats",
        build=lambda trigger: {"forwarded": trigger["payload"]},
    )
```

The framework picks the trigger up on the Kafka harness, runs the builder, and emits the response on the NATS harness — correlation translation between the two is handled by the `CorrelationBridge` configured on the `Stage`. See [Multi-transport bridges (Stage)](#multi-transport-bridges-stage) and the worked example in [examples/06-multi-transport-bridge/](examples/06-multi-transport-bridge/).

---

## Correlation policy

A `CorrelationPolicy` decides how per-scope correlation ids are generated,
stamped onto outbound messages, and read from inbound ones. The library
ships three profiles:

```python
from choreo import Harness, NoCorrelationPolicy, DictFieldPolicy, test_namespace

#
Harness(transport)
Harness(transport, correlation=NoCorrelationPolicy())   
Harness(transport, correlation=DictFieldPolicy(field="trace_id"))
```

### When do I need one?

| Situation | Policy |
|---|---|
| Single scenario at a time against a dedicated broker | `NoCorrelationPolicy` (default) |
| Parallel scenarios sharing one broker connection | `DictFieldPolicy(field=...)` |
| Shared broker across tenants / CI runs / dev machines | `DictFieldPolicy(field=..., prefix=<run-unique>)` |
| Downstream systems filter test traffic on `TEST-` | `test_namespace()` |
| Correlation lives in a header, not the payload | Custom `CorrelationPolicy` |


A working demonstration of parallel isolation with and without a policy
lives in [examples/03-parallel-isolation/](examples/03-parallel-isolation/).

### When you also have a `CorrelationBridge`

`CorrelationPolicy` and `CorrelationBridge` solve different problems and
compose. The policy decides how a wire id is stamped onto and read from
a single message on a single transport. The bridge — only present in
`Stage` scenarios — translates a per-scope *logical* id into a different
*wire* id per transport, so the same scope can be filtered independently
on Kafka, NATS, and any other harness it owns.

In a multi-transport test you typically configure both: each harness
gets its own `DictFieldPolicy(field="correlation_id")` (or whatever
your schema uses), and the `Stage` gets a `MappedBridge` whose
`forwards` mapping mints a deterministic per-transport id from the
logical scope id. The policy then stamps the bridge's per-transport id
onto each outbound message and reads it back on inbound, keeping
concurrent scopes from cross-matching even on shared brokers.

See the [Stage user guide](docs/guides/stage.md) for the full
relationship and [examples/06-multi-transport-bridge/](examples/06-multi-transport-bridge/)
for a worked example.

---

## Matchers

Matchers live in `choreo.matchers`. They compose into expressive predicates
over decoded payloads. See [docs/guides/matchers.md](docs/guides/matchers.md)
for the full cookbook.

### Flat field matchers

```python
from choreo.matchers import (
    field_equals, field_ne, field_in, field_gt, field_lt,
    field_exists, field_matches,
)

s.expect("reading", field_equals("sensor_id", "SENSOR-01"))
s.expect("tick",    field_in("region", ("eu-west", "us-east", "ap-south")))
s.expect("result",  field_gt("count", 0))
s.expect("result",  field_lt("latency_ms", 1000.0))
s.expect("evt",     field_exists("trace_id"))
s.expect("evt",     field_ne("status", "REJECTED"))
s.expect("evt",     field_matches("order_id", r"^ORD-\d+$"))
```

Paths come in three forms:

- **Dotted string** - sugar for the common case. Numeric segments are
  treated as list indices, so `"items.0.id"` walks into a list.
- **Bare int** - a single-level lookup, useful for integer-keyed payloads
  (tag-value maps): `field_equals(35, "D")`.
- **Sequence** (tuple or list) - the canonical form. It is the only way
  to reach a dict key that literally contains `"."`, and it disambiguates
  a string key `"0"` from a list index `0`:

  ```python
  field_equals(("trace.id",), "abc")          # dict key with a dot
  field_equals(("items", "0"), "x")            # string key "0" on a dict
  field_equals(("items", 0), "x")              # list index 0
  field_equals(("fills", 0, "price"), 100.25)  # mixed nesting
  ```

### Nested shape matching

`contains_fields` does a recursive **subset** match over dicts and lists.
Leaves can be literals or other matchers.

```python
from choreo.matchers import contains_fields, eq, ne, in_, gt, lt, matches, exists, all_of, not_

s.expect("events.processed", contains_fields({
    "event": {
        "status":     "COMPLETED",
        "count":      gt(0),
        "amount":     all_of(gt(0.0), lt(10_000.0)),
        "order_id":   matches(r"^ORD-\d+$"),
        "trace_id":   exists(),
    },
    "steps":  [{"state": in_(("NEW", "PART"))}],
    "actor":  not_(in_(("blocked_1", "blocked_2"))),
}))
```

### Composition and list quantifiers

```python
from choreo.matchers import all_of, any_of, not_, every, any_element

all_of(field_equals("kind", "CREATE"), field_gt("count", 0))
any_of(field_equals("status", "COMPLETED"), field_equals("status", "PART_COMPLETED"))
not_(field_equals("status", "REJECTED"))

# List quantifiers: use inside contains_fields or at the top level.
every(field_gt("px", 0.0))                       # every element passes
any_element(field_equals("side", "BUY"))         # at least one element passes
```

A list-quantifier inside a shape match expresses "there exists a fill with
px > 100" in one line:

```python
contains_fields({"order": {"fills": any_element(field_gt("px", 100.0))}})
```

Three layers deep is the rule of thumb before factoring out into a named
matcher.

### Raw-bytes escape hatch

When you need to match on a non-JSON payload (a fixed-width wire format,
a binary blob), `payload_contains` does a substring check on the raw bytes.
It **requires a `bytes` payload** and raises `TypeError` otherwise. If
you have a decoded dict or string, use `field_matches` / `contains_fields`
instead.

```python
from choreo.matchers import payload_contains

s.expect("frames.echo", payload_contains(b"MAGIC"))
```

### Writing your own

A matcher is anything implementing the `Matcher` Protocol: `description: str`
plus `match(payload) → MatchResult`. For a side-by-side expected/actual
diff in the report, also implement the optional `Reportable` Protocol
(`expected_shape()`); matchers without it fall back to `description`.
Build one as a frozen dataclass and compose it exactly like the built-ins.

---

## Transports

The library ships five transports and defines a 7-method `Transport`
Protocol so you can drop in your own.

### `NatsTransport`

Talks to a real NATS broker. Lazy-imported: `nats-py` is only required if
you actually construct one.

```python
from pathlib import Path
from choreo.transports import NatsTransport

transport = NatsTransport(
    servers=["nats://localhost:4222"],
    allowlist_path=Path("config/allowlist.yaml"),
    name="my-suite",           # reported to broker (default: "choreo")
    connect_timeout_s=5.0,     # total connect budget
)
```

Validates `nats_servers` in the allowlist.

`KafkaTransport`, `RabbitTransport`, and `RedisTransport` follow the same
shape with their own constructor fields and allowlist categories
(`kafka_brokers`, `amqp_brokers`, `redis_servers`).

### `MockTransport`

In-memory pub/sub for unit-scope tests. Synchronous dispatch (subscribers
fire before `publish()` returns). Optionally validates `endpoint` against
the `mock_endpoints` allowlist category.

```python
from pathlib import Path
from choreo.transports import MockTransport

transport = MockTransport(
    allowlist_path=Path("config/allowlist.yaml"),   # optional
    endpoint="mock://localhost",                    # optional
)
```

Diagnostic methods (for testing your test code, not your system):
`transport.sent()`, `transport.active_subscription_count()`,
`transport.clear_subscriptions()`.

### Transport authentication

Every real transport accepts an optional `auth=` parameter, a typed
descriptor for the auth mode your broker requires. Credentials are cleared
from memory after `connect()` returns and never appear in `repr()`,
`pickle`, error messages, or pytest assertion diffs.

```python
from choreo.transports import NatsTransport, NatsAuth

# Literal: credentials in source (fine for local dev / CI).
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.user_password("admin", "s3cret"),
)

# Resolver: credentials fetched at connect() time (stronger lifetime).
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=lambda: NatsAuth.token(os.environ["NATS_TOKEN"]),
)

# Async resolver: for Vault, Secrets Manager, etc.
async def fetch_creds():
    secret = await vault_client.read("secret/nats")
    return NatsAuth.user_password(secret["username"], secret["password"])

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=fetch_creds,
)
```

See [docs/guides/authentication.md](docs/guides/authentication.md) for the
full cookbook: every NATS auth mode, TLS variants, resolver recipes for
Vault and AWS Secrets Manager, and the consumer fixture pattern.

### Writing your own transport

Drop a module under
[packages/core/src/choreo/transports/](packages/core/src/choreo/transports/)
implementing the `Transport` Protocol. The `connect()` implementation is
where your allowlist enforcement, credential handling, and socket setup
all live. The Harness never sees those details.

```python
from typing import Protocol, Callable, Optional

TransportCallback = Callable[[str, bytes], None]
OnSent = Callable[[], None]

class Transport(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def subscribe(self, topic: str, callback: TransportCallback) -> None: ...
    def unsubscribe(self, topic: str, callback: TransportCallback) -> None: ...
    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None: ...
    def active_subscription_count(self) -> int: ...
    def clear_subscriptions(self) -> None: ...
```

Follow the pattern in [nats.py](packages/core/src/choreo/transports/nats.py)
(asyncio-native) or [mock.py](packages/core/src/choreo/transports/mock.py)
(synchronous). The `on_sent` callback is how you report post-wire
timing to the timeline. Fire it after the message is on the wire, on
the asyncio loop thread.

---

## Test report

**[View the live test report →](https://clear-route.github.io/choreo/)**

The **choreo-reporter** package is a pytest plugin that writes an
interactive HTML report and a structured JSON file at the end of every
pytest run.

Install and it is active, no explicit opt-in needed (via pytest11 entry
point).

```bash
pip install -e 'packages/core-reporter[test]'

# Run your tests as usual
pytest

# Output (default location)
ls test-report/
# index.html   results.json   assets/

# Custom location
pytest --harness-report=/tmp/my-report

# Disable entirely
pytest --harness-report-disable
```

What the report includes:

- **Scenario list** with pass/fail/slow/timeout counts at a glance.
- **Jaeger-style waterfall timeline** for each scenario: every publish,
  receive, match, mismatch, reply, and deadline event plotted on one axis.
- **Expected-vs-actual diffs** for failing handles, driven by
  `matcher.expected_shape()`.
- **Per-reply lifecycle**: was it armed? did it match? did it fire?
- **Git metadata** per test (commit, branch, author).
- **Credential redaction (best-effort).** The default redactor strips
  values under a denylist of field names (`password`, `token`, `secret`,
  `api_key`, `authorization`, close variants), scrubs bearer/API-key
  tokens and `scheme://user:pass@host` URL credentials from string
  output, and lets consumers layer on domain rules:

  ```python
  from choreo_reporter import register_redactor

  def mask_api_keys(text: str) -> str:
      return re.sub(r"sk-[A-Za-z0-9]{32}", "sk-REDACTED", text)

  register_redactor(mask_api_keys)
  ```

  Redaction is best-effort. See [SECURITY.md](SECURITY.md) for the
  scope and the recommended posture for PII-heavy test suites.

- **pytest-xdist support.** Each worker writes partial JSON; the reporter
  merges them at session end.

---

## Chronicle: Longitudinal Analytics

Chronicle is a self-hosted reporting server that ingests `test-report-v1` JSON
over time, stores it in TimescaleDB, and serves a React dashboard for
performance analytics. It answers questions no single report can: "Is this
topic getting slower?", "Did that deploy break anything?", "Are we within
budget?"

```bash
# After your CI pipeline runs pytest:
curl -X POST https://chronicle.internal/api/v1/runs \
  -H "Content-Type: application/json" \
  -H "X-Chronicle-Tenant: my-team" \
  -d @test-report/results.json
```

Chronicle lives in `packages/chronicle/`. See the
[Chronicle README](packages/chronicle/README.md) for installation, API
reference, and deployment instructions.

**Try it locally:**

```bash
pip install choreo-chronicle
docker compose -f docker/compose.chronicle.yaml up -d
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle migrate
DATABASE_URL=postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle \
  python -m chronicle
# Dashboard at http://localhost:5173
```


---

## Running end-to-end tests

A separate e2e suite exercises the Transport Protocol against real
networks by pointing transport implementations at disposable brokers under
Docker Compose.

```bash
# Install the NATS extra
pip install -e 'packages/core[test,nats]'

# Bring up all brokers (NATS, NATS-auth, Kafka, RabbitMQ, Redis)
docker compose -f docker/compose.e2e.yaml --profile all up -d

# Or just one transport
docker compose -f docker/compose.e2e.yaml --profile nats up -d

# Run just the e2e suite
pytest -m e2e

# Tear down
docker compose -f docker/compose.e2e.yaml --profile all down
```

The compose stack includes both unauthenticated and authenticated broker
profiles. The `nats` profile brings up two NATS containers: one without
auth on port 4222 and one with user/password auth on port 4223. The e2e
suite exercises the Transport Protocol contract against both, verifying
that the `auth=` descriptor path works end-to-end.

If `nats-py` isn't installed, or no broker is reachable at `NATS_URL`
(default `nats://localhost:4222`), the e2e suite **skips** rather than
fails. CI should treat a skip there as the compose stack not coming up.

Override the broker URL with `NATS_URL`, but add the URL to the
`nats_servers` category in your allowlist first, or `connect()` will refuse.

---

## Linting and formatting

Ruff is the single source of truth for lint and format across the monorepo.
Config lives in the root `pyproject.toml` under `[tool.ruff]`. Pre-commit
runs it on every commit; CI enforces the same check on every PR.

```bash
# One-time local setup
pip install pre-commit
pre-commit install

# Manual invocation
ruff check packages/          # lint
ruff check --fix packages/    # lint + auto-fix
ruff format packages/         # format
ruff format --check packages/ # verify formatting without rewriting
```

---

## Contributing

Issues, bug reports, and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community norms. The project
is Apache 2.0 licensed.
