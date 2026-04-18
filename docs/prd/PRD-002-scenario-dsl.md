# Scenario DSL — Type-state Builder, Handles, Timeouts — Product Requirements Document

**Status:** Draft
**Created:** 2026-04-17
**Last Updated:** 2026-04-17
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering

---

## Executive Summary

Give test authors a fluent, type-safe DSL for writing integration tests against a message-driven platform. Structural enforcement of expect-before-publish eliminates the single most common class of flaky integration-test bugs, while a handle-based result model lets tests make precise per-expectation assertions on content and timing.

---

## Problem Statement

### Current State

No DSL exists. Without one, tests would be written by hand against the Harness API (PRD-001): manual subscriber registration, raw `asyncio.Future` creation, manual correlation-ID injection, ad-hoc cleanup. Every test would reinvent the same boilerplate.

### User Pain Points

- **Race conditions from publishing before subscribers are ready.** In hand-rolled tests this is the single most common flakiness source in async integration testing.
- **Boilerplate drift.** Two authors set up subscribers two different ways → inconsistent cleanup → tests that pass locally and fail in CI.
- **No idiomatic way to assert on timing.** Tests either don't check timing (missing real bugs) or write their own latency measurement harness (inconsistent across tests).
- **Cleanup on exception is easy to forget.** A test that raises mid-execution leaves subscribers registered, polluting the next test.

### Business Impact

- Flaky tests undermine trust in the suite. They get marked `@skip` or `@flaky(retry=3)`. Either way, real coverage degrades.
- Boilerplate makes tests long and hard to review, slowing PR cycles.
- Inconsistent patterns make onboarding harder and multiply maintenance cost.

---

## Goals and Objectives

### Primary Goals

1. **Structural enforcement of subscribe-before-publish.** Impossible to call `publish()` without first registering at least one `expect()`.
2. **One canonical scenario shape** every test follows, cleaning up guaranteed on exception.
3. **Per-expectation handles** so tests can assert on specific messages (content, timing, latency) not just "all passed".
4. **Deadline-based timeouts** via `asyncio.timeout_at`, yielding all timeouts in one result set rather than failing on the first.

### Success Metrics

- Zero tests merged to main that publish before expecting, because the type-checker rejects them.
- 100% of scenarios clean up subscribers and rules on exit, including on exception.
- Test author can write a working scenario in **under 15 lines** for the common case (one expect, one publish, one assertion).
- All timeouts in a scenario reported together in the failure; no "first failure masks the others".

### Non-Goals

- **Transport implementation.** PRD-001.
- **Stateful fake adapters.** Out of scope here; each consumer composes its own.
- **Parallel execution across processes.** Deferred (xdist); this PRD targets asyncio-level parallelism inside one process.

---

## User Stories

### Primary User Stories

**As a** test author,
**I want to** write `s.publish()` only after at least one `s.expect()`,
**So that** I cannot accidentally introduce race conditions by publishing before subscribers are registered.

**Acceptance Criteria:**
- [ ] Calling `publish()` on a `ScenarioBuilder` (no expectations yet) is a type error, caught by mypy / pyright.
- [ ] The runtime equivalent (e.g. via dynamic call) raises a clear `NoExpectationsRegisteredError` with the suggested fix.
- [ ] IDE autocomplete on `ScenarioBuilder` does not list `publish`.

---

**As a** test author,
**I want to** get a handle from every `expect()` call,
**So that** I can assert on the specific message that matched, including its timing.

**Acceptance Criteria:**
- [ ] `handle = s.expect(...)` returns an object with `was_fulfilled() -> bool`, `message -> ReceivedMessage`, `latency_ms -> float`, `outcome -> Outcome`.
- [ ] After `await_all()`, each handle reflects the outcome of its expectation independently.
- [ ] Handles survive scenario teardown so post-scope assertions are valid.

---

**As a** test author,
**I want to** specify a scenario-wide timeout,
**So that** a stuck service doesn't freeze the suite indefinitely.

**Acceptance Criteria:**
- [ ] `await s.await_all(timeout_ms=500)` enforces a 500ms deadline across all pending expectations.
- [ ] Expectations that didn't fire by the deadline are reported as `TIMEOUT` with the reason and the absolute deadline crossed.
- [ ] The deadline is absolute, not per-expectation — first expectation fires fast, remaining get the remaining budget.

