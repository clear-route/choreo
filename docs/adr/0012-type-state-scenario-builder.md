# 0012. Type-State Scenario Builder for Expect-Before-Publish Enforcement

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md) §Primary Goals item 1

---

## Context

Race conditions between publish and subscribe are the single most common class of flaky integration-test bug in async frameworks. A test that calls `publish("orders.approved", ...)` before registering `expect("orders.booked", ...)` misses the response message, then blocks on a future that will never resolve, and times out minutes later with an opaque error.

The convention "subscribe before publish" is documented in [context.md §5](../context.md) and [framework-design.md §8](../framework-design.md). A convention is not an enforcement — every team grows a contributor who forgets. This ADR records the decision to enforce expect-before-publish **structurally** so that publishing before expecting is not a runtime mistake but a type error surfaced at author time.

### Background

- [framework-design.md §8](../framework-design.md) proposes the type-state option under "Options".
- [PRD-002 §User Story 1](../prd/PRD-002-scenario-dsl.md) lists "type-checker rejects publish-before-expect" as a P0 acceptance criterion.
- Python does not have proper algebraic types, but method-narrowing via return-type annotations is enough for mypy / pyright to reject illegal transitions.
- Similar patterns exist in production libraries: boto3's paginators, SQLAlchemy's query builders, Rust's typestate crate.

### Problem Statement

How do we make "publish before expect" structurally impossible rather than a runtime check?

### Goals

- **Static rejection.** mypy / pyright flag `scenario().publish(...)` as a type error before the test runs.
- **IDE signal.** Autocomplete on a fresh scenario does not offer `publish` or `await_all`.
- **Runtime fallback.** Dynamic callers (e.g. via `getattr`) still hit a clear `NoExpectationsRegisteredError` if they bypass the type system.
- **Single canonical shape.** All scenarios follow the same `expect* → publish → await_all` chain; no alternative orderings.
- **Exception safety.** Cleanup runs on every exit path (scope is `async with`).

### Non-Goals

- Preventing every possible misuse (e.g. calling `expect()` inside a subprocess). Covers the documented API only.
- Supporting arbitrary re-orderings. `reopen_for_expect()` is the one escape hatch; see §Rationale.
- Non-pytest test runners. Optimised for pytest-asyncio but not tied to it.

---

## Decision Drivers

- **Correctness over convenience.** A structural guarantee is worth a small amount of type-annotation boilerplate.
- **Feedback speed.** Catching misuse at author time beats catching it after a flaky CI run.
- **Cognitive load.** One canonical shape that every scenario follows.
- **Python idiomatic.** Matches how other Python async libraries structure lifecycle state.

---

## Considered Options

### Option 1: Runtime state flag

**Description:** One `Scenario` class with an internal state field. `publish()` checks "any expectations registered?" at runtime.

**Pros:**
- Simplest implementation.
- No extra classes.

**Cons:**
- Error only surfaces when tests run, not when they are written or reviewed.
- IDE autocomplete shows all methods regardless of state, offering invalid combinations.
- A flaky CI run is the first signal; the fix is a rework, not a one-character change in the editor.

### Option 2: Type-state with four distinct classes (chosen)

**Description:** Four classes — `ScenarioBuilder`, `ExpectingScenario`, `TriggeredScenario`, `ScenarioResult` — each exposing only methods legal for that stage. Each method returns the next class in the chain.

**Pros:**
- `publish` literally does not exist on `ScenarioBuilder`; calling it is an `AttributeError` in the editor's type checker.
- IDE autocomplete after `scenario()` shows only `expect`, `on`, `with_correlation`.
- Transitions are visible: `s = s.expect(...)` makes intent explicit.
- Maps 1:1 to the PRD-002 narrative.

**Cons:**
- Four classes where one would do. Readers have to learn the shape.
- Reassignment (`s = s.expect(...)`) is verbose compared to fluent self-return.
- Requires mypy / pyright discipline to enforce types; untyped callers still bypass.

### Option 3: Single-expression fluent builder

**Description:** Methods return `self`. `s.expect(...).publish(...)` in one expression.

**Pros:**
- Compact.
- Familiar to jQuery / builder-pattern users.

**Cons:**
- `self`-return cannot narrow types, so every method is available at every stage — same failure as Option 1.
- Reassignment disappears, but so does the opportunity to catch misuse.

### Option 4: Callback-based API

