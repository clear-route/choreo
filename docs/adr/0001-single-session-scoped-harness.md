# 0001. Single Session-Scoped Harness Facade for Connection Reuse

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md) §3 (Connection reuse)

> **Promoted to Accepted 2026-04-17.** The two blockers that kept this ADR in Proposed status are resolved by: [ADR-0006](0006-environment-boundary-enforcement.md) (environment-boundary guard) and [ADR-0007](0007-harness-failure-recovery.md) (recovery policy). Credential handling, SDK trust-boundary, and log-redaction concerns are delegated to the consumer repo (see SECURITY.md).

---

## Context

Choreo needs to interact with long-lived transports (NATS, Kafka, RabbitMQ, and similar). Creating a new transport context for every test would cost hundreds of milliseconds in setup alone, making a 100-test suite multi-minute on CI. For a pre-merge gate that needs to return under a minute, that cost model is a non-starter.

The opposite extreme — one shared connection for the whole suite — risks cross-test state leakage: a subscription from test A firing on test B, stale callbacks receiving messages from the wrong scope, sockets left open after unclean teardowns.

### Background

- [framework-design.md §3](../framework-design.md) captures the trade-offs between the three connection-reuse options: single session-scoped, pooled, and per-test.
- [context.md §7](../context.md) sketches a `Transport` abstraction with `connect` / `disconnect` lifecycle methods, implying a single long-lived instance.
- Established integration-test frameworks (Testcontainers with `reuse=True`, WireMock with `resetAll()` between tests) converge on the "single shared resource, scoped cleanup between tests" pattern.

### Problem Statement

How should the harness manage expensive-to-create transports (broker connections, stateful mocks) across a test suite?

### Goals

- **Per-test marginal cost under 5 ms after session warm-up.** The first test in a session bears a one-off setup cost (hundreds of ms for transport context creation + connection setup); every subsequent test pays only the marginal scope / subscriber registration cost.
- Zero orphaned sockets or subscriptions after any pytest session.
- Clean ownership: one object responsible for transport lifecycle.
- Test authors never write transport setup code.
- **Refuse to connect to any bus outside the declared test environment.** `Harness.connect()` requires an explicit `environment` parameter matching an allowlist (`dev`, `ci`, `uat-isolated`); missing or unrecognised values cause `Harness.connect()` to raise before any socket is opened. Target host / transport resolver must be on an allowlist for the declared environment. Detailed design in a future ADR on environment-boundary enforcement.
- **Never retain plaintext transport credentials.** Passwords pulled from env vars or a secrets vault at connect time, zeroed from memory after logon. `Harness.__repr__` and transport `__repr__` implementations redact credentials; `__getstate__` raises to prevent pickling.

### Non-Goals

- Multi-process parallelism (xdist). Deferred; this decision targets single-process asyncio. Flagged in Notes because xdist decisions will touch this ADR.
- Persistent cross-run state. Each pytest session starts fresh.
- Pluggable transport backends (Kafka, RabbitMQ). Single bus for now.
- Self-healing recovery from transport SDK corruption. Harness corruption fails fast; recovery policy is a separate open decision (see Notes).

---

## Decision Drivers

- **Speed of CI feedback** — per-test overhead is the dominant cost for small tests.
- **Reliability** — socket / callback leakage is a persistent source of flaky-test reports.
- **Change surface** — future additions (metrics, tracing, replay) should land in one place.
- **Developer ergonomics** — test authors should not handle transport lifecycle.
- **Parallelism readiness** — the design must not preclude asyncio-level parallel tests in the same process.

---

## Considered Options

### Option 1: Single session-scoped Harness

**Description:** One `Harness` object, constructed by a `pytest` session-scoped fixture, tears down at session end. Holds one transport, one `Dispatcher`, and any scope-level state. Shared by every test in the session.

**Pros:**
- Fastest amortised per-test cost.
- One object owns all lifecycle; teardown is one call.
- Natural home for cross-cutting concerns (metrics, tracing, correlation map).
- Mirrors the production reality (one broker connection per process) more faithfully than the alternatives.

**Cons:**
- Shared state surface: a bug in one test can pollute another without careful scoping (addressed by [ADR-0002](0002-scoped-registry-test-isolation.md)).
- Under pytest-xdist (when introduced later), each worker is a separate process → one Harness per worker, not one per suite. Implications tracked in Notes.
- If the Harness enters a corrupted state mid-suite (transport SDK hang, session death), every remaining test fails. No recovery policy is defined by this ADR.

### Option 2: Pool of pre-warmed transports

**Description:** At suite start, create N transports. Tests check one out, run, return it to the pool.

**Pros:**
- Isolates parallel tests from each other without correlation-ID routing.
- Survives a single transport failure by returning it to a broken pool.

