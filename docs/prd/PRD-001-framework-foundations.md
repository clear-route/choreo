# Framework Foundations — Harness, Dispatcher, Transports — Product Requirements Document

**Status:** Draft
**Created:** 2026-04-17
**Last Updated:** 2026-04-18
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering

---

## Executive Summary

Establish the suite-scoped plumbing every test in the harness depends on: a single `Harness` facade that owns one transport connection, one Dispatcher for correlation-based inbound routing, and a thread-safety bridge between any off-loop callback thread and the asyncio event loop. No test author should write socket or callback-threading code — the Harness abstracts all of it behind one interface.

---

## Problem Statement

### Current State

The test harness has no shared connection layer. Every test would have to:
- create its own transport connection (hundreds of ms of setup for most backends)
- set up its own receiver callbacks
- handle the hand-off from the transport's callback thread to the asyncio loop by hand
- clean up on teardown (often forgotten, leaking sockets)

Over a 100-test suite, that is **minutes of setup overhead** and a consistent source of flaky teardown bugs.

### User Pain Points

- **Slow feedback.** Per-test transport setup dominates a typical test's runtime.
- **Thread-safety traps.** Many native-code transport SDKs fire callbacks on their own thread. A test that resolves an `asyncio.Future` from the wrong thread causes nondeterministic failures.
- **Leaking state.** Tests that don't clean up receivers pollute the next test's subscriptions, producing flakes that only surface in CI.
- **Correlation routing done ad-hoc.** With no shared dispatcher, parallel tests on a shared connection see each other's messages. Authors either serialise (slow) or write per-test filters (error-prone).

### Business Impact

- CI feedback loop is too slow for pre-merge gating.
- Flaky tests erode engineer trust and get marked `@skip`, eroding coverage.
- Without correlation routing, tests cannot run in parallel → suite growth directly hurts merge velocity.

---

## Goals and Objectives

### Primary Goals

1. One transport connection per pytest session, shared by all tests in the session.
2. Correlation-based inbound dispatch so parallel tests on shared infrastructure do not see each other's messages.
3. Thread-safe delivery from any off-loop callback thread to asyncio Futures, guaranteed, invisibly to test authors.
4. Guaranteed teardown on test exit, including when tests raise exceptions.

### Success Metrics

- Per-test transport setup cost: **under 5ms** (vs hundreds of ms for fresh connection creation).
- Zero failing tests due to "got Future attached to a different loop" in the first 90 days of use.
- Zero orphaned subscriptions after any pytest session completes (including after SIGINT).
- Suite of 100 tests runs in **under 60 seconds** with correlation parallelism enabled, without any per-test connection setup.

### Non-Goals

- **Scenario DSL.** Covered by PRD-002. This PRD defines the interfaces the DSL builds on.
- **Multiprocess parallelism (pytest-xdist).** Deferred; single-process asyncio parallelism only.
- **Stateful fake adapters.** Out of scope here. This PRD makes them possible; it does not define one.

---

## User Stories

### Primary User Stories

**As a** test author,
**I want to** write a test that subscribes to a topic and receives messages,
**So that** I can assert on service behaviour without handling transport threading or connection setup.

**Acceptance Criteria:**
- [ ] A test that writes `async with harness.scenario("x") as s: s.expect("topic", matcher)` works without knowing which transport is attached.
- [ ] The test's Future is resolved on the asyncio loop thread, never on any transport callback thread.
- [ ] Teardown of the scenario unregisters the subscription even if the test raises.

---

**As a** framework maintainer,
**I want to** add or change the inbound dispatch logic in one place,
**So that** correlation routing behaviour is consistent across the whole harness.

**Acceptance Criteria:**
- [ ] All inbound traffic from any Transport flows through Dispatcher before reaching a test's Future.
- [ ] Correlation ID extraction is configurable per topic / per message schema.
- [ ] Unmatched correlations are recorded and exposed as a query-able "surprise log" per session.

