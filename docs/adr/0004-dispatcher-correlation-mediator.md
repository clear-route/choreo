# 0004. Dispatcher as a Mediator for Correlation-Based Inbound Dispatch

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md) — inbound dispatch architecture

> **Stateful-fake wiring resolved:** all inbound messages — including those into and out of any stateful fake adapter — route through the Dispatcher. The broker is the single dispatch point. See Decision §Rationale and Implementation.

---

## Context

With a shared Harness ([ADR-0001](0001-single-session-scoped-harness.md)) and a scoped test-isolation model ([ADR-0002](0002-scoped-registry-test-isolation.md)), inbound messages from transports (LBM, mocks, stateful fakes) still need to be routed to the right test's pending Futures. Parallel scenarios share the Harness — so a single receiver may see messages intended for several concurrent tests.

The question is: who is responsible for that routing, and what information does it use?

A naive design has each scenario register a filter callback with the transport directly, and the transport iterates all registered callbacks on every message. This works for small N, but:

- Bundles routing logic into the transport layer, making it harder to add cross-cutting concerns (metrics, surprise logging, replay).
- Duplicates correlation extraction across every callback.
- Provides no central view for observability or debugging.

### Background

- [framework-design.md §10](../framework-design.md) names this component `Dispatcher` and calls it a **Mediator** — the Gang of Four pattern for decoupling many components by introducing an intermediary.
- [framework-design.md §4](../framework-design.md) (option 5) commits to correlation-ID routing as the parallelism mechanism.
- [PRD-001 §User Stories](../prd/PRD-001-framework-foundations.md#user-stories) lists this as a framework maintainer need: *"I want to add or change the inbound dispatch logic in one place."*

### Problem Statement

How are inbound messages from transports routed to the right test's Future, across any transport, with visibility into failures and space for future cross-cutting features?

### Goals

- Single routing implementation across every transport and every in-process fake.
- Constant-time lookup from correlation ID to target scope.
- Unmatched messages visible, not silently dropped.
- Natural extension point for metrics, tracing, replay.

### Non-Goals

- Replacing the transport layer — the broker sits above it, not beside it.
- Load balancing or high-availability dispatch — single-process test harness.
- Message transformation — the broker routes, it does not rewrite payloads.

---

## Decision Drivers

- **Parallel test throughput** — this is the mechanism that makes parallel execution correct.
- **Single change surface** — observability and replay must land in one place.
- **Transport-agnosticism** — the broker should not care whether a message came from LBM, NATS, or any in-process fake.
- **Debuggability** — when a test's Future never resolves, we need to know if the message arrived and was misrouted vs never arrived at all.
- **Performance** — the broker is on the hot path for every inbound message.

---

## Considered Options

### Option 1: Dispatcher as central Mediator with correlation → scope map (chosen)

**Description:** One `Dispatcher` object owns a `correlation_id → ScenarioScope` map. Every transport feeds inbound to the broker via `dispatcher.dispatch(msg, source)`. The broker extracts the correlation ID, looks up the scope, and resolves the appropriate Future. Unmatched messages go to a surprise log.

**Pros:**
- Single implementation of routing logic, transport-agnostic.
- O(1) map lookup; extractor cost dominates per-message expense regardless, so this option doesn't make extraction cheaper than alternatives but also doesn't make it more expensive.
- Single point for metrics, tracing, surprise logging, replay capture.
- Mirrors the Mediator pattern (Gang of Four) with documented precedent.
- Transports are pass-through forwarders: they hold no routing state.

**Cons:**
- One more level of indirection per message (function call through `dispatcher.dispatch`).
- Dispatcher lifetime must match the Harness's; teardown ordering matters.
- Correlation extraction is pluggable per topic / message type — the broker needs an extractor registry, described in Implementation. A rename of a schema field silently breaks the extractor; mitigated by contract tests in each consumer repo.

### Option 2: Per-scenario filter callbacks registered with transports

**Description:** Each scenario registers a callback with each transport it subscribes to. The callback filters by correlation. The transport iterates callbacks on every message.

**Pros:**
- No Mediator object needed; fewer top-level components.
- Scenario-to-callback relationship is direct and visible.

**Cons:**
- Routing logic duplicated per callback.
- Transports iterate all callbacks per message — O(N) in number of active scenarios.
- No central hook for metrics or surprise logging — every transport would need its own.
- Makes future replay / forensic features much harder.

### Option 3: Asyncio queue per scenario

**Description:** Each scenario owns an `asyncio.Queue`. Transports push onto a shared queue, a dispatcher task pulls and routes by correlation.

**Pros:**
- Decouples transport from scenarios via queue.
- Natural backpressure.

**Cons:**
- Adds a dispatcher task per scenario — more lifecycle to manage.
- Doesn't fundamentally differ from Option 1; Option 1 is the "same idea minus a queue per scenario".
- Latency: queue → pull → route is slower than direct routing.

### Option 4: No routing; every scenario sees every message

**Description:** Scenarios subscribe with their own filter. No central dispatch.

**Pros:**
- No new components at all; scenarios self-service.

**Cons:**
- Fails parallelism.
- Every scenario gets every message. O(N²) pattern for a suite.
- Collapses to Option 2 eventually as people add filters.

---

## Decision

**Chosen Option:** Option 1 — `Dispatcher` as a central Mediator with a correlation → scope map.

### Rationale

- The Mediator pattern is the textbook solution for "many-to-many routing decoupled from participants" (Gang of Four).
- Single routing implementation scales to any number of transports: LbmTransport, NatsTransport, in-process stateful fakes (both inbound subscription and outbound publish), potentially other back-ends later.
- Central dispatch gives one home for observability (metrics, tracing, surprise logs) and forensic features (replay, capture) without restructuring later.
- Per-test filters (Option 2) distribute the same complexity across N callbacks with no central hook; Option 3 adds queueing overhead without a clear benefit for single-process asyncio.

**Stateful-fake wiring — decided here.** An in-process stateful fake adapter is a Harness-level peer to the real transports. Its outbound messages flow through `Transport → Dispatcher → Future`, exactly like any other publisher. Its inbound subscription (reading upstream traffic to drive its own responses) also routes via the broker; the broker's extractor table treats the fake's subscribed topics the same as any other. Alternative (fake reads directly from `Transport._on_message` bypassing the dispatcher) is rejected because it creates a second dispatch path, undermines the "single dispatch point" invariant, and makes correlation metrics incomplete.

---

## Consequences

### Positive

- Parallel scenarios route correctly with no cross-talk (subject to correlation IDs round-tripping — that's [ADR-0002](0002-scoped-registry-test-isolation.md)'s responsibility).
- One place to wire in metrics, OpenTelemetry spans, dispatch-latency histograms.
- Surprise log for unmatched inbound is implemented once in the broker (see Security Considerations for content-classification requirements).
- Future replay / capture: broker records every dispatch; a replay mode reads the log and re-fires.
- Transports are pass-through forwarders; no routing state duplicated outside the dispatcher.

### Negative

- One more object in the architecture. Boot / teardown / lifecycle to manage.
- Correlation extraction must be registered per topic / message type (e.g. one topic uses a JSON field, another uses a protobuf field, another uses a transport-level header). The broker owns that extractor registry.
- A broker bug affects routing for every scenario — single point of failure, but also single point of fix.

### Neutral

- Test authors never touch the broker directly. Interaction is through the Harness / Scenario DSL.
- Dispatcher state is purely in-memory. Process crash loses the correlation map — acceptable for a test harness.
- The broker is Python code, GIL-bound. At 10k+ messages per second we may need to revisit, but the suite is not expected to run at that scale.

### Security Considerations

The broker is the single dispatch surface for the harness, which makes it both powerful and concentrated attack surface. Specific requirements:

1. **Correlation IDs are cryptographically random** — UUIDv4 or `secrets.token_hex(16)`. Never sequential, never timestamp-based. Prevents a buggy or adversarial SUT from echoing a predictable ID from a prior scope and firing a later scope's Future.
2. **Scope-liveness check on dispatch** — before resolving a Future, assert the target scope is still registered. Late-arriving messages to a deregistered correlation go to the surprise log as `timeout_race` and do not resolve anything.
3. **Extractor constraints** — `register_extractor(topic_or_msg_type, extractor_fn)` accepts only pure parsing functions. No deserialisers that execute payload-provided code (`pickle.loads`, `yaml.load` without SafeLoader, custom `__reduce__` hooks). Enforced by type hint + framework-internal test that attempts to register a `pickle.loads`-based extractor and asserts it is rejected.
4. **Surprise-log redaction** — unmatched inbound may contain sensitive payload data (identifiers, personal data, monetary amounts, regulatory short-codes). The surprise log records only (a) topic or message type, (b) correlation ID value, (c) payload size, (d) timestamp, (e) classification bucket (`timeout_race`, `unknown_scope`, `no_correlation_field`). Full payload capture requires an explicit `--unsafe-full-capture` CLI flag and is forbidden in CI.
5. **Cross-field sanity check (adversarial SUT)** — optional but recommended: the broker can verify that a second discriminating field on the payload matches what the registered scope expects, before resolving. A mismatch is logged and dropped. Default: disabled; enabled per-test via a scope flag.
6. **Tampering resistance** — `Dispatcher` methods are declared `@typing.final`. A startup integrity check asserts `Dispatcher.dispatch.__func__ is Dispatcher._original_dispatch` (set at class-definition time) to catch monkey-patching by rogue conftest plugins or compromised dev dependencies.

---

## Implementation

1. `Dispatcher` class (declared `@typing.final`):
   - `register_scope(scope, correlation_id)` — called on `scenario_scope.__aenter__`.
   - `deregister_scope(scope)` — called on `scenario_scope.__aexit__`.
   - `dispatch(msg, source_transport)` — called by each transport on inbound. Extracts correlation, **checks scope liveness**, then resolves Future via [ADR-0003](0003-threadsafe-call-soon-bridge.md) bridge. Late arrivals for deregistered scopes go to surprise log.
   - `register_extractor(topic_or_msg_type, extractor_fn)` — per-message-type correlation extraction. Extractor function signature constrained to `(payload) -> str | None`; no deserialisers.
   - `surprise_log()` — returns queue of redacted unmatched entries (topic, corr_id, size, timestamp, classification). Full-payload variant is behind `--unsafe-full-capture` and forbidden in CI.
2. Each transport `_on_message(msg)` hook calls `dispatcher.dispatch(msg, source=<transport_name>)`.
3. **Stateful-fake inbound routes through the dispatcher.** Any in-process stateful fake is a Harness-level peer; its subscribed topics flow through `Transport → dispatcher.dispatch`, and its outbound publications flow back through `Transport → dispatcher.dispatch` like any other publisher. No dispatch path bypasses the dispatcher.
5. Dispatcher lifecycle tied to Harness lifecycle: created in `Harness.__init__`, cleared on `Harness.close()`.
6. **Extractor registry:** a single YAML file `harness/extractors.yaml` declares `{topic_or_msg_type: field_path}` pairs. Loaded at Harness startup. New services register their correlation field here (not in Python code) to make contract drift visible in diffs.
7. **Startup integrity check** in `Harness.connect()`: `assert Dispatcher.dispatch is Dispatcher._original_dispatch` (the latter captured at class definition). Monkey-patching fails loudly.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (skeleton with MockTransport): broker implemented + unit-tested without SDK.
- Phase 2 (real transports): broker wired into LBM + NATS.
- Phase 3 (observability): metrics, surprise log, replay hooks.

---

## Validation

How will we know this decision was correct?

### Success Metrics

- **Correct routing:** 100% of inbound messages with a known correlation ID reach the right scope. Verified by framework-internal parallel-test harness.
- **Dispatch latency:** broker adds **<100μs p99** on the hot path, measured with perf tests.
- **Unmatched inbound visibility:** every surprise is captured; zero silent drops. Verified by fault-injection tests that deliberately stage unmatched messages.
- **Single-change-surface test:** adding a new transport type requires only `dispatcher.dispatch` calls from that transport — no changes to scenarios or the DSL.

### Monitoring

- Counter: `dispatcher.inbound_total`, `dispatcher.inbound_routed`, `dispatcher.inbound_surprised`.
- Histogram: broker dispatch latency (µs).
- CI log: `surprise_log()` dumped at session end; any entries become a warning.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Harness is the broker's owner and lifecycle controller.
- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scopes register with the broker; correlation IDs come from scope allocation.
- [ADR-0003](0003-threadsafe-call-soon-bridge.md) — The broker dispatches to Futures via the loop bridge.

---

## References

- [framework-design.md §10](../framework-design.md) — Dispatcher in the runtime architecture
- [PRD-001](../prd/PRD-001-framework-foundations.md) — Framework foundations PRD
- Gang of Four, *Design Patterns* — Mediator pattern
- WireMock priority-ordered stub list — precedent for central dispatch with first-match routing

---

## Notes

**Open follow-ups:**

- **Extractor registry ownership.** Who owns `harness/extractors.yaml`? Proposal: each service team adds its entries when onboarding to the harness; a schema CI check verifies every inbound topic the harness subscribes to has a matching entry. **Owner:** Platform / Test Infrastructure, in coordination with service teams.
- **Correlation-less broadcasts.** Market-data snapshots may have no correlation field at all. PRD-001 Open Question #3 tracks this. Proposal: a special `broadcast` subscription mechanism on the Harness; the broker treats broadcasts as fan-out and skips the correlation check. **Owner:** Framework.
- **Adversarial-SUT cross-field sanity** — per Security Considerations item 5, this is implementable but not default. Revisit once we have real-world data on how often wrong-echo happens in practice. **Owner:** Framework.

**Last Updated:** 2026-04-17
