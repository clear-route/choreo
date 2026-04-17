# 0015. Deadline-Based Scenario Timeouts via `asyncio.timeout_at`

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md) §User Story 3

---

## Context

A scenario waits for multiple expectations to resolve, any of which could hang if the service under test is stuck. The framework needs a reliable, bounded way to say "give up after N ms and report what did not fire". The decision is what primitive to use, how it interacts with `asyncio.wait`, and how timeouts surface as test results.

`asyncio` offers several mechanisms; they differ in subtle ways. This ADR records the choice of **`asyncio.timeout_at(deadline)` as the outer guard with `asyncio.wait(return_when=ALL_COMPLETED)` inside**, producing a deadline-based scenario-wide timeout that collects all results rather than failing fast on the first pending future.

### Background

- Python 3.11 introduced `asyncio.timeout()` / `asyncio.timeout_at()` as the idiomatic context-manager-based timeout.
- [ADR-0005 §Related](0005-pytest-asyncio-session-loop.md) locks Python to 3.11+.
- [framework-design.md §5](../framework-design.md) weighed the options and recommended this shape.
- `pytest-timeout` is a common test-framework plugin, but per [framework-design.md §5](../framework-design.md) it is unsafe for asyncio (SIGALRM interferes with the loop; thread mode kills the loop thread).

### Problem Statement

What primitive does `await_all(timeout_ms=N)` use to enforce an absolute deadline across several pending expectations, and how do deadline-exceeded expectations surface in the result?

### Goals

- **Absolute deadline, not per-expectation.** All expectations share one wall-clock budget; first expectation's latency eats into later expectations' budget.
- **Collect, don't abort.** When the deadline fires, every pending expectation becomes a `TIMEOUT` outcome — we do not throw away results from expectations that fired before the deadline.
- **Cancellation is clean.** Unresolved Futures are cancelled, not orphaned.
- **Deterministic reporting.** A timeout test reports the same result shape every run — which expectations fired, which timed out, how far over the deadline.
- **No pytest-timeout.** That plugin is unsafe for asyncio; we do not use it.

### Non-Goals

- Per-expectation timeouts. A future extension `.within(ms)` can tighten individual budgets below the scenario deadline; not in this ADR.
- Wall-clock accuracy beyond asyncio loop resolution. `asyncio.timeout_at` is millisecond-accurate; that is enough.
- Timeouts on stages of the scenario lifecycle (construction, publish). This ADR covers `await_all()` only.

---

## Decision Drivers

- **Idiomatic Python.** Use the stdlib's timeout primitive unless there's a reason not to.
- **All-or-nothing reporting.** A scenario that partially fires and then times out is a specific and diagnosable failure mode; we must surface which expectations fired and which did not.
- **Cancellation safety.** Pending coroutines must stop; no leaked tasks after timeout.
- **Python 3.11+ baseline.** We have committed to 3.11+ already (ADR-0005).

---

## Considered Options

### Option 1: `asyncio.timeout_at(deadline)` outer + `asyncio.wait(ALL_COMPLETED)` inner (chosen)

**Description:** Compute deadline at scenario entry. Inside `await_all()`, run `asyncio.wait([f for f in futures], return_when=ALL_COMPLETED)` inside `async with asyncio.timeout_at(deadline)`. On timeout, the outer `asyncio.TimeoutError` is caught, pending futures are cancelled, each Handle gets `TIMEOUT`.

**Pros:**
- Idiomatic Python 3.11+.
- Context-manager semantics — outer `async with` makes the budget visible in the code.
- Deadline is absolute; pass through budgets naturally via `timeout_at(deadline)`.
- Cancellation is automatic — `asyncio.wait` cancels pending on context-manager exit.
- All resolved expectations are kept; timed-out ones become `TIMEOUT`.

**Cons:**
- Requires Python 3.11+. Already committed.
- Two-layer structure (`async with` outer, `await asyncio.wait` inner). Slightly verbose.