---

**As a** CI engineer,
**I want to** run tests in parallel on a shared Harness,
**So that** suite wall-clock time scales with the slowest test, not the sum of all tests.

**Acceptance Criteria:**
- [ ] N parallel scenarios on one Harness do not see each other's messages when each has a unique correlation ID.
- [ ] Total suite time for 100 tests is under 60s on commodity CI hardware.

---

## Proposed Solution

### High-Level Approach

One `Harness` object, created once per pytest session, owns:

- **Transport** — any implementation of the five-method `Transport` Protocol. Subscribe, unsubscribe, publish, and its own allowlist enforcement. Shipped implementations: `MockTransport`, `NatsTransport`, `KafkaTransport`, `RabbitTransport`, `RedisTransport`.
- **Dispatcher** — the Mediator. Holds `correlation_id → ScenarioScope` map. Receives every inbound message from the transport and dispatches to the right scope.
- **LoopPoster** — internal helper. Transports that deliver messages off the asyncio loop use it to hand work to the loop via `loop.call_soon_threadsafe(...)`.

Tests never touch these directly. They interact with the Harness via the ScenarioScope DSL (PRD-002). But the scope calls through to the Harness for every subscribe / publish / inbound dispatch.

### User Experience

Test author sees this (from the perspective of PRD-002 — this PRD implements the parts below the DSL line):

```
async with harness.scenario("my test") as s:
    s = s.expect("events.completed", Matcher.field_equals("status", "COMPLETED"))
    s = s.publish("requests.submitted", fixture)
    result = await s.await_all(timeout_ms=500)
```

Underneath, the Harness:

1. The `harness.scenario("my test")` call creates a fresh `ScenarioScope`, registers it with `Dispatcher` under a fresh correlation ID.
2. `s.expect(topic, matcher)` calls `harness.subscribe(topic, callback)` which registers a scope-local callback on the active transport.
3. `s.publish(topic, payload)` injects the correlation ID into the payload and calls `harness.publish(topic, payload)`.
4. When the transport fires a message, the Dispatcher extracts the correlation ID, looks up the scope, and resolves its Future via `loop.call_soon_threadsafe` if the callback arrived off-loop.
5. On `__aexit__` the scope calls `harness.unsubscribe_all(scope)` which removes every callback this scope registered.

### Key Components

- **Harness (Facade)** — top-level object; all access goes through it.
- **Transport** — Protocol with five methods plus allowlist enforcement; one concrete impl per backend.
- **Dispatcher (Mediator)** — inbound router. Holds correlation map. Dispatches to Futures.
- **LoopPoster** — asyncio thread-safety bridge for transports that deliver off-loop.
- **SurpriseLog** — collects unmatched inbound for debugging (metadata only; payloads not retained).

---

## Scope

### In Scope

- `Harness` Facade: construct, connect, disconnect, expose transport accessors.
- `Transport` Protocol plus at least `MockTransport` for framework-internal testing.
- `Dispatcher` with correlation-ID-keyed dispatch, unmatched-log, scope registration / deregistration.
- Thread-safety bridge (off-loop callback thread → asyncio loop) for transports that need it.
- pytest session-scoped fixture pattern that consumers use to create/tear down the Harness.
- Correlation-ID injection into outbound payloads (configurable field name per topic).
- Correlation-ID extraction from inbound payloads (same configuration).

### Out of Scope

- Scenario DSL — PRD-002.
- Stateful fake adapters — out of scope here.
- Real transport SDK licensing, installation, and packaging — a downstream concern for the deployment that consumes the framework.
- pytest-xdist (multi-process) support — covered by later scope.

### Future Considerations

- Shared-state correlation map for xdist support.
- Metrics / OTel emission from Dispatcher for dispatch latency visibility.
- Additional transport back-ends behind the same Transport Protocol shape.

---

## Requirements

### Functional Requirements

