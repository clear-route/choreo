# Stage — Multi-Transport Scenarios

A user guide for writing tests that span multiple message transports
in a single scenario.

> **TL;DR.** `Stage` wraps a named registry of `Harness` instances and
> a `CorrelationBridge`. One scenario can publish on transport A,
> register a reactive reply on transport B, and assert on transport
> A again — under one global deadline. Designed for testing bridge
> services and protocol translators.

For the design rationale, see
and . For
the broader framework context, see [framework-design.md §12](../framework-design.md#12-multi-transport-scenarios-stage).

---

## When to use Stage

Use `Stage` when **the system under test bridges between two
transports** and the natural test boundary spans both. Examples:

- A request gateway that consumes NATS requests and republishes them
  as Kafka events.
- A protocol translator that bridges legacy AMQP traffic to a modern
  Kafka pipeline.
- A latency-sensitive front-end that round-trips through a durable
  back-end and back.

Use a plain `Harness` when only one transport is involved — Stage
adds correlation translation and per-transport routing that cost
nothing on the single-transport path but add concepts the test
author has to learn. One transport, one `Harness`. Two transports,
one `Stage`.

---

## Quickstart: NATS ↔ Kafka round-trip

```python
from admiral import Harness, MappedBridge, Stage
from admiral.correlation import DictFieldPolicy
from admiral.matchers import field_equals
from admiral.transports import KafkaTransport, NatsTransport

# Per-transport correlation policy. Without this the wire id never
# round-trips through the message and parallel-isolation breaks down
# on shared brokers.
nats_h = Harness(
    NatsTransport(servers=[NATS_URL], allowlist_path=ALLOWLIST),
    correlation=DictFieldPolicy(field="correlation_id"),
)
kafka_h = Harness(
    KafkaTransport(bootstrap_servers=[KAFKA_BOOTSTRAP], allowlist_path=ALLOWLIST),
    correlation=DictFieldPolicy(field="correlation_id"),
)

# The bridge translates a per-scope logical id into per-transport
# wire ids. Two transports + a deterministic prefix is enough for
# most tests.
bridge = MappedBridge(forwards={
    "nats":  lambda logical: f"nats-{logical}",
    "kafka": lambda logical: f"kafka-{logical}",
})

stage = Stage(
    harnesses={"nats": nats_h, "kafka": kafka_h},
    bridge=bridge,
)

await stage.connect()
try:
    async with stage.scenario("orders-bridge-round-trip") as s:
        # When the AUT publishes orders.new on Kafka, emit the
        # processed-event on NATS.
        s.on("orders.new", on="kafka").publish(
            "orders.processed", on="nats",
            build=lambda trigger: {"forwarded": trigger["payload"]},
        )
        # Assert on the AUT's downstream NATS message.
        result_handle = s.expect(
            "results", field_equals("kind", "result"), on="nats",
        )
        # Kick off the round-trip.
        s.publish("orders.new", {"payload": 42}, on="kafka")
        result = await s.await_all(timeout_ms=5000)
finally:
    await stage.disconnect()

assert result.passed
assert result_handle.message["forwarded"] == 42
```

The full bridge round-trip in 13 lines of scenario body. Every line
of setup before the `async with` is reusable across tests.

---

## Concepts

### `CorrelationBridge`

Maps a per-scope logical id to per-transport wire ids. Two
implementations ship:

- **`IdentityBridge`** — every transport sees the same wire id.
  Useful for framework-internal tests and for single-transport
  Stages. Rejected at `Stage.__init__` for multi-transport Stages
  because every transport would produce the same wire id (and trip
  `BridgeAmbiguityError`).
- **`MappedBridge`** — explicit per-transport forward functions.
  The common case for production protocol bridges where each
  transport's wire id has a deterministic shape derived from the
  logical id. Optionally takes inverse functions for diagnostic
  `from_wire` translation.

Custom bridges implement the small `CorrelationBridge` Protocol:

```python
class CorrelationBridge(Protocol):
    async def fresh(self) -> Any: ...
    def to_wire(self, logical: Any, transport: str) -> str: ...
    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None  # diagnostic only; default is no inverse mapping
```

`fresh()` is called once per scope to mint a logical id.
`to_wire(logical, transport)` is called once per registered
transport per scope at scope entry (eager minting). `from_wire` is
optional and used only for diagnostic logs.

### Per-harness `CorrelationPolicy`

The bridge produces wire ids; the harness's `CorrelationPolicy`
stamps and reads them on the wire. The default `NoCorrelationPolicy`
is a no-op — fine for tests where one Stage runs alone on a broker
with no other tenants. Tests that run on **shared infrastructure**
(or that exercise parallel-isolation across many Stages) configure
each harness with a `DictFieldPolicy` (or a header-based policy):

```python
nats_h = Harness(
    NatsTransport(...),
    correlation=DictFieldPolicy(field="correlation_id"),
)
```

With `DictFieldPolicy`, `Stage.publish` stamps the per-child wire id
into the published payload; the inbound dispatcher callback reads
the stamped id back and filters out messages destined for another
scope. 100 concurrent scopes can share a real broker without
cross-talk.

### `on=` selector

Every DSL method on a Stage scope takes an `on=<transport>` keyword
argument. It is required (omitting it raises
`MissingTransportError`); an unknown name raises
`UnknownTransportError` with the registered set in the diagnostic.

```python
s.expect(topic, matcher, on="kafka")
s.publish(topic, payload, on="nats")
s.on(trigger_topic, on="kafka").publish(response_topic, on="nats", build=...)
```

The cross-transport reply chain uses **two** `on=` selectors —
trigger on Kafka, response on NATS — making the cross-transport
direction explicit at the call site.

### Result shape

`_StageScenarioResult` (returned by `await_all`) has:

- `handles: tuple[Handle, ...]` — every expectation's resolved handle,
  with `Handle.transport` reflecting the registering transport.
- `passed: bool` — True iff every handle resolved as PASS.
- `replies: tuple[StageReplyReport, ...]` — one report per
  `on().publish()` registration, with `state` (FIRED /
  ARMED_NO_MATCH / FIRED_BUILDER_ERROR / etc.),
  `trigger_transport`, `response_transport`, `candidate_count`,
  `match_count`.
- `by_transport: Mapping[str, tuple[Handle, ...]]` — per-transport
  view of handles.

```python
result = await s.await_all(timeout_ms=500)
result.assert_passed()                            # raises with
                                                  # diagnostics on
                                                  # any failing handle
nats_handles = result.by_transport["nats"]        # NATS-side handles
fired_replies = [r for r in result.replies
                 if r.state is StageReplyState.FIRED]
```

---

## Patterns

### Cross-transport reply (the canonical bridge)

```python
async with stage.scenario("bridge") as s:
    s.on("orders.new", on="kafka").publish(
        "orders.processed", on="nats",
        build=lambda trigger: build_response(trigger),
    )
    s.expect("orders.processed", matcher, on="nats")
    s.publish("orders.new", request_payload, on="kafka")
    result = await s.await_all(timeout_ms=5000)
```

The reply lifecycle is fire-once: the trigger callback flips `_Reply`
state from ARMED to FIRED before invoking `build`, so a nested
publish from inside `build` cannot re-fire. Build/publish exceptions
flip state to FIRED_BUILDER_ERROR (terminal) and capture
`builder_error` as the exception class name (never `str(exc)`).

### Same-transport reply via Stage

A reply where trigger and response use the same transport is the
degenerate case of cross-transport. The Stage handles it without a
special path:

```python
async with stage.scenario("same-transport-echo") as s:
    s.on("orders.new", on="kafka").publish(
        "orders.echoed", on="kafka",
        build=lambda trigger: {"echoed": trigger["payload"]},
    )
    ...
```

This shape has identical observable behaviour to a single-`Harness`
`s.on(...).publish(...)` chain — same fire-once, same lifecycle
reports.

### Parallel scopes on shared infrastructure

100 concurrent scopes against the same brokers stay isolated by the
correlation filter:

```python
async def run_scope(stage, scope_idx):
    async with stage.scenario(f"scope-{scope_idx}") as s:
        h = s.expect("response", field_equals("scope_idx", scope_idx),
                     on="nats")
        s.publish("request", {"scope_idx": scope_idx}, on="kafka")
        return await s.await_all(timeout_ms=5000)

results = await asyncio.gather(
    *[run_scope(stage, i) for i in range(100)]
)
```

For this to work, each harness must be configured with a
`DictFieldPolicy` so the wire id round-trips through the message
and the inbound filter can route per-scope.

### Decoupling test orchestration from the AUT

The Stage scenario describes WHAT messages should flow across the
boundary. The AUT (the system under test) is a separate process
running its own NATS/Kafka clients. The test publishes the input
that triggers the AUT; the AUT consumes, processes, and republishes;
the test's `expect` resolves on the AUT's output.

For unit-style tests where you do not want a real AUT running,
register a reactive reply via `s.on(...).publish(...)` to
mock the AUT's behaviour from inside the scenario.

---

## Lifecycle

### State machine

```
NEW ──connect()──► CONNECTED ──disconnect()──► DISCONNECTED
                       │
                       │  on connect() failure:
                       │  rollback (failing transport + siblings),
                       │  state stays NEW
                       ▼
                     NEW
```

Re-use is not supported. After `disconnect()` the Stage is
terminal; construct a new `Stage` to reconnect.

### `connect()` rollback

If any harness's `connect()` raises mid-way, the Stage rolls back:

1. The failing harness's transport is disconnected via
   `Harness.force_disconnect()` (closes the resource even though
   the harness state never reached "connected").
2. Already-connected siblings are disconnected in reverse
   registration order.
3. Rollback failures (a transport that also fails on disconnect)
   are logged at WARNING (`stage_rollback_failing_transport_disconnect_failed`
   / `stage_rollback_sibling_disconnect_failed`) and swallowed —
   the rollback path itself never raises.

A `StageConnectError` then surfaces with the failing transport
named on the typed attribute.

### `disconnect()` aggregation

Best-effort across all transports. If any harness's `disconnect()`
raises, the Stage continues to disconnect the rest, then raises
`StageDisconnectError(ExceptionGroup)` carrying every collected
failure. Use PEP 654 `except*` to walk the group:

```python
try:
    await stage.disconnect()
except* StageDisconnectError as eg:
    for exc in eg.exceptions:
        logger.warning("disconnect failed: %s", exc)
```

Single-failure disconnect produces a group of length 1 — the
surface is uniform regardless of how many transports raised.

### Mid-scenario broker drop

Handles waiting on a dropped transport resolve as `Outcome.TIMEOUT`
at the global deadline. `Handle.transport` names which side dropped,
so failure diagnostics in CI logs are unambiguous about where to
look.

The `__aexit__` per-child unsubscribe loop is isolated by per-pair
try/except (mirrors the single-`Harness` scope's existing
behaviour): a failing unsubscribe on a dropped transport is logged
at WARNING and does not abort the rest of the teardown.

---

## Errors

| Error | When raised |
|-------|-------------|
| `BridgeAmbiguityError` | Bridge produced the same wire id for two transports (smoke test or per-scope re-validation). |
| `BridgeTransportMismatchError` | Bridge advertising `configured_transports` does not match the registered harness set. |
| `BridgeTranslationError` | Bridge call (`fresh`/`to_wire`/`from_wire`) raised, OR `to_wire` returned a non-`str`/empty/oversized value. Carries `.original`. |
| `MissingTransportError` | Stage DSL method called with `on=None`. |
| `UnknownTransportError` | Stage DSL method called with `on=<typo>`. |
| `StageStateError` | Stage method called in the wrong lifecycle state (e.g. `connect()` twice). |
| `StageConnectError` | Stage.connect aborted on a transport failure. Names the failing transport. |
| `StageDisconnectError` | One or more disconnects raised. Subclass of `ExceptionGroup` (PEP 654). |

All inherit from `StageError` for catch-all `except StageError` (and from a standard taxon — `LookupError`/`ValueError`/`RuntimeError`/`ExceptionGroup` — for idiomatic Python error handling).

---

## Operational guidance

### Allowlist

Every transport in a Stage must have its endpoint(s) in the shipped
`config/allowlist.yaml`. The Stage performs no extra allowlist
enforcement; the per-harness transport handles its own at
`connect()` time.

### Per-Stage timeouts

`await_all(timeout_ms=N)` is the only deadline that matters.
Per-transport budgets are not supported in v1; use
`Handle.within_ms()` per expectation if you need per-handle latency
SLAs.

### Debugging cross-scope leaks

If a scope resolves on a message that doesn't belong to it,
suspect:

1. **No `DictFieldPolicy`**: each harness defaults to
   `NoCorrelationPolicy`, which is broadcast (every message reaches
   every scope's matcher). Configure a `CorrelationPolicy` per
   harness.
2. **Bridge `fresh()` is collision-prone**: the `j2` canary in
   `tests/integration/test_stage_parallel_isolation.py` documents
   this failure mode. The shipped `MappedBridge.fresh()` uses
   `secrets.token_hex(16)` and is collision-resistant for typical
   process-local use.
3. **Same-name transports across two Stages**: each Stage's
   bridge is independent; if two Stages share a broker AND share a
   `CorrelationPolicy.field`, they share a correlation namespace.
   Distinguish by configuring a unique `prefix` on each Stage's
   `DictFieldPolicy`.

---

## Reading the test report

The `admiral-reporter` package emits `test-report/results.json` and
`test-report/index.html` per ;
 extends both
surfaces with Stage-specific fields.

### `results.json` shape for a Stage scenario

A Stage scenario lands in the JSON with an additional `stage` block.
The presence of `scenario.stage` is the canonical signal that the
scenario was a Stage scenario:

```json
{
  "name": "bridge_round_trip",
  "correlation_id": "logical-3f2a91b8c4d50e1f",
  "outcome": "pass",
  "stage": {
    "bridge_class": "MappedBridge",
    "transports": ["kafka", "nats"],
    "correlation_ids": {
      "nats":  "sha256:3f2a91b8c4d50e1f",
      "kafka": "sha256:7e8b50c1ad4912ff"
    }
  },
  "handles": [
    {
      "topic": "results",
      "outcome": "pass",
      "transport": "nats",
      "correlation_id": "sha256:3f2a91b8c4d50e1f"
    }
  ],
  "replies": [
    {
      "trigger_topic": "orders.new",
      "reply_topic": "orders.processed",
      "state": "replied",
      "trigger_transport": "kafka",
      "response_transport": "nats"
    }
  ]
}
```

Three things to note when consuming:

1. **`scenario.correlation_id` is the *logical* id** (the value
   `bridge.fresh()` returned at scope entry, not redacted — it is the
   consumer-meaningful pivot for cross-run linkage).
   **`handle.correlation_id` is the per-handle *wire* id, hash-redacted**
   (`sha256:<16 hex>`). Different identifiers, different roles.
2. **`reply.state` keeps the v1.0 four values.** The framework's
   `StageReplyState.FIRED` maps to `"replied"`; `FIRED_BUILDER_ERROR`
   maps to `"reply_failed"`. No new enum strings.
3. **`reply.reply_topic` carries the framework's `response_topic`
   value**. The JSON key stayed stable; the framework renamed
   internally for cross-transport clarity but the wire format did not.

### Run-level `transport` vs `transports`

For a run containing any Stage scenario, `run.transport` is `null` and
`run.transports` is the sorted union of every transport name encountered
across both single-`Harness` and Stage scenarios. Single-`Harness`-only
runs continue to emit `run.transport` and omit `run.transports`.

### HTML report — visual surface

The HTML report renders Stage scenarios with:

- A small **transport badge** on each handle row.
- A **scope-level Stage breadcrumb** between the scenario header and
  the handle list, carrying the bridge class name, the registered
  transports as pills, and the per-transport hash-redacted correlation
  ids (in a collapsed disclosure under "correlation ids").
- **Trigger / response transport badges** on every reply row;
  cross-transport replies show distinct badges, same-transport replies
  show the same badge twice.
- A **failing-side sub-badge** in the scenario header when any handle
  failed — `FAIL → [nats]` (or multiple).
- A **failing-reply sub-badge** when a reply landed in `reply_failed`
  — `REPLY FAILED → [nats]`.

The renderer's `data-*` attributes split into stable and advisory tiers
6. See the [reporter README](../../packages/core-reporter/README.md)
for the full table; consumers writing CI selectors against the report
should rely on stable-tier attributes only.

### Schema versioning

`schema_version` is `"1.3"`.
Consumers gating on `schema_version.startswith("1")` continue to work
across v1.0 through v1.3. Strict-validator consumers update their
pinned schema document to
[test-report-v1.3.json](../schemas/test-report-v1.3.json); v1.2, v1.1,
and v1.0 schemas remain in tree.

The v1.3 addition is a new optional `source` enum field on
`timeline_entry` (`publish` / `expect` / `reply` / `scope`) tagging
the DSL surface that produced the event. Disambiguates a test-side
publish from a reply-chain's automatic response on the same topic.
Single-`Harness` entries omit the field for byte-identity.

The v1.2 additions (still in effect) are: optional `transport`
(per-transport attribution for Stage entries), optional
`logical_topic` (forward-compatibility for translating bridges), and
`topic` relaxed to optional (scope-level events such as DEADLINE
omit the field). The HTML report adds a Stage timeline banner,
per-transport swim lanes, cross-transport reply arrows, and
virtualisation for cap-saturated workloads. See for the
full specification and the admiral-reporter README for the consumer
contract.

### Wire-id redaction

Stage per-transport correlation ids (framework: "wire ids") are
hash-redacted at the report boundary via
`admiral.redaction.redact_correlation_id` (SHA-256 truncated to 16
hex chars, prefixed `sha256:`). The framework's in-process `_redact()`
for short-lived error messages is unchanged. Algorithm version is
exposed in `run.redactions.redaction_version` (currently `"v1"`).

The protection assumes the correlation id has at least 64 bits of entropy.
The shipped `IdentityBridge` and `MappedBridge` use
`secrets.token_hex(16)` for `bridge.fresh()` by default. Bridges that
derive `fresh()` from low-entropy or PII sources defeat redaction —

---

## See also
- [framework-design.md §12 — Multi-transport scenarios (Stage)](../framework-design.md#12-multi-transport-scenarios-stage)
- [Test plan: 0027-stage-integration-tests.md](../test-plans/0027-stage-integration-tests.md)
- [`admiral-reporter` README](../../packages/core-reporter/README.md) — full `data-*` tier table