### Option 2: `asyncio.wait_for(asyncio.gather(...), timeout)`

**Description:** Use `wait_for` on a gathered future.

**Pros:**
- One-line.

**Cons:**
- `gather` with `return_exceptions=False` raises on first exception — kills the "collect all results" requirement.
- `gather` with `return_exceptions=True` swallows exceptions rather than surfacing them per-handle.
- `wait_for` cancels the whole gather on timeout; individual results already completed inside the gather get lost.

### Option 3: Shared `asyncio.Event` with deadline

**Description:** Each expectation resolution sets part of a counter; a shared event fires when all resolve or the deadline fires.

**Pros:**
- Explicit state machine.

**Cons:**
- Reinvents `asyncio.wait`.
- More code to maintain.
- Still needs `timeout_at` or equivalent.

### Option 4: `pytest-timeout`

**Description:** Let pytest-timeout handle scenario deadlines externally.

**Pros:**
- No scenario-level code.

**Cons:**
- Unsafe for asyncio: SIGALRM approach interferes with the event loop; thread mode kills the loop thread.
- Per-test, not per-scenario — one test can have multiple scenarios.
- Ruled out on correctness grounds.

---

## Decision

**Chosen Option:** Option 1 — `asyncio.timeout_at(deadline)` outer + `asyncio.wait(ALL_COMPLETED)` inner.

### Rationale

- It's the stdlib-idiomatic primitive for exactly this shape.
- Options 2-3 either lose partial results (gather) or reinvent the wheel (shared Event).
- Option 4 is unsafe for asyncio — pytest-timeout's own docs caveat that.
- Deadline-based gives absolute time budgets that propagate through nested awaits naturally, matching how operators think about SLAs.

---

## Consequences

### Positive

- One scenario-wide deadline, visible in the code via `async with asyncio.timeout_at(...)`.
- All expectations that fire before the deadline are kept; only truly pending ones become `TIMEOUT`.
- Pending coroutines are cancelled on context-manager exit; no leaked tasks.
- Deterministic reporting — timeout tests fail with the same shape every run.

### Negative

- Requires Python 3.11+ for `asyncio.timeout_at`. Baseline is 3.11 per [ADR-0005](0005-pytest-asyncio-session-loop.md).
- Cancellation on timeout is cooperative — a matcher that blocks synchronously for hours would not cancel. Matchers are guaranteed microsecond by [ADR-0013](0013-matcher-strategy-pattern.md); any slower would be a bug.
- Two-layer structure is slightly verbose compared to a one-line `wait_for`. Acceptable.

### Neutral

- Per-expectation `.within(ms)` tightens budgets below the scenario deadline; implementation is another `timeout_at` layered inside, not a fundamental change.
- Deadline reconfiguration (`asyncio.timeout().reschedule(...)`) is possible but not used — scenarios have fixed budgets.

### Security Considerations

- **Timeout leakage via traceback.** When `asyncio.TimeoutError` escapes, Python's default exception includes `repr(future)` for pending futures. Those reprs can include payload or scope data. Handles implement a redacting `__repr__` ([ADR-0014](0014-handle-result-model.md)) and Futures are wrapped so their `__repr__` only shows topic + correlation. Verified by a framework-internal test.
- **Deadline-exceeded diagnostics** include only metadata (topic, correlation ID, matcher description, deadline overshoot in ms). Payload content is not logged ([ADR-0009](0009-log-data-classification.md)).
- **No per-test silent skips.** A timed-out scenario marks its `TIMEOUT` outcome explicitly; pytest sees a real failure.

---

## Implementation

### Shape