---

**As a** framework maintainer,
**I want to** guarantee subscriber / rule cleanup on scope exit,
**So that** I never have to debug "test A pollutes test B" class of flakes.

**Acceptance Criteria:**
- [ ] Scope is an `async with` context manager; `__aexit__` unregisters all subscribers and rules registered within the scope.
- [ ] Cleanup runs even if the test raises inside the `async with` block.
- [ ] Post-cleanup verification is available (assert no residual state).

---

## Proposed Solution

### High-Level Approach

The DSL is a **type-state fluent builder** with four distinct states, each exposing only the methods legal at that stage:

```
ScenarioBuilder
    ↓ .with_correlation() / .expect() / .on()
ExpectingScenario
    ↓ .publish()
TriggeredScenario
    ↓ .await_all()
ScenarioResult
```

Each state transition returns a new object of the next type. `publish()` does not exist on `ScenarioBuilder`, so calling it before any `expect()` is a static error. `await_all()` only exists on `TriggeredScenario`, so calling it before `publish()` is likewise impossible.

Every `expect*()` call returns a `Handle` — an opaque reference to the Future behind the expectation. Tests can read the handle after `await_all()` to make per-expectation assertions.

Timeouts use `asyncio.timeout_at(deadline)` at the outermost level and `asyncio.wait(..., return_when=ALL_COMPLETED)` inside, so all pending futures resolve (or timeout) together and all timeouts surface in the result.

### User Experience

**Common case (single expect, single publish):**

```
async with harness.scenario("risk check passes") as s:
    handle = s.expect("risk.passed", Matcher.field_equals("decision", "PASS"))
    s = s.publish("risk.inbound", fixtures.load_json("valid_order.json"))
    result = await s.await_all(timeout_ms=500)
    assert result.passed
    assert handle.latency_ms < 100
```

**Multiple expectations with handles:**

```
async with harness.scenario("event fan-out") as s:
    h_validated = s.expect("events.validated", Matcher.field_equals("status", "PASS"))
    h_state     = s.expect("state.changed",    Matcher.field_gt("count", 0))
    h_recorded  = s.expect("events.recorded",  Matcher.field_exists("record_id"))

    s = s.publish("events.submitted", fixtures.load_json("valid_event.json"))

    result = await s.await_all(timeout_ms=1000)
    assert h_validated.was_fulfilled()
    assert h_state.latency_ms < h_recorded.latency_ms   # state change lands before recording
```

**Invalid scenarios rejected at the type-checker:**

```
async with harness.scenario("bad") as s:
    s = s.publish("topic", payload)   # type error: ScenarioBuilder has no .publish
```

### Key Components

- **ScenarioBuilder / ExpectingScenario / TriggeredScenario / ScenarioResult** — four state classes, each exposing only legal next calls.
- **Handle** — opaque reference to one expectation's Future + metadata.
- **Expectation** — internal record: future, matcher, correlation ID, topic, deadline.
- **Matcher (Strategy)** — pluggable predicates: `field_equals`, `field_in`, `field_gt`, `group_contains`, `all_of`, `any_of`, `payload_contains`.
- **Scope lifecycle** — `async with harness.scenario(name)` boots a scope; `__aexit__` unregisters everything.

---

## Scope

### In Scope

- Four state classes with type-state transitions.
- `Handle` with `was_fulfilled`, `message`, `latency_ms`, `outcome`.
- `Matcher` interface + the composable matchers listed above.
- `scenario(name)` factory on `Harness`; returns a `ScenarioBuilder` inside a scope.
- `with_correlation(field_name)` and `without_correlation()`.
- `expect(topic, matcher)` for inbound expectations on the configured transport.
- `publish(topic, payload)`.
- `await_all(timeout_ms)` with deadline-based semantics.
- `reopen_for_expect()` on `TriggeredScenario` for the rare case of staging follow-up expectations after a publish.
- Scenario cleanup on `__aexit__` — subscribers unregistered, rules cleared, correlation ID released.
- Scenario-wide deadline using `asyncio.timeout_at`.
- Result types: `ExpectationResult`, `ScenarioResult`, `Handle`, `Outcome` (PASS / FAIL / TIMEOUT).

