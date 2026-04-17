# 0014. Handle-Based Result Model for Per-Expectation Assertions

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md) §User Story 2

---

## Context

A scenario often registers several expectations and triggers one publish. When the publish fires, each expectation resolves independently — order-booked before position-update, fill before audit, ack before fill. A test needs to:

- Assert that every expectation fired (a scenario-level outcome).
- Assert properties of individual messages — content, timing, latency.
- Make relative timing assertions — "position updated before order was booked".

The scenario-level `ScenarioResult.passed` carries aggregate pass/fail, but it does not surface per-expectation detail. This ADR records the decision to return a **Handle** from every `expect*()` call — an opaque reference that exposes the resolved state of that specific expectation after `await_all()` completes.

### Background

- [PRD-002 §User Story 2](../prd/PRD-002-scenario-dsl.md) requires `handle = s.expect(...)` with `.was_fulfilled()`, `.message`, `.latency_ms`, `.outcome`.
- [framework-design.md §8 Proposed additions](../framework-design.md) sketches the handle shape.
- Precedents: `asyncio.Future`, pytest's `capsys.readouterr()` return value, requests' `Response` object — all are "opaque reference to a resolved outcome".

### Problem Statement

How do scenarios expose the resolved state of individual expectations without leaking internal `asyncio.Future` machinery to test authors?

### Goals

- **Per-expectation assertions.** `assert handle.was_fulfilled()`, `assert handle.latency_ms < 500`, `assert handle.message.get("status") == "PASS"`.
- **Relative timing.** `assert h_position.latency_ms < h_booked.latency_ms`.
- **Survives scope teardown.** After `async with` exits, handles still carry their resolved state for post-scope assertions.
- **Opaque.** No access to internal Future, no way to resolve from the outside, no mutable state visible to tests.
- **Safe on timeout.** A handle whose expectation timed out reports `outcome == TIMEOUT`; `.message` is None, `.latency_ms` reflects the deadline.

### Non-Goals

- Providing raw `asyncio.Future` access. Tests should not `await` handles directly.
- Streaming multiple-message support (handle fires on first match, then stops). Multi-message scenarios register multiple expectations.
- Handles as scope anchors. The scope is anchored by the `async with`; handles are passive references to outcomes.

---

## Decision Drivers

- **Test ergonomics.** Per-expectation assertions should be short, obvious, and type-safe.
- **Encapsulation.** Internal Future mechanics must not leak; swapping the implementation later should not break tests.
- **Failure diagnostics.** On timeout or mismatch, the handle must carry enough context to explain what went wrong.
- **Survives teardown.** A test that asserts after the `async with` block closes should still work.

---

## Considered Options

### Option 1: Handle per `expect*()` — dataclass with resolved state (chosen)

**Description:** `expect*()` returns a `Handle`. After `await_all()`, the Handle is populated with `message`, `latency_ms`, `outcome`, `reason`. Before `await_all()`, the Handle reports `outcome == PENDING`.

**Pros:**
- Clean encapsulation; test never sees a Future.
- `handle.was_fulfilled()` is a one-line assertion.
- Opaque type; internal changes do not break tests.
- Survives scope teardown — the dataclass is a simple Python object.
- Supports relative timing assertions trivially.

**Cons:**
- Two-phase lifecycle (registered → resolved) means handles have a `PENDING` outcome window; accidental reads before `await_all()` give junk values if not handled.
- One more type to document.

### Option 2: Aggregate `ScenarioResult` only

**Description:** No per-expectation handles; `ScenarioResult.handles` is a `dict[str, ResolvedExpectation]` keyed by expectation label.

**Pros:**
- Fewer types.
- All assertions happen after `await_all()`.

**Cons:**
- Relative timing is verbose: `result.handles["orders.booked"].latency_ms`.
- Per-expectation assertions read worse than `handle.was_fulfilled()`.
- Requires naming every expectation; anonymity is harder.

### Option 3: Raw `asyncio.Future` exposed

**Description:** `expect*()` returns an `asyncio.Future`; tests `await future` to get the message.

**Pros:**
- No new type.
- Users who know asyncio recognise it.

**Cons:**
- Tests that await futures can deadlock if the ordering is wrong.
- No timing info, no outcome classification.
- Internal implementation detail exposed; changing to a different primitive breaks every test.

### Option 4: Callback-based — `expect(topic, matcher, on_match=...)`

**Description:** Tests provide a callback that runs when the expectation resolves.

**Pros:**
- No post-scope assertion phase; everything happens in the callback.

**Cons:**
- Callbacks run on the dispatcher thread; assertions there are awkward.
- Tests that want relative timing need shared state across callbacks.
- Diverges from the PRD-002 shape.

---

## Decision

**Chosen Option:** Option 1 — Handle per `expect*()` with resolved-state dataclass.

### Rationale

- Option 2's post-hoc aggregate loses the per-expectation natural reading.
- Option 3 is the wrong abstraction — the test should not care that there's a Future.
- Option 4 moves assertions to a bad place (dispatcher thread, no pytest semantics).
- Option 1 matches how every widely-used async test framework surfaces per-item outcomes (`Response`, `Future` but wrapped, `asyncio.Task.result()`) while preserving the clean scope semantics.

---

## Consequences

### Positive

- Per-expectation assertions read naturally: `assert handle.was_fulfilled() and handle.latency_ms < 500`.
- Relative timing is trivial and readable.
- Handle is an opaque dataclass; internal implementation (Future, Event, queue) can change without breaking tests.
- Handles survive scope teardown; post-scope assertions are valid.
- Timeout carries explanation: `handle.outcome == TIMEOUT`, `handle.reason == "deadline exceeded by 42ms"`.