**Cons:**
- Solves a problem we don't have in single-process asyncio mode: contention is on the transport callback thread, not on multiple sockets.
- N transports × transport-context-cost = N × (hundreds of ms) at suite start. Pays the setup cost we wanted to avoid.
- Every test must remember to return its transport; checkout / checkin is yet another leakable resource.

### Option 3: Fresh transport per test

**Description:** Connect at each test's setUp, disconnect at tearDown.

**Pros:**
- Perfect isolation. No shared-state class of bugs possible.
- Simplest conceptual model.

**Cons:**
- Seconds of overhead per test. 100 tests × a few seconds = minutes. Fails the speed goal outright.
- Stateful sessions (broker logons, handshakes) would tear down and re-establish per test, making real-transport tests completely impractical.

---

## Decision

**Chosen Option:** Option 1 — Single session-scoped Harness facade.

### Rationale

- The alternatives either fail the speed goal outright (Option 3) or solve a problem we don't have in the target topology (Option 2).
- Shared-state risk is real but bounded: per-scenario cleanup via scoped registries (ADR-0002) and correlation-ID routing via a central Dispatcher (ADR-0004) contain the blast radius.
- The pattern has precedent (Testcontainers with `reuse=True`, WireMock JUnit rules, pytest session fixtures) — not a novel invention.
- Starting with a single Harness and adding a pool later (if measured concurrency demands it) is lower-cost than starting with a pool and removing it. The decision is reversible in the sense that a pool can be introduced behind the same Facade without changing the test-facing API.

---

## Consequences

### Positive

- Per-test transport setup cost approaches zero after the suite warms up.
- One place to boot, monitor, and tear down all external connections.
- Observability hooks (metrics, tracing, surprise logs) live on one object with a clear API.
- Natural fit for `async with harness.scenario(name) as s:` — the Harness is the scope factory.
- Session-scoped fixture lifecycle aligns with modern pytest patterns.

### Negative

- Shared state is a surface area for subtle bugs. Every scope cleanup is mechanically enforced by [ADR-0002](0002-scoped-registry-test-isolation.md); framework-internal tests assert scope residue returns to baseline on every exit path.
- Under pytest-xdist (when introduced), N workers = N Buses = N transport contexts. Each worker bears the setup cost. Additional security implications (N × logons may breach per-identity acceptor session limits) are called out in Notes.
- If the Harness corrupts mid-suite, every remaining test fails. No recovery is attempted by this ADR. A failure-recovery ADR is tracked as open work in Notes.

### Neutral

- Requires a session-scoped asyncio event loop to avoid "Future attached to different loop" errors (see [ADR-0005](0005-pytest-asyncio-session-loop.md)).
- Test authors do not see the concrete transport directly; they only interact via the `Harness` Facade. More indirection, less flexibility, but a smaller surface area to learn.

### Security Considerations

The Harness is the single object that holds all external-system connections and all test-session credentials. This concentration is deliberate but has implications:

1. **Environment boundary.** `Harness.connect(environment, config)` MUST refuse to start when `environment` is missing, unrecognised, or when `config` targets an endpoint outside the allowlist for that environment. A developer running `pytest` against the wrong config cannot accidentally connect the harness to a staging or production bus. This is the single most important safety requirement for the harness. A future ADR documents the allowlist mechanism in detail.
2. **Credential lifecycle.** Logon credentials (passwords, certificate references) are fetched at `Harness.connect()` from an env var or vault token, used for the handshake, then cleared from memory. `Harness.__repr__` and transport `__repr__` implementations redact sensitive fields. `Harness.__getstate__` raises to prevent pickling the Harness into debug artefacts.
3. **Transport SDK trust boundary.** Any vendor SDK running in-process with network access is pinned to a specific version + hash, fetched only from an internal artefact repo, and its config file schema validated before `Harness.connect()` opens any socket. A future ADR documents SBOM / supply-chain details.
4. **Write-only credential accessor.** The Harness exposes `set_credentials(...)` but no `get_credentials()`. Tests have no mechanism to read secrets from the Harness.
5. **xdist logon multiplication (deferred concern).** When xdist is introduced, `N workers × N logons` may breach per-identity concurrent-session limits and lock out real users from the same identity. A worker-count cap enforced against the environment's session-limit allowlist is required before xdist ships.

---

## Implementation

