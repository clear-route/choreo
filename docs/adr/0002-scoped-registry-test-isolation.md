# 0002. Scoped Subscriber Registry Plus Correlation IDs for Test Isolation

**Status:** Proposed
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-001 — Framework foundations](../prd/PRD-001-framework-foundations.md) §4 (Test isolation)

> **Proposed, not Accepted.** Two acceptance blockers remain: (1) PRD-001 Open Question #2 is unresolved — no downstream service has been confirmed to round-trip correlation IDs, so the parallelism layer's premise is unverified; (2) the serial-per-service fallback depends on a pytest classification mark introduced by a later PRD (not yet written). See Open Questions and Notes.

---

## Context

[ADR-0001](0001-single-session-scoped-harness.md) commits the harness to a single shared `Harness` for the whole pytest session. That decision is only safe if per-test isolation is enforced — otherwise one test's subscriber fires on another's message, rule sets leak, and the suite becomes a web of invisible dependencies.

Two different kinds of isolation problem exist:

1. **Lifecycle leakage** — a subscriber registered by test A is still receiving messages when test B runs. Same for stateful mock rules, per-scenario state, and correlation-ID registrations.
2. **Content leakage under parallelism** — test A and test B run concurrently on the same Harness, both publish on `orders.approved`, both expect on `orders.booked`. Without a discriminator, each test's callback fires for both messages.

These are distinct problems needing different mechanisms.

### Background

- [framework-design.md §4](../framework-design.md) lists the four isolation layers (subscriber registry, stateful-mock rule set, correlation identity, session / sequence state) and the mechanism for each.
- [context.md §6](../context.md) describes the correlation-ID parallelism strategy and the serial-per-service fallback.
- WireMock's JUnit rule uses a similar pattern: shared WireMock server, per-test rule reset via `resetAll()`, and optional per-test stub scoping.

### Problem Statement

How do we guarantee that tests running on a shared Harness don't observe each other's subscribers, rules, correlation registrations, or messages — under both sequential and parallel execution?

### Goals

- Zero cross-test leakage of subscribers, rules, or correlation registrations.
- Guaranteed cleanup on test exit, including when a test raises.
- Support parallel scenarios on the shared Harness when correlation IDs round-trip.
- Graceful degradation to serial-per-service when they don't.

### Non-Goals

- Process-level isolation (xdist). Deferred.
- Topic namespacing (requires the service under test to support dynamic topic config; not generally available).
- Immutable event log for replay. Separate concern.

---

## Decision Drivers

- **Correctness first** — silent leakage is the single worst kind of flaky-test bug.
- **Parallelism** — sequential suites over 100 tests are too slow for pre-merge CI.
- **Composability** — isolation mechanisms need to layer cleanly (registry cleanup + correlation routing are orthogonal).
- **Graceful degradation** — not every service echoes correlation IDs back; the design must work even when some don't.
- **Debuggability** — when isolation fails, the failure mode must be visible, not silent.

---

## Considered Options

### Option 1: Correlation-ID routing only

**Description:** Every test generates a unique ID, injects it into outbound payloads, filters inbound by matching the ID. No separate scoping mechanism; subscribers are one global registry for the whole suite, with correlation-based filtering per-callback.

**Pros:**
- Maximum parallelism out of the box.
- One mechanism to reason about.

**Cons:**
- Depends entirely on the service echoing the ID. Some request types echo a reliable header; others rely on adapter-specific fields; for LBM protobuf it depends on the message schema.
- No cleanup mechanism when correlation fails — callbacks accumulate for the suite's life.
- No exception safety: a test that raises leaves its subscriber registered forever.
- Hard to reason about what a given callback is expecting.

### Option 2: Scoped subscriber / rule registry

**Description:** Each scenario enters an `async with scenario_scope(harness)` block. On entry it gets fresh subscriber slots, an empty rule list, and a unique correlation ID. On exit — normal or exception — all of them are torn down. No correlation filtering; tests run sequentially.

**Pros:**
- Guaranteed cleanup, including on exception.
- Clean exception semantics — a raising test doesn't pollute the next.
- Works regardless of whether correlation IDs round-trip.
- Scoping model is familiar from WireMock's JUnit rule and pytest fixtures.

**Cons:**
- Sequential only. No parallel speed-up.
- Correlation ID exists but is used only for logging, not routing.

### Option 3: Topic namespacing