### Out of Scope

- Stateful fake adapter behaviour — out of scope here; each consumer composes its own.
- Parallel scenario orchestration — covered by Harness / Dispatcher (PRD-001).
- Suite-level reporting (`SuiteResult`) — covered by PRD-007.

### Future Considerations

- Conditional expectations (`expect_one_of(m1, m2, m3)`) for scenarios where the system may reply in more than one valid way.
- Replay from capture — load a captured scenario, play it back, compare actual vs expected.

---

## Requirements

### Functional Requirements

**Must Have (P0):**
- [ ] `Harness.scenario(name)` returns a `ScenarioBuilder` wrapped in an `async with` scope.
- [ ] `ScenarioBuilder` exposes `with_correlation`, `expect`, `on`, but NOT `publish` or `await_all`.
- [ ] `.expect*()` returns `ExpectingScenario` + a `Handle`.
- [ ] `ExpectingScenario` exposes `expect*`, `publish` (new), but still not `await_all`.
- [ ] `.publish()` returns `TriggeredScenario`.
- [ ] `TriggeredScenario` exposes `publish` (chainable), `await_all`, `reopen_for_expect`.
- [ ] `.await_all(timeout_ms)` returns `ScenarioResult`, populates all `Handle`s.
- [ ] `Handle` provides `was_fulfilled() -> bool`, `message -> ReceivedMessage | None`, `latency_ms -> float`, `outcome -> Outcome`.
- [ ] Matchers: `field_equals`, `field_in`, `field_gt`, `field_exists`, `group_contains`, `all_of`, `any_of`, `payload_contains`.
- [ ] Scope `__aexit__` unregisters all subscribers, clears staged rules, removes scope from `Dispatcher`.
- [ ] Cleanup runs on exception (verified by tests that raise inside the scope).
- [ ] Deadline-based timeout using `asyncio.timeout_at`; all pending futures collected via `asyncio.wait(return_when=ALL_COMPLETED)`.
- [ ] Timeouts reported per expectation, not as one scenario-level failure.

**Should Have (P1):**
- [ ] `.within(ms)` on an individual expectation to tighten budget below scenario deadline.
- [ ] Matchers print themselves in failure messages (for debuggability).
- [ ] Scenario result's `summary()` method produces a human-readable report.

**Nice to Have (P2):**
- [ ] `@scenario` decorator as an alternative entry point to `async with`.
- [ ] VS Code / IDE snippets for the common scenario shape.

### Non-Functional Requirements

**Performance:**
- DSL overhead per expectation: **<100μs** (negligible vs transport cost).
- No hidden O(n²) behaviour as expectations grow (matcher lookup via dict/list is acceptable).

**Reliability:**
- 100% of scenarios clean up on `__aexit__`, including on exception. Verified by per-scope residue check.
- Type-state violations caught by mypy strict mode.

**Observability:**
- Each expectation logs its lifecycle: registered → matched / timed out, with latency.
- `ScenarioResult.summary()` includes latencies, outcomes, and matcher descriptions on failures.

**Compatibility:**
- Python 3.11+ for `asyncio.timeout_at` + `asyncio.TaskGroup`.
- Compatible with pytest 8.x + pytest-asyncio 0.24+ (session-scope loop).

---

## Dependencies

### Internal Dependencies

- **PRD-001 (Harness + Dispatcher)** — the DSL delegates to Harness for subscribe/publish and to Dispatcher for correlation routing.

### External Dependencies

- Python 3.11 runtime.
- `asyncio` (stdlib).
- Optional: typing helpers like `typing.Protocol` for the Strategy matchers.

---

## Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Type-state is awkward for novice Python users | Medium | Medium | Docs + examples. The fluent API is familiar from libraries like boto3's resource API and is well-accepted. |
| Chained reassignment (`s = s.expect(...)`) is verbose | Low | High | Acceptable trade-off for safety. Mitigate with examples and optional single-expression composition. |
| `asyncio.timeout_at` requires 3.11+ | Low | Low | Pin Python 3.11 (already implied by framework-design.md §5). |
| Handle references held across scope exit leak memory | Low | Medium | Handles are dataclasses holding finished results, not live Futures. No leak by design. |
| Missing expectation crash-loops the scenario | Low | Low | Deadline enforcement guarantees bounded time. Timeouts logged, not retried. |