High-level plan from [PRD-001 Timeline and Milestones](../prd/PRD-001-framework-foundations.md#timeline-and-milestones):

1. Implement `Harness` as a Facade with `connect()` / `disconnect()` and accessor methods for the configured transport and `Dispatcher`.
2. Provide a pytest fixture: `@pytest.fixture(scope="session")` that yields a connected Harness and tears down on exit.
3. Register an `atexit` safety net for abnormal pytest terminations.
4. Unit-test the Harness with `MockTransport` — a memory-backed transport that doesn't need any external SDK.

### Migration Path

Not applicable — this is a greenfield framework.

### Timeline

- Phase 1 (Mock-backed skeleton): 1 week after approval.
- Phase 2 (Real transport SDK integration): 2 weeks after SDK access is confirmed.
- Phase 3 (Hardening): 1 week after Phase 2.

---

## Validation

How will we know this decision was correct?

### Success Metrics

- **Per-test marginal time after warm-up:** <5ms (i.e. from the second test onwards). Measured via pytest timings + framework instrumentation. The first-test budget is separate and reflects transport context creation time.
- **First-test latency budget:** Harness connect (transport context + logon) completes within **2 seconds** on commodity CI. If exceeded, the cause is logged for review.
- **Teardown correctness:** zero orphaned receivers after any pytest session. Verified by a per-session residue check that asserts receiver count == 0 on teardown.
- **Suite runtime:** 100-test suite completes in under 60 seconds on commodity CI.
- **Environment-guard effectiveness:** a deliberately-misconfigured fixture with a production-like host is rejected by `Harness.connect()` with a specific error; framework-internal test asserts this.

### Monitoring

- Framework-level counter: `harness.active_subscriptions` should drop to zero at session end.
- CI pipeline reports per-session setup / teardown duration.
- Any "Future attached to different loop" error logged to CI is a regression signal for this decision (or for ADR-0003 or ADR-0005).

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scoped subscriber registry + correlation IDs (the isolation mechanism that makes this decision safe).
- [ADR-0003](0003-threadsafe-call-soon-bridge.md) — `loop.call_soon_threadsafe` bridge (the thread-safety mechanism the shared Harness depends on).
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher as Mediator (the routing layer on top of the shared Harness).
- [ADR-0005](0005-pytest-asyncio-session-loop.md) — Session-scoped pytest-asyncio event loop (the loop-scope alignment this decision requires).
- [ADR-0006](0006-environment-boundary-enforcement.md) — Environment-boundary guard (prod-connection refusal; closes the first blocker to Accepted status).
- [ADR-0007](0007-harness-failure-recovery.md) — Failure recovery policy (closes the second blocker: what happens when the Harness corrupts mid-suite).

---

## References

- [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md)
- [framework-design.md §3](../framework-design.md) — Connection reuse options and trade-offs
- [context.md §7](../context.md) — Original Transport sketch
- [pytest fixtures — session scope](https://docs.pytest.org/en/stable/how-to/fixtures.html#fixture-scopes)
- [Testcontainers-python `reuse=True`](https://deepwiki.com/testcontainers/testcontainers-python/3.8-ryuk-and-resource-cleanup) — precedent for shared-resource-across-suite
- [WireMock JUnit rule](https://wiremock.org/docs/junit-jupiter/) — per-test reset on shared server

---

## Notes

The single-Harness decision is load-bearing for every subsequent framework decision. If it turns out to be wrong — e.g. because transport contexts can't be shared across asyncio tasks safely — expect this ADR to be superseded and every downstream ADR to need revisiting.

**Open follow-ups blocking Accepted status:**

- **Environment-boundary enforcement** — design the allowlist mechanism, the `HARNESS_ENV` token handling, the per-environment host / client-identity allowlists, and the test-mode tagging on every outbound correlation ID. **Owner:** Platform / Test Infrastructure. Tracked as a future ADR in the docs/adr/ README.
- **Harness-corruption recovery policy** — decide between fail-fast, quarantine-and-retry, and reboot-Harness. Affects CI runtime when any transport SDK enters a bad state mid-suite. **Owner:** Platform / Test Infrastructure.
- **Transport SDK supply-chain controls** — SBOM, version pinning + hash, fetch source, licence-file provenance, startup log line. **Owner:** Platform / Security.
- **Credential-handling specifics** — concrete redacting `__repr__` / `__getstate__` behaviour for Harness and FixMock; write-only credential accessor API. **Owner:** Platform / Test Infrastructure.
- **xdist implications** — when xdist is introduced, the worker-count cap against per-identity session limits must be enforced. Worker-local correlation ID namespacing must prevent cross-worker surprise-log cross-contamination. **Owner:** Framework maintainers when xdist lands.

**First-test latency note:** the advertised "<5 ms per-test setup" from PRD-001 is marginal cost *after* warm-up. The first test in any session pays the full Harness-creation cost (transport context, logon). In dev-local short runs (e.g. 3 tests), per-test cost is dominated by this setup. This is the expected behaviour of a session-scoped Harness and not a bug.

**Last Updated:** 2026-04-17
