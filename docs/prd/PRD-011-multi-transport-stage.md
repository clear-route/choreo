# Multi-Transport Scenarios — The Stage Coordinator — Product Requirements Document

**Status:** Draft
**Created:** 2026-05-04
**Last Updated:** 2026-05-04
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering, Integration Test Engineering

---

## Executive Summary

Add a new top-level coordinator, `Stage`, that wraps a named registry of `Harness` instances and exposes a single scenario DSL spanning all of them. A test author can then publish on one transport (e.g. NATS), expect on another (e.g. Kafka), and register cross-transport replies, in one declarative scope with one global deadline. The existing `Harness(transport)` API is unchanged; single-transport tests are unaffected.

The motivating use case: an application-under-test (AUT) acts as a bridge — a request arrives on NATS, the AUT translates and republishes onto a Kafka topic for the durable processing pipeline, the pipeline emits a result on Kafka, the AUT translates the result back onto NATS for the original requester. Today choreo cannot express this round trip in one scenario.

---

## Problem Statement

### Current State

`Harness` ([packages/core/src/choreo/harness.py:45-74](../../packages/core/src/choreo/harness.py#L45-L74)) holds exactly one `Transport`, one `Codec`, and one `CorrelationPolicy`. Scenarios spawned from a harness can already register multiple `expect()` / `publish()` / reply registrations across different topics, but every one of them shares that single transport.

This is fine for a single-protocol test: NATS-only, Kafka-only, mock-only. It breaks down the moment the AUT bridges between protocols.

### Concrete Example — NATS ↔ Kafka Bridge

A request-processing service accepts requests on NATS (low-latency request/response pub/sub), translates each request into an event on a Kafka topic for the durable processing pipeline, awaits the pipeline's result event on a sibling Kafka topic, and republishes the result back on NATS to the original requester.

To test this end-to-end today, an author has three bad options:

1. **Two harnesses, two scenarios.** Spin up `harness_nats` and `harness_kafka`, run two parallel scenarios with two `await_all()` deadlines, manually correlate by sleeping or polling. No single timeline. Failures on one side don't cancel the other.
2. **One harness, mock both transports as one.** Build a fake transport that internally fans out NATS and Kafka messages on the same wire. The wire format collapses into a single codec, Kafka's header semantics are lost, and the test no longer exercises the real bridge contract.
3. **Skip the test.** The most common outcome. The bridge boundary becomes the least-tested seam in the system.

### User Pain Points

- **No single timeline.** Two harnesses cannot share `await_all()`. A test author has to invent ad-hoc rendezvous logic and decide what counts as "done".
- **Correlation lives in the AUT, not the test.** When the AUT translates a NATS request id `req-12345` into a Kafka event id `evt-orders-12345`, the test currently has no way to express "wait for the Kafka side, then map the result back to the NATS side". Each transport's `CorrelationPolicy` is local; nothing crosses the boundary.
- **No cross-transport replies.** [PRD-008](PRD-008-scenario-replies.md) introduced reactive replies (`s.on(topic).publish(topic, build=...)`) within one transport. There is no equivalent for "when an event arrives on Kafka, reply on NATS".
- **Lifecycle is uncoordinated.** Two harnesses connect and disconnect independently. A failure in one mid-test leaves the other still running, masking the cause.

### Business Impact

- Bridge-boundary tests are the highest-value tests in any service whose job is protocol translation, and they are the ones we cannot write.
- Engineers drop down to integration suites against real downstream systems to cover bridge behaviour; these are slow, flaky, and expensive.
- Regressions in the translation layer are caught in UAT or production rather than CI.

---

## Goals and Objectives

### Primary Goals

1. **One scenario, multiple transports.** A test author writes a single `stage.scenario(name)` block that publishes, expects, and replies across any number of named transports, with one global deadline.
2. **Per-transport codecs and correlation, coordinated by the Stage.** Each transport keeps its own wire format and its own correlation policy. The Stage maps a logical scope ID across them, so an `expect()` on Kafka matches the event that the AUT emitted in response to a `publish()` on NATS.
3. **Cross-transport reply registration via the same DSL.** `s.on("trigger.topic", on="kafka").publish("response.topic", on="nats", build=...)` reads as one sentence. No new vocabulary beyond the `on=` selector.
4. **Existing single-transport API unchanged.** `Harness(transport)` keeps its current shape and semantics. A consumer with no need for `Stage` should see no diff in their test code.
5. **Fail-fast lifecycle.** If any transport in the Stage fails to `connect()`, the Stage aborts and tears down the rest. Tests do not run with a partial fabric.

### Success Metrics

- **Coverage:** the canonical NATS-in / Kafka-bridge / NATS-out round-trip is expressible in ≤15 lines of Stage DSL, end to end.
- **No-regression:** the existing single-transport scenario suite passes without modification against the Stage-aware code path.
- **Correlation correctness:** a parallel-isolation test runs 100 concurrent Stage scenarios, each with two transports and a translated correlation map; every scope sees only its own messages on every transport. Zero cross-talk.
- **Lifecycle correctness:** a test where transport B fails to connect terminates with transport A's connection released, surfaced as a single error referencing both transports.
- **Adoption:** at least one consumer test suite (target: the integration bridge suite) replaces a hand-rolled two-harness fixture with `Stage`-based fixtures within one sprint of landing.

### Non-Goals

- **Replacing `Harness`.** `Stage` is an additive coordinator. Existing single-transport tests do not migrate.
- **A unified codec.** Per-transport codecs stay per-transport. The Stage does not invent a meta-codec; if a transport needs JSON, it gets `JSONCodec`; if a transport needs a binary or schema-aware codec (e.g. Avro, Protobuf), it gets that one. Two transports in the same Stage can use different codecs.
- **A global topic namespace.** Topics remain per-transport. The `on=` selector disambiguates; there is no implicit routing of a topic name to "whichever transport has it".
- **Distributed correlation propagation in the AUT.** Choreo does not inject correlation headers into the wire on behalf of the AUT. The AUT is still expected to propagate (or translate) correlation values itself; the Stage's job is to know how to *read* the value back on each side.
- **Transport-level transactions.** The Stage does not provide cross-transport atomicity (e.g. "publish on A and B atomically"). Tests that care about ordering use the same primitives they do today.
- **Multi-hop reply chains (`on → publish → on → publish`).** Same deferral as PRD-008. The API should not foreclose it.

---

## User Stories

### Primary User Stories

**As a** test author writing a bridge round-trip test,
**I want to** publish on NATS, register a reply on Kafka, and expect a result on NATS, all in one scenario,
**So that** the test reads as one timeline and fails as one event.

**Acceptance Criteria:**
- [ ] `stage.scenario(name)` returns a scope whose `expect()`, `publish()`, and `on()` accept an `on=<transport_name>` selector.
- [ ] `await_all(timeout_ms)` enforces one global deadline across every handle on every transport.
- [ ] If any handle on any transport times out, the scenario fails and the result names which transport produced the timeout.

---

**As a** test author running hundreds of bridge scenarios in parallel,
**I want to** know that the Kafka result routes to the NATS scope it belongs to,
**So that** parallel runs don't false-positive each other.

**Acceptance Criteria:**
- [ ] The Stage generates one logical correlation ID per scope.
- [ ] A user-supplied `CorrelationBridge` translates the logical ID to each transport's wire-level identifier (e.g. logical UUID → NATS request id, → Kafka event id).
- [ ] An `expect()` on Kafka uses the bridge to recover the logical ID from the inbound Kafka message and match it to the scope.
- [ ] A parallel-isolation test runs 100 Stage scenarios concurrently, each with two transports; every scope's handles resolve from messages tagged with that scope's logical ID. Zero cross-scope matches.

---

**As a** test author standing in for the downstream Kafka processing pipeline,
**I want to** declare "when the AUT publishes an order event on Kafka, reply on Kafka with a processed-event" without a hand-rolled subscriber,
**So that** my test has the same reactive ergonomics as same-transport replies.

**Acceptance Criteria:**
- [ ] `s.on("orders.new", on="kafka").publish("orders.processed", on="kafka", build=...)` registers a same-transport reply via the Stage's selector.
- [ ] `s.on("orders.new", on="kafka").publish("results", on="nats", build=...)` registers a cross-transport reply: trigger on Kafka, response on NATS.
- [ ] The reply builder receives the decoded triggering payload, decoded by the *trigger* transport's codec.
- [ ] The reply is encoded by the *response* transport's codec before publish.
- [ ] Reply registrations are scope-bound and deregister on scope exit on both transports.

---

**As a** consumer of choreo running tests in CI,
**I want to** know that if any one of my transports fails to connect, the test fails with a clear error rather than running with a partial fabric,
**So that** I don't chase phantom failures from a half-connected stage.

**Acceptance Criteria:**
- [ ] `await stage.connect()` connects every registered transport. If any one fails, every transport that has already connected is disconnected before the error propagates.
- [ ] The raised exception names the failing transport and the underlying error.
- [ ] `stage.disconnect()` is idempotent and tolerates partial-up state from a prior failed `connect()`.

---

**As an** author of an existing single-transport test,
**I want** my test to keep working with no changes,
**So that** Stage adoption is opt-in and incremental.

**Acceptance Criteria:**
- [ ] `Harness(transport)`, `Harness(transport, codec=...)`, `harness.scenario(name)`, and the existing `expect/publish/on/await_all` DSL are all unchanged in signature and semantics.
- [ ] The full pre-Stage scenario test suite passes against the post-Stage main branch with no modifications.

---

## Proposed Design

This PRD specifies the abstraction and interface. Implementation detail (file layout, internal data structures) is for a follow-up ADR.

### Conceptual Model

```
                ┌─────────────────────────────────────────────┐
                │                   Stage                     │
                │  ┌────────────┐  ┌────────────┐  ┌────────┐ │
                │  │ Harness    │  │ Harness    │  │ Bridge │ │
                │  │  "nats"    │  │  "kafka"   │  │        │ │
                │  │  +Transport│  │  +Transport│  │  maps  │ │
                │  │  +Codec    │  │  +Codec    │  │ logical│ │
                │  │  +Corr.    │  │  +Corr.    │  │  ID ↔  │ │
                │  └────────────┘  └────────────┘  │ wire ID│ │
                │                                  └────────┘ │
                │              scenario(name) ────►            │
                │              await_all(timeout_ms) ────►     │
                └─────────────────────────────────────────────┘
```

A `Stage` owns:

- A named registry of `Harness` instances: `dict[str, Harness]`.
- A `CorrelationBridge` (see below) that maps a logical scope ID to per-transport wire identifiers.

Each `Harness` continues to own its own transport, codec, and per-transport correlation policy. The Stage does not reach into them.

### Stage API (Sketch)

```python
from choreo import Harness, Stage
from choreo.transports import NatsTransport, KafkaTransport
from choreo.codecs import JSONCodec

stage = Stage(
    harnesses={
        "nats": Harness(NatsTransport(...), codec=JSONCodec()),
        "kafka": Harness(KafkaTransport(...), codec=JSONCodec()),
    },
    bridge=MyCorrelationBridge(),   # see CorrelationBridge below
)

await stage.connect()        # connects all; fail-fast
try:
    async with stage.scenario("bridge_round_trip") as s:
        s.on("orders.new", on="kafka").publish(
            "orders.processed", on="kafka",
            build=lambda order_event: processed_event(order_event),
        )
        s.expect("results", on="nats", contains_fields({"status": "OK"}))
        s = s.publish("requests.in", on="nats", payload=request_fixture)
        result = await s.await_all(timeout_ms=500)
        result.assert_passed()
finally:
    await stage.disconnect()
```

### Scope DSL Changes

Every existing scope method gains an optional `on=<transport_name>` keyword. Within a `stage.scenario()` scope, `on=` is required for any operation that touches a transport (`expect`, `publish`, `on`). Within a `harness.scenario()` scope, `on=` is absent and the operation routes to that harness's single transport — unchanged.

```python
s.expect(topic, matcher, on="kafka")               # was: s.expect(topic, matcher)
s.publish(topic, payload, on="nats")               # was: s.publish(topic, payload)
s.on(trigger_topic, on="kafka").publish(           # cross-transport reply
    response_topic, on="nats",
    build=lambda msg: {...},
)
```

`Handle.transport` is a new read-only attribute: the name of the transport the handle is bound to. Diagnostics reference it.

### Correlation Bridge

The single trickiest piece. The user explicitly stated the Stage must *map between* transports' correlation identifiers, not merely stamp a shared value into different fields.

A `CorrelationBridge` is a small protocol the consumer implements. It says:

```python
class CorrelationBridge(Protocol):
    def fresh(self) -> LogicalScopeId:
        """Generate a logical scope identifier for a new scenario."""

    def to_wire(self, logical: LogicalScopeId, transport: str) -> WireId:
        """Translate the logical id to the wire-level id this transport uses
        when publishing on behalf of the test."""

    def from_wire(self, wire: WireId, transport: str) -> LogicalScopeId | None:
        """Recover the logical id from a wire-level id seen on this transport.
        Return None if this message is not addressed to any active scope."""
```

`LogicalScopeId` is opaque to the framework. `WireId` is whatever the transport's correlation policy returns/consumes (a UUID string, a structured event id, an integer sequence, a header value, etc.).

When a scope registers an `expect("orders.processed", on="kafka")`, the Stage:

1. Asks the harness `"kafka"` to subscribe to `orders.processed`.
2. On every inbound message, the Kafka harness's `CorrelationPolicy` extracts the wire id (e.g. `correlation-id` header value `"evt-orders-12345"`).
3. The Stage calls `bridge.from_wire("evt-orders-12345", "kafka")` to recover the logical id.
4. If it matches the scope's logical id, the matcher runs; if not, the message is discarded as not-for-this-scope.

When the same scope calls `s.publish("requests.in", on="nats", payload=...)`:

1. The Stage calls `bridge.to_wire(logical, "nats")` to get the NATS wire id (e.g. UUID `req-12345`).
2. The NATS harness's `CorrelationPolicy.write()` stamps that value into the outbound payload or header.
3. The harness's transport publishes.

The Stage ships **two reference bridge implementations**:

- `IdentityBridge` — the wire id *is* the logical id on every transport. Equivalent to "they all use the same UUID". For homogeneous-correlation cases (rare in practice, useful in tests of the framework itself).
- `MappedBridge(translations)` — takes a mapping function `(logical, transport) → wire` and its inverse. The common case for protocol bridges where the user knows the AUT's translation rule.

Anything more elaborate (e.g. "the Kafka event id is the NATS request id with a topic-derived prefix and a checksum suffix") is a custom bridge. The Stage does not try to be clever.

### Lifecycle

`Stage.connect()`:
- Calls `harness.connect()` for each registered harness in registration order.
- If any one raises, the Stage iterates over the harnesses that already succeeded and calls `disconnect()` on each, swallowing any disconnect errors but logging them. The original error propagates to the caller, wrapped in a `StageConnectError` that names the failing transport.
- Either every harness is connected, or none are.

`Stage.disconnect()`:
- Calls `harness.disconnect()` for each in reverse registration order.
- Surfaces a multi-error if more than one disconnect raises; logs and continues otherwise.
- Idempotent: safe to call from a `finally` block whether `connect()` succeeded, partially succeeded, or never ran.

### Scenario Scope and `await_all`

`stage.scenario(name)` returns a scope object that:
- Generates one logical scope id via `bridge.fresh()`.
- Holds one expectations list and one replies list, but each entry is tagged with its transport.
- Registers callbacks on the named harness for each entry.
- On `await_all(timeout_ms)`, awaits all expectations on a single global deadline, identical in semantics to `harness.scenario(name).await_all()` today, just spanning multiple transports.
- On exit, deregisters every callback from every transport it touched.

`ScenarioResult` gains a per-transport breakdown for diagnostics: `result.by_transport["kafka"]` returns the handles bound to that transport. The pass/fail semantics are unchanged at the result level.

### Backward Compatibility

- `Harness` is untouched in signature and behaviour.
- The `on=` parameter is absent from `harness.scenario(...)` scopes; passing it surfaces as a `TypeError` from the Python interpreter. Inside `stage.scenario(...)` scopes, `on=` is a keyword argument that defaults to `None`; omitting it (or passing `None`) raises `MissingTransportError` at registration time so the diagnostic names the framework concept rather than a generic interpreter message.
- The existing test suite passes unchanged. A new e2e test asserts mixed single-transport and Stage-based fixtures coexist in one process.

---

## Open Questions

These are intentionally not decided in this PRD. Each is carried into ADR-0027 §Notes with a named owner before implementation kicks off.

- **Topic-name conflict diagnostics.** If two transports legitimately have the same topic name, do we warn at registration time? My instinct: no, since `on=` already disambiguates and per-protocol topic naming conventions differ. **Owner:** Platform — confirm before Phase 1.
- **Per-handle transport-level latency budgets.** `Handle.within_ms()` already exists and continues to work. Should there be a default per-transport budget (e.g. "Kafka is normally slow, set 200ms; NATS 20ms")? Resolved in ADR-0027 §Non-Goals: per-handle override is enough for v1. **Owner:** Platform for follow-up.
- **Stage-level CorrelationPolicy as default.** If a consumer doesn't supply a `CorrelationBridge` and all transports happen to use the same correlation key/value, can we infer `IdentityBridge`? **Resolved: no** — `Stage` requires an explicit `bridge=` argument. `IdentityBridge` is exported but produces a `BridgeAmbiguityError` at `Stage.__init__` whenever more than one transport is registered, so the obvious "easy" choice fails fast rather than silently routing every transport's traffic into every scope. **Owner:** Platform.
- **Mixed-mode scenarios.** Can a single test fixture create both a `Harness` and a `Stage` in the same process? Yes, no shared state. ADR-0027 §Validation includes a regression test asserting coexistence. **Owner:** Implementing engineer.
- **Mid-scenario broker drop.** What happens when a transport connects successfully but the broker drops the connection during a running scenario? Resolved in ADR-0027 §Implementation: handles waiting on the dropped transport resolve as `TIMEOUT` at the global deadline, and `__aexit__` tolerates `unsubscribe()` raising on a dropped transport (per-child isolation, same pattern as the single-transport scope). **Owner:** Implementing engineer.
- **Worked end-to-end example.** Neither this PRD nor ADR-0027 ships the canonical end-to-end example (publish → trigger → reply → expect → assert) in one block. Deferred to ADR-0027 Phase 2 (README / `framework-design.md` Stage section). **Owner:** Author of this PRD.

---

## Validation

### Test Strategy

- **Unit:** `Stage` registry, lifecycle, error wrapping, `IdentityBridge`, `MappedBridge`. No transports involved beyond `MockTransport`.
- **Integration (in-memory):** two `MockTransport` instances in one Stage. Round-trip publish/expect/reply across both. Parallel-isolation test with 100 scopes. Failure-injection on connect.
- **End-to-end:** a Stage with two real transports (e.g. `NatsTransport` + `KafkaTransport`, gated by `pytest -m e2e`). Validates the lifecycle and routing under real broker conditions before any consumer-specific bridge work lands in their suites.

### Metrics

- Line-count for the canonical bridge round-trip ≤ 15.
- Pre-Stage scenario suite passes unchanged on post-Stage main.
- Parallel-isolation test passes at 100 concurrent scopes with zero cross-talk.
- Connect-failure test: stage left in a fully-disconnected state, single error surfaced.

---

## Related Documents

- [PRD-001](PRD-001-framework-foundations.md) — `Harness` and `Transport` foundations
- [PRD-002](PRD-002-scenario-dsl.md) — Scenario DSL primitives this PRD extends
- [PRD-008](PRD-008-scenario-replies.md) — Reactive reply registrations being lifted to multi-transport
- [ADR-0019](../adr/0019-pluggable-correlation-policy.md) — Correlation policy design (the per-transport piece this PRD spans)
- [docs/framework-design.md](../framework-design.md) — Architecture overview

A follow-up ADR ("Stage coordinator and correlation bridge") will document the implementation choices once this PRD is accepted.

---

## Notes

- Naming: `Stage` was preferred over `Ensemble`, `Conductor`, `Choir` in design discussion (2026-05-04). Open to revisit before public API freeze.
- The motivating use case is a NATS ↔ Kafka request-bridge suite. The abstraction is general; the integration team is the first internal customer and the validating signal for adoption.