---

## Alternatives Considered

### Alternative 1: Runtime state check

**Description:** One `Scenario` class with a state field; `publish()` checks the state at runtime.

**Why not chosen:**
- Bugs only surface at runtime, not at authoring time. Defeats the main goal.

### Alternative 2: Single-expression builder (no reassignment)

**Description:** Methods mutate the scenario in place and return `self`, so `s.expect(...).publish(...)` works without reassignment.

**Why not chosen:**
- Mutates state invisibly; reassignment makes the type flow visible and prevents dangling references.
- Prevents type-state enforcement: a `self`-returning method can't narrow types.

### Alternative 3: Callback-based API

**Description:** `harness.scenario("name", setup=lambda s: s.expect(...).publish(...))`.

**Why not chosen:**
- Harder to read for complex scenarios.
- Error lines point into the lambda, not the test — worse stack traces.

---

## Open Questions

1. **Exact Handle API surface.** Should handles expose raw `asyncio.Future` access or only the resolved-value accessors? Recommending the latter (clean encapsulation) but confirming with authors.
   - **Status:** Open
   - **Owner:** Framework

2. **Matcher descriptions in failures.** Should matchers carry a human-readable description string, or auto-derive from the predicate? Trade-off: descriptiveness vs verbosity.
   - **Status:** Open
   - **Owner:** Framework

3. **Expression composition for parallel suites.** `harness.run_parallel([scenario1, scenario2, ...])` — is this a DSL-level API or a pytest-level one?
   - **Status:** Open — tentatively pytest-level via fixtures
   - **Owner:** Framework

---

## Timeline and Milestones

### Phases

**Phase 1: Core state machine + matchers** (Target: +1 week after PRD-001 lands)
- Four state classes, expect/publish/await_all transitions
- Basic matchers (field_equals, field_in, field_gt, field_exists, all_of, any_of)
- Handle type
- asyncio.timeout_at integration
- Unit tests using MockTransport

**Phase 2: Scope lifecycle + cleanup** (Target: +1 week after Phase 1)
- `async with` scope with guaranteed teardown
- Scenario result types + `summary()`
- Residue assertions (no subscribers leaked)

**Phase 3: Extensions** (Target: +1 week after Phase 2)
- `within(ms)` per-expectation budget
- `reopen_for_expect()`

### Key Milestones

- [ ] PRD Approved: TBD
- [ ] ADR Created (type-state vs runtime check): TBD
- [ ] Implementation Started: TBD
- [ ] First round-trip scenario passes with MockTransport: TBD
- [ ] mypy strict check confirms type-state enforcement: TBD

---

## Appendix

### Related Documents

- [framework-design.md](../framework-design.md) §4 (test isolation), §5 (timeouts), §8 (expect-before-publish)
- [context.md](../context.md) §5 (DSL design principles)
- [PRD-001 — Framework foundations](PRD-001-framework-foundations.md)

### References

- Meszaros, *xUnit Test Patterns* — canonical reference for test doubles and scenario structure
- asyncio `timeout_at` docs — https://docs.python.org/3/library/asyncio-task.html#asyncio.timeout_at
- WireMock scenarios (inspiration for handle-based assertions) — https://wiremock.org/docs/stateful-behaviour/

### Glossary

- **Type-state** — A class's method surface depends on its state; illegal transitions caught at compile time.
- **Handle** — Opaque reference to a single expectation's resolved result plus metadata.
- **Scope** — `async with` context holding all resources for one scenario; cleaned up on exit.
- **Outcome** — Enum: PASS / FAIL / TIMEOUT.
- **Expectation** — One registered "wait for this" on the scope.

---

**Approval Signatures:**

- [ ] Product Owner: _________________ Date: _______
- [ ] Technical Lead: _________________ Date: _______
- [ ] Security Review: _________________ Date: _______