```
async def await_all(self, *, timeout_ms: int) -> ScenarioResult:
    started_at = self._loop.time()
    deadline = started_at + timeout_ms / 1000

    try:
        async with asyncio.timeout_at(deadline):
            done, pending = await asyncio.wait(
                [h._future for h in self._handles],
                return_when=asyncio.ALL_COMPLETED,
            )
    except asyncio.TimeoutError:
        # Cancellation handled by the context manager;
        # handle state populated below.
        pass

    for handle in self._handles:
        if handle._future.done():
            self._populate_from_future(handle, started_at)
        else:
            handle.outcome = Outcome.TIMEOUT
            handle._reason = f"deadline exceeded by {overshoot_ms(deadline)}ms"
            handle._latency_ms = timeout_ms
            handle._future.cancel()

    return ScenarioResult(handles=tuple(self._handles), ...)
```

### Deadline propagation

- Scenario-wide deadline is computed once at `await_all()` entry.
- Any downstream `asyncio.timeout_at()` inside the scope uses the same deadline; nested `asyncio.timeout()` calls with shorter durations tighten locally.
- `.within(ms)` (future extension) creates a nested `asyncio.timeout()` scoped to one expectation.

### Handle population on timeout

- Every pending Future is cancelled before `await_all()` returns.
- Every Handle is populated — either from its done Future (PASS / FAIL) or synthesised (TIMEOUT).
- `ScenarioResult.passed = all(h.was_fulfilled() for h in handles)` — TIMEOUTs fail the scenario.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with PRD-002 implementation): scenario-wide deadline via `asyncio.timeout_at` + `asyncio.wait`.
- Phase 2: `.within(ms)` per-expectation tightening.
- Phase 3: deadline reconfiguration if a use case appears.

---

## Validation

### Success Metrics

- **Every expectation's outcome is populated after `await_all()`,** regardless of whether it fired or timed out. Verified by framework-internal tests.
- **Pending Futures are cancelled,** not orphaned. Verified by checking `asyncio.all_tasks()` is empty after scope teardown.
- **Timed-out scenarios produce a deterministic failure shape** — same topics / matchers / reason strings across reruns.
- **Zero `asyncio.TimeoutError` leaked out of `await_all`** — the framework catches and converts to Handle outcomes.
- **`pytest-timeout` is not in the dev dependencies.** Enforced by a CI check.

### Monitoring

- Framework-internal metric: count of scenarios that time out, broken down by which expectation dominated.
- Alert if >5% of scenarios time out in a session — either the services are too slow or the budgets are too tight.

---

## Related Decisions

- [ADR-0005](0005-pytest-asyncio-session-loop.md) — Session-scoped loop (requires 3.11+; same baseline we need for `timeout_at`).
- [ADR-0009](0009-log-data-classification.md) — Timeout diagnostics emit metadata only; no payload content.
- [ADR-0012](0012-type-state-scenario-builder.md) — DSL (where `await_all()` lives, on `TriggeredScenario`).
- [ADR-0014](0014-handle-result-model.md) — Handles (TIMEOUT outcome + redacting repr come from here).

---

## References

- [PRD-002 §User Story 3](../prd/PRD-002-scenario-dsl.md) — scenario-wide timeout acceptance criteria.
- [framework-design.md §5](../framework-design.md) — options and rationale.
- Python 3.11 release notes — `asyncio.timeout()` / `asyncio.timeout_at()`.
- pytest-timeout docs — explicit warning about SIGALRM + asyncio incompatibility.

---

## Notes

- **Open follow-up — deadline inheritance from pytest fixtures.** Some test frameworks let a session-scoped fixture impose a global deadline. Not planned for day one; revisit if CI starts to hang. **Owner:** Platform.
- **Open follow-up — `.within(ms)` per-expectation budget.** Documented as a Phase 2 extension. The implementation is straightforward (nested `asyncio.timeout`) but requires a small refactor to Handle creation. **Owner:** Framework maintainers.
- **Open follow-up — deadline diagnostics artefact.** When a scenario times out, a diagnostic snapshot (similar to [ADR-0007](0007-harness-failure-recovery.md)) might be valuable. Current Handle fields carry enough context; add if operators request it. **Owner:** Platform.

**Last Updated:** 2026-04-17