**Must Have (P0):**
- [ ] `Harness` exposes `subscribe(topic, callback)`, `unsubscribe(topic, callback)`, `publish(topic, payload)` methods.
- [ ] Multiple callbacks per topic: a dispatcher iterates all callbacks when a message arrives.
- [ ] `Dispatcher.register_scope(scope, correlation_id)` / `deregister_scope(scope)`.
- [ ] Inbound messages route to the right `ScenarioScope` by correlation ID.
- [ ] `loop.call_soon_threadsafe` used on every cross-thread callback path.
- [ ] Unmatched correlations logged with topic, correlation value, and timestamp, queryable via `harness.surprise_log()` (metadata only; payloads not retained).
- [ ] `MockTransport` for in-memory unit tests of the framework itself.
- [ ] Session-scoped pytest fixture pattern that creates Harness, yields it, tears it down.
- [ ] Harness teardown disconnects the transport, closes all sockets, and releases all callbacks. Runs on normal exit and on exception.
- [ ] Correlation-ID extraction pluggable per topic: default field name `correlation_id`, overridable per subscription.

**Should Have (P1):**
- [ ] `atexit` safety net: if pytest crashes mid-session, sockets still release.
- [ ] Diagnostic `harness.dump_state()` returns active subscriptions, registered scopes, surprise log.

**Nice to Have (P2):**
- [ ] Metrics hook: count of inbound routed / dropped / surprised; dispatch latency histogram.

### Non-Functional Requirements

**Performance:**
- Per-test transport setup cost: **<5ms** amortised (one Harness shared across tests).
- Inbound dispatch latency (transport callback → Future resolve): **<1ms p99** on commodity hardware.
- Publish cost: bounded by the transport's publish cost; no framework overhead beyond correlation-ID injection.

**Reliability:**
- Zero "Future attached to different loop" errors. Enforced by always using `call_soon_threadsafe` on off-loop paths.
- Zero orphaned subscriptions after any session end (normal or exception).
- Idempotent `unsubscribe`: calling twice is safe.

**Observability:**
- Structured log per inbound: `{topic, correlation_id, scope_id | None, latency_ms, outcome}`.
- Session-end summary: total inbound, total routed, total surprised.
- `surprise_log()` retains unmatched messages (metadata only) for post-hoc debugging.

**Compatibility:**
- Python 3.11+ (for `asyncio.timeout_at`, needed downstream by PRD-002).
- pytest 8.x + pytest-asyncio 0.24+ (for `loop_scope` semantics).

---

## Dependencies

### Internal Dependencies

- **Transport extras** — each transport pulls in its own dependency via a `pip install 'choreo[<name>]'` extra (e.g. `nats`, `kafka`, `rabbitmq`, `redis`).
- **pytest-asyncio** — for session-scoped event loop.

### External Dependencies

- Any broker runtime (NATS, Kafka, RabbitMQ, Redis, …) required by the transport a consumer chooses. The repo ships a Docker Compose stack for local / CI e2e testing.

---

## Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Native-code transport SDK is unavailable on CI | High | Medium | Mock transport lets us build and unit-test without any real SDK. Real transports are optional extras; consumers install only what they use. |
| Thread-bridging bug causes nondeterministic failures | High | Low | Enforce `call_soon_threadsafe` in one helper; no direct loop access from off-loop threads anywhere. Code-review gate. |
| Correlation ID isn't echoed by some services | Medium | Medium | Pluggable `CorrelationPolicy` with a no-op default (ADR-0019) — consumers who can't guarantee echo opt out and serialise those tests. |
| Session teardown races with in-flight messages | Medium | Medium | Drain inbound queue with timeout before disconnecting. Log any drops. |
| Surprise log grows unbounded in long sessions | Low | Medium | Cap size (ring buffer, default 10k); emit warning when capped. |

---

## Alternatives Considered

### Alternative 1: Fresh transport per test

**Description:** Connect on every test's setUp, disconnect on tearDown.