### Negative

- Two-phase lifecycle: `PENDING` outcome before `await_all()` is a footgun if tests read too early. Mitigated by type annotations and a runtime guard (`handle.message` raises if outcome == PENDING).
- Handle is yet another type in the DSL vocabulary alongside the four scenario states.
- Handle must not hold a live reference to the underlying Future after scope teardown (would leak); teardown copies the resolved fields out.

### Neutral

- Handle equality is by identity, not by content. Two handles to "the same expectation" do not exist — each `expect*()` creates one.
- Handles are created eagerly at `expect*()` time; resolved lazily as messages arrive.
- Handles can be passed between functions within the scope — useful for factored helpers.

### Security Considerations

- **Handle stores the matched message payload** via `handle.message`. In fixed income, that payload contains sensitive data.
- **Redaction at rest.** Handle's `__repr__` does not include the payload; `handle.message` is accessed explicitly. Tests that log handles do not leak by default.
- **Pickling.** Handles are not pickleable — same `__reduce__` pattern as Harness ([ADR-0010](0010-secret-management-in-harness.md)). Prevents debug artefacts from carrying payloads across process boundaries.
- **Surprise-log interaction.** A handle never exposes content from another scope's messages. The scope boundary (ADR-0002) + correlation routing (ADR-0004) enforce this.

---

## Implementation

### Handle type

```
class Outcome(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"


@dataclass
class Handle:
    topic: str
    matcher_description: str
    correlation_id: str
    outcome: Outcome = Outcome.PENDING
    _message: Any = None
    _latency_ms: float | None = None
    _reason: str = ""

    def was_fulfilled(self) -> bool: return self.outcome == Outcome.PASS

    @property
    def message(self) -> Any:
        if self.outcome == Outcome.PENDING:
            raise RuntimeError("handle accessed before await_all()")
        return self._message

    @property
    def latency_ms(self) -> float:
        if self._latency_ms is None:
            raise RuntimeError("handle accessed before await_all()")
        return self._latency_ms

    @property
    def reason(self) -> str: return self._reason

    def __repr__(self) -> str:
        return f"<Handle topic={self.topic} outcome={self.outcome.value}>"

    def __reduce__(self) -> Any:
        raise TypeError("Handle is not pickleable — may carry payload data")
```

### Resolution flow

1. `expect*()` creates a Handle in `PENDING` state, returns it, and registers an internal record with the scope's Dispatcher correlation.
2. Dispatcher's resolver (the lambda passed to `broker.dispatch`) is bound to resolve this specific Handle: runs matcher, updates outcome, stores message, stamps latency.
3. On match: `outcome = PASS`, `message` set, `latency_ms` computed from `await_all()` start timestamp.
4. On no-match with matching correlation: `outcome = FAIL`, reason explains which matcher rejected.
5. On deadline: `outcome = TIMEOUT`, `reason = "deadline exceeded by Xms"`.
6. After `await_all()` returns, Handles are frozen — no further mutation.

### Relationship to ScenarioResult

`ScenarioResult.handles` is a tuple of every Handle registered in the scenario, in registration order. `ScenarioResult.passed == all(h.was_fulfilled() for h in handles)`.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with PRD-002 implementation): Handle with the four outcomes, basic fulfilment check, message / latency access.
- Phase 2: `reason` populated with matcher failure details (requires ADR-0013 matcher descriptions).
- Phase 3: timing helpers (`handle.fired_before(other_handle)`).

---

## Validation

### Success Metrics

- **Per-expectation assertions read as one line.** Verified by scenario tests.
- **Relative timing works:** `h1.latency_ms < h2.latency_ms`.
- **Pre-await reads raise, not return junk.** Verified by a framework-internal test that accesses `handle.message` before `await_all()`.
- **Handles survive teardown.** Test that asserts outside the `async with` block still sees resolved state.
- **Handle never carries another scope's message.** Enforced by correlation routing; verified by a parallel-scenario test.

### Monitoring

- No runtime metrics — handles are passive.
- Framework-internal test catches handles that leak between scopes.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scoped registry (handles are scope-local until teardown).
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher (resolves handles via the resolver lambda).
- [ADR-0010](0010-secret-management-in-harness.md) — Pickling refusal pattern reused here.
- [ADR-0012](0012-type-state-scenario-builder.md) — DSL (`expect*()` returns a Handle).
- [ADR-0013](0013-matcher-strategy-pattern.md) — Matcher (populates `reason` on failure).
- [ADR-0015](0015-deadline-based-scenario-timeouts.md) — Deadline (TIMEOUT outcome comes from there).

---

## References

- [PRD-002 — Scenario DSL](../prd/PRD-002-scenario-dsl.md)
- [framework-design.md §8](../framework-design.md)
- Python dataclasses — https://docs.python.org/3/library/dataclasses.html

---

## Notes

- **Open follow-up — handle labels.** Some tests might want to name expectations for readability in `summary()`. Optional `label=` keyword on `expect*()` would populate `Handle.label`. Add in Phase 2 if needed. **Owner:** Framework maintainers.
- **Open follow-up — streaming matches.** A scenario might want to match N messages, not just the first. Current design is one-shot. If this need arises, add a `times(n)` clause on `expect*()`. **Owner:** Platform.

**Last Updated:** 2026-04-17