**Description:** `bus.scenario("name", setup=lambda s: s.expect(...).publish(...))`.

**Pros:**
- Can enforce ordering by only passing `ExpectingScenario`-like objects into the lambda.

**Cons:**
- Stack traces point into the lambda; poor diagnostics.
- Awkward for multi-step scenarios.
- Harder to read for complex tests.

---

## Decision

**Chosen Option:** Option 2 — Type-state with four distinct classes.

### Rationale

- The correctness goal is non-negotiable; Option 1's runtime check is strictly worse than a type-checked structural guarantee.
- Python's type system is expressive enough for the transitions we need (methods returning narrower types).
- The reassignment cost (`s = s.expect(...)`) is small and makes intent visible, which is a feature, not a cost.
- Same pattern is used in well-understood Python libraries (boto3 paginators, SQLAlchemy query builders); not novel.
- Option 3 is Option 1 with extra syntax; Option 4 has worse diagnostics.

### The four states

```
                ┌──────────────────┐
                │ ScenarioBuilder  │   entry point; no publish, no await
                ├──────────────────┤
                │ .with_correlation│
                │ .expect          │
                │ .on              │
                └─────────┬────────┘
                          │ .expect / .on
                          ▼
                ┌────────────────────┐
                │ ExpectingScenario  │   has registered at least one expectation
                ├────────────────────┤
                │ .expect            │
                │ .on                │
                │ .publish  ← only here
                └─────────┬──────────┘
                          │ .publish
                          ▼
                ┌────────────────────┐
                │ TriggeredScenario  │   the trigger has fired
                ├────────────────────┤
                │ .publish  (more)   │
                │ .reopen_for_expect │   escape hatch — see Notes
                │ .await_all  ← only here
                └─────────┬──────────┘
                          │ .await_all
                          ▼
                ┌────────────────────┐
                │ ScenarioResult     │   immutable outcome
                ├────────────────────┤
                │ .passed            │
                │ .handles           │
                │ .summary()         │
                └────────────────────┘
```

---

## Consequences

### Positive

- `s.publish(...)` before any `s.expect(...)` is a type error flagged by mypy / pyright; the author sees it before running the test.
- IDE autocomplete on each state shows exactly the legal next calls — the DSL teaches itself.
- Exception safety: the whole chain lives inside an `async with` scope; teardown runs on every exit path.
- Runtime diagnostics: if an untyped caller dynamically invokes `publish` on a `ScenarioBuilder`, the resulting `AttributeError` message is clear.
- One canonical scenario shape across the codebase.

### Negative

- Four classes to document; more API surface than a single-class alternative.
- `s = s.expect(...)` reassignment is verbose. Acceptable trade-off for visible state transitions.
- Requires consumers to run mypy / pyright to get the static guarantee. Projects without type-checking pay only runtime enforcement.
- `reopen_for_expect()` is an escape hatch; overuse would undermine the structural guarantee. Documented carefully.

### Neutral

- Handle objects returned by `expect*()` methods live across state transitions (see [ADR-0014](0014-handle-result-model.md)).
- Scenario scope (`async with bus.scenario(...)`) is the lifetime boundary; transitions happen inside the scope.

### Security Considerations

N/A — this decision is about correctness of test authoring, not about trust boundaries or data handling.

---

## Implementation

1. **Four small classes** in `core.scenario` module:
   - `ScenarioBuilder` — constructor called by `harness.scenario(name)`.
   - `ExpectingScenario` — returned by `ScenarioBuilder.expect*()`.
   - `TriggeredScenario` — returned by `ExpectingScenario.publish()`.
   - `ScenarioResult` — returned by `TriggeredScenario.await_all()`.
2. **Type annotations on every method** — return types narrow to the next state class.
3. **No method re-declared across state classes except the ones that return the same state** (e.g. `ExpectingScenario.expect` returns `ExpectingScenario`, `TriggeredScenario.publish` returns `TriggeredScenario`).
4. **Shared state** via an internal `_ScenarioContext` dataclass that all four states hold a reference to. State classes are thin wrappers over the context, providing only the method projection.
5. **`reopen_for_expect()`** on `TriggeredScenario` returns an `ExpectingScenario`. Documented as the only legitimate back-edge; used when a test needs to stage a follow-up expectation after seeing the first publish's response.
6. **Exception path:** all four states live inside `async with bus.scenario(...)`; `__aexit__` on the scope triggers teardown regardless of which state the user was in at the time.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with PRD-002 implementation): all four classes, expect + publish + await_all transitions, basic Matchers.
- Phase 2: `reopen_for_expect()`, full Matcher set.
- Phase 3: `.within(ms)` per-expectation budget overrides; polish.