**Description:** Every test gets a unique topic suffix. Tests publish to `events.created.<test-id>` and subscribe to `state.changed.<test-id>`.

**Pros:**
- Trivially parallel.
- No shared-state surface at all.

**Cons:**
- Requires the service under test to accept dynamic topic configuration at runtime. Many real-world platforms do not.
- Ruled out by reality.

### Option 4: Full process isolation

**Description:** Each test runs in a subprocess with its own Harness.

**Pros:**
- Perfect isolation.
- Survives catastrophic test failures without affecting peers.

**Cons:**
- Undoes the entire reason for [ADR-0001](0001-single-session-scoped-harness.md).
- Seconds per test for process + Harness setup.
- Shared stateful mocks across tests become impossible without an external coordinator.

### Option 5 (chosen): Option 2 as spine, Option 1 layered on top

**Description:** `scenario_scope` provides registry cleanup, exception safety, and correlation-ID generation. When running in parallel, correlation IDs disambiguate inbound messages between concurrent scopes. When correlation doesn't round-trip, fall back to serial execution (sharded per-service from [framework-design.md §7](../framework-design.md)).

**Pros:**
- Best of both: scope guarantees correctness, correlation routing enables parallelism.
- Composable: the two layers don't interact beyond the scope holding the correlation ID.
- Graceful: any test that can be parallelised will be; any that can't falls back cleanly.

**Cons:**
- Two mechanisms to learn and document. Mitigated by the scope being the user-facing primitive; correlation is transparent to test authors.

---

## Decision

**Chosen Option:** Option 5 — Scoped subscriber / rule registry (Option 2) as the correctness spine, with correlation-ID routing (Option 1) layered on top for parallelism.

### Rationale

- Correctness dominates. Option 1 alone leaks state on exception; Option 3 requires a capability the platform doesn't have; Option 4 discards the whole reuse design.
- Option 2's scope-based cleanup mirrors WireMock's JUnit rule, pytest fixtures, and Python's own `async with` primitives. Cleanup is deterministic and exception-safe because `__aexit__` runs on the stack-unwind path.
- Layering correlation routing on top lets us claim parallelism when the services under test echo IDs, and fall back to serial-per-service sharding when they don't.
- The two mechanisms interact at one point — scope teardown deregisters from `Dispatcher` — but otherwise operate on disjoint concerns (cleanup vs routing). The teardown race (late message arriving after scope exit) is addressed explicitly in Consequences.

---

## Consequences

### Positive

- Exception-safe test cleanup is the default, not something tests have to remember.
- Parallel scenarios on a shared Harness without false failures from cross-talk.
- Fall-back path (serial-per-service) handles the long tail of services that don't echo correlation IDs.
- One scope primitive (`async with harness.scenario(...)`) is the user-facing surface; correlation is transparent.

### Negative