**Why not chosen:**
- Seconds of overhead per test: unacceptable for 100+ test suites.
- No meaningful gain in isolation — scoped subscribers already deliver that.

### Alternative 2: Pool of transports

**Description:** Pre-warm N transports, tests check one out.

**Why not chosen:**
- Adds complexity without clear benefit in single-process asyncio mode.
- The real parallelism constraint is not multiple sockets; it is correlation-based routing on one.

### Alternative 3: No dispatcher, each test subscribes directly with its own filter

**Description:** Each test registers its own callback that filters by correlation.

**Why not chosen:**
- Duplicated filter logic across tests.
- No central visibility into dispatch and unmatched messages.
- Harder to add features like metrics or surprise logging later.

---

## Open Questions

1. **Correlation field naming conventions.** Should the default extractor look for `correlation_id`, or provide a pluggable policy from the outset? Addressed by ADR-0019.
   - **Status:** Resolved — ADR-0019 landed the pluggable `CorrelationPolicy` with `NoCorrelationPolicy` default.
   - **Owner:** Platform

2. **How do we handle inbound with no correlation at all?** (e.g. broadcast events.) Log-and-drop, or expose a "broadcast" subscription mechanism?
   - **Status:** Open
   - **Owner:** Framework

---

## Timeline and Milestones

### Phases

**Phase 1: Mock-backed skeleton**
- `Harness`, `MockTransport`, `Dispatcher` with correlation routing
- Session-scoped pytest fixture pattern
- First round-trip scenario using the mock transport

**Phase 2: Real-broker integration**
- `NatsTransport`, `KafkaTransport`, `RabbitTransport`, `RedisTransport`
- Thread-bridge via `call_soon_threadsafe` for any transport that delivers off-loop
- Docker Compose stack for local / CI e2e

**Phase 3: Hardening**
- `atexit` safety net
- Surprise log and diagnostic dumps
- Performance measurement vs success metrics

### Key Milestones

- [x] ADRs Created (connection reuse, scoped registry, dispatcher, thread bridge, allowlist): ADR-0001..0007, ADR-0019
- [x] Framework-internal unit tests pass
- [x] E2E transport contract suite passes against NATS, Kafka, RabbitMQ, Redis
- [ ] Pluggable correlation policy (ADR-0019) tracked in follow-up work

---

## Appendix

### Related Documents

- [framework-design.md](../framework-design.md) — architecture narrative
- [context.md](../context.md) — contributor implementation notes
- [ADR-0001 — Single session-scoped Harness](../adr/0001-single-session-scoped-harness.md)
- [ADR-0002 — Scoped registry + correlation IDs](../adr/0002-scoped-registry-test-isolation.md)
- [ADR-0003 — Thread-safety bridge](../adr/0003-threadsafe-call-soon-bridge.md)
- [ADR-0004 — Dispatcher](../adr/0004-dispatcher-correlation-mediator.md)
- [ADR-0006 — Allowlist enforcement](../adr/0006-environment-boundary-enforcement.md)
- [ADR-0019 — Pluggable correlation policy](../adr/0019-pluggable-correlation-policy.md)

### References

- pytest-asyncio `loop_scope` docs — https://pytest-asyncio.readthedocs.io/en/stable/concepts.html

### Glossary

- **Harness** — the top-level Facade object; one per pytest session.
- **Dispatcher** — Mediator that routes inbound messages to Futures by correlation ID.
- **Correlation ID** — opaque value per `ScenarioScope`; injected into outbound payloads, extracted from inbound to route responses back. See ADR-0019 for the pluggable policy.
- **ScenarioScope** — per-test context manager; defined in PRD-002; referenced here as the unit of dispatch.

---

**Approval Signatures:**

- [ ] Product Owner: _________________ Date: _______
- [ ] Technical Lead: _________________ Date: _______
- [ ] Security Review: _________________ Date: _______