---

## Validation

### Success Metrics

- **Zero tests merged to main that publish before expecting.** Enforced by mypy / pyright running in CI on the test directory.
- **IDE autocomplete offers only legal calls** on each state. Verified by a developer-experience smoke check.
- **Runtime `AttributeError` for dynamic callers** when they invoke `publish` on a `ScenarioBuilder`. Confirmed by a framework-internal test.
- **`reopen_for_expect()` usage stays rare.** Count occurrences quarterly; if >5% of scenarios use it, investigate whether the main chain is missing something.

### Monitoring

- CI runs mypy strict mode on `tests/`; any type error fails the build.
- Framework-internal test catches dynamic `getattr` attempts that bypass types.
- Lint rule: importing `reopen_for_expect` outside a small allowlist of tests requires a justification in the PR.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scoped registry + correlation IDs (the scope lifecycle this DSL lives inside).
- [ADR-0013](0013-matcher-strategy-pattern.md) — Matcher Strategy (the predicates `expect*()` accepts).
- [ADR-0014](0014-handle-result-model.md) — Handle result model (what `expect*()` returns).
- [ADR-0015](0015-deadline-based-scenario-timeouts.md) — Deadline model (how `await_all` budgets time).

---

## References

- [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md)
- [framework-design.md §8](../framework-design.md) — Expect-before-publish options
- Rust typestate pattern — academic reference, same idea applied to Python's weaker type system
- boto3 resource collections — precedent for state narrowing in Python

---

## Notes

### Correction — 2026-04-17: implemented as single class with runtime state flag

The four-class shape in §Considered Options / Option 2 turned out to contradict [ADR-0014](0014-handle-result-model.md), which commits `expect*()` to return a `Handle`. Having `expect*()` return both a `Handle` (for per-expectation assertions) *and* the next state class (for method narrowing) requires either a tuple return (`s, h = s.expect(...)`) or a mutation-in-place pattern that cannot be expressed in Python's type system.

The implementation lands on a pragmatic compromise:

- **One `Scenario` class** with a `_state` flag (`builder | expecting | triggered`).
- `expect(topic, matcher) -> Handle` — returns the Handle, advances state to `expecting`.
- `publish(topic, payload) -> Scenario` — raises `AttributeError` in `builder` state, advances state to `triggered`, returns `self`.
- `await_all(*, timeout_ms) -> ScenarioResult` — raises `AttributeError` when not in `triggered` state.

The `AttributeError` on illegal transitions is raised from a state-checking `@property` getter, so the error surface matches what a missing attribute would produce. All PRD-002 acceptance criteria are satisfied — invalid transitions still raise `AttributeError` — but the *enforcement is runtime, not compile-time*.

What we lost vs Option 2:

- mypy / pyright cannot reject `s.publish(...)` on a fresh scenario at type-check time. The failure surfaces when the test runs (as an `AttributeError`) rather than when the test is written.
- IDE autocomplete shows `publish` and `await_all` on the same object regardless of state; users see the error only when they call them.

What we kept:

- `AttributeError` at runtime when `publish` is called before any `expect*()`.
- `AttributeError` at runtime when `await_all` is called before `publish`.
- `AttributeError` at runtime when `expect` is called after `publish`.
- Exception-safe scope cleanup.
- Matches the PRD-002 example shape exactly: `handle = s.expect(...)` plus `s = s.publish(...)` plus `result = await s.await_all(...)`.

This is a deliberate correction to the ADR. If a future decision wants stronger static enforcement, the options remain: tuple-returning `expect()` with destructuring, or a separate `next_state` helper. Both are at the cost of a less natural test-author API.

### Open follow-ups

- **Static enforcement revisit.** If false-positive rate on `AttributeError` in tests becomes a problem, revisit tuple-return destructuring. Three candidate syntaxes noted in the implementation's docstring. **Owner:** Platform.
- **`reopen_for_expect()` semantics.** Does reopening clear the existing handles or extend them? Current implementation does not yet include this — add when a scenario requires it in practice. Current thinking: extend. **Owner:** Framework maintainers.

**Last Updated:** 2026-04-17