- Scenario scope is mandatory. Tests can't just subscribe and forget; they must enter a scope. Documented as a required pattern.
- Correlation-ID parallelism depends on the service echoing IDs — a behavioural contract with the services under test. If they break it, parallelism regresses (but correctness doesn't).
- Two mechanisms in play; more surface area to explain than a single-mechanism design.

### Neutral

- The scope's teardown is the single most important code path for correctness. Framework-internal tests verify it: one scope raises inside `__aexit__`, one scope raises before `__aexit__`, one scope exits cleanly — in all three cases, Harness subscriber count returns to the pre-scope baseline.
- **Late-arriving messages after scope exit.** A message with a correlation ID for a scope that has already torn down arrives at the dispatcher. This is distinct from a "never-registered" correlation. The surprise log classifies the two cases separately (`timeout_race` vs `unknown_scope`); only the latter triggers a warning.
- **Concurrent teardown under `asyncio.gather`.** If two parallel scopes exit simultaneously and one raises, `asyncio.CancelledError` may interrupt the other's teardown. Teardown must be idempotent and must not depend on being the only caller.
- Shared state across scopes still lives in the Harness and any stateful mocks (session-level state, sequence numbers, transport logon). Those are managed at a coarser granularity (session boundaries, not scenario boundaries). Credential handling is tracked separately in [ADR-0001](0001-single-session-scoped-harness.md) Goals.

### Security Considerations

The scope boundary is a trust boundary in this harness: data from scenario A must not reach scenario B. Specific exposures to watch:

- A test introspecting `harness._fix_mock.session` bypasses the scope boundary. See [ADR-0001](0001-single-session-scoped-harness.md) on credential redaction and write-only accessors.
- The surprise log records unmatched inbound — payloads may contain trade data (ISINs, LEIs, P&L). Default-redact with a per-topic allowlist. Tracked separately as a future ADR on log-content classification; flagged in README.
- Timeout tracebacks from a failing scope include `repr(future)` which can leak payload content to CI logs. Expectation and message objects implement a redacting `__repr__`. Owner: Platform / Test Infrastructure.

---

## Implementation

The DSL and lifecycle live in [PRD-002](../prd/PRD-002-scenario-dsl.md). This ADR commits the framework to the mechanism; the DSL surfaces it.

1. `ScenarioScope` implements `__aenter__` / `__aexit__` and tracks registries: subscribers, rules, scope-registration (Dispatcher).
2. On `__aenter__`: allocate a cryptographically-random correlation ID (UUIDv4 via `secrets.token_hex` or `uuid.uuid4`); register the scope with `Dispatcher`; clear all registries.
3. Scenario operations (`expect`, `on`, and similar) add entries to the registries.
4. On `__aexit__`: drain registries in reverse order; unregister from `Dispatcher`; classify leftover unmatched inbound for this correlation as `timeout_race` (the scope raised `TimeoutError` while messages were still arriving) and log at INFO, not WARNING.
5. Teardown is **idempotent** and **reentrant-safe**: `__aexit__` may be called more than once (e.g. from `asyncio.gather` cancellation unwind) without corrupting state.
6. Parallel scenarios run concurrently via `asyncio.gather` or equivalent; each has its own scope and correlation ID.
7. Tests that need the serial fallback opt in via pytest mark. The mark is introduced by a later PRD (not yet written — status tracked in the docs/prd/ README). Until then, tests that can't echo correlation IDs run strictly sequentially.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (DSL in [PRD-002](../prd/PRD-002-scenario-dsl.md)): 1 week after PRD-001 lands.
- Phase 2 (parallel execution harness): 2 weeks after Phase 1.

---

## Validation

How will we know this decision was correct?

### Success Metrics

- **Cross-test leakage incidents:** zero reported in the first 90 days.
- **Exception teardown correctness:** 100% of scope exits clean up registries, verified by a residue check in CI.
- **Parallel speedup:** 100-test suite in under 60s (from PRD-001 target).
- **Graceful degradation:** tests that don't echo correlation IDs automatically run serial-per-service without author intervention.

### Monitoring

- Framework-internal test: every scenario exit asserts `harness.active_subscriptions_for_scope(scope) == 0`.
- CI runs a dedicated "isolation" check that launches 10 parallel scenarios, all publishing on the same topic, and asserts each gets back only its own message.
- "Surprise log" — unmatched inbound by correlation ID — exposed at session end for anomaly detection.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single session-scoped Harness (this decision is what makes that one safe).
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher as the routing mechanism that implements correlation-ID dispatch.
- [ADR-0005](0005-pytest-asyncio-session-loop.md) — Session-scoped event loop (a prerequisite for running parallel scopes on one Harness).
- [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md) — The user-facing DSL that exposes `scenario_scope`.

---

## References

- [framework-design.md §4](../framework-design.md) — Isolation layers and mechanisms
- [context.md §6](../context.md) — Correlation-ID parallelism and serial-per-service fallback
- WireMock JUnit rule reset-per-test pattern — https://wiremock.org/docs/junit-jupiter/
- Meszaros, *xUnit Test Patterns* — Fresh Fixture vs Shared Fixture trade-offs

---

## Notes

The serial-per-service fallback isn't free — it still requires services to be classifiable by a `@pytest.mark.service(name)` mark or similar. That mark is introduced in a yet-to-be-written CI-pipeline PRD. Until then, tests that can't echo correlations run strictly sequentially.

**Open follow-ups:**

- **Verify correlation echo for each downstream service surface** before the ADR can move to Accepted. Confirm at least the chosen transport-level correlation header and any protobuf internal correlation field (field name TBC per PRD-001 Open Question #2). **Owner:** Platform.
- **Introduce the `@pytest.mark.service` mark** so the fallback is actually available. **Owner:** Platform / Test Infrastructure.
- **Concurrent-teardown race semantics under `asyncio.gather`** — document the idempotency requirement in framework-internal tests. **Owner:** Framework maintainers.

**Last Updated:** 2026-04-17
