# 0017. Fire-and-Forget Reply Results with ReplyReport

**Status:** Proposed
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-008 — Scenario Replies](../prd/PRD-008-scenario-replies.md) §User Stories 5, §Functional Requirements

---

## Context

Every existing `expect*()` call in the Scenario DSL returns a `Handle` ([ADR-0014](0014-handle-result-model.md)) — a dataclass that carries the resolved outcome, message, latency, and reason for that expectation. Tests assert on Handles. The Handle model is the established way the DSL surfaces per-subscription outcomes.

[PRD-008](../prd/PRD-008-scenario-replies.md) introduces replies (`on(...).publish(...)`). The question: do replies also return Handles, or do they use a different result model?

A Handle implies:

- This subscription is an **assertion target**. Tests read it to say pass or fail.
- It carries a **latency budget**; `within_ms()` tightens it.
- It has **five outcomes** — PENDING / PASS / FAIL / TIMEOUT / SLOW — all with observable meaning to the test.

A reply is not any of those things. It is a stand-in for a service. Its "success" is that downstream expectations fire correctly; its "failure" is that they don't. Replies are machinery, not assertions.

But replies still need **observability**. When a scenario mysteriously times out on a downstream expectation, the first question is "did my reply fire?" If the answer is opaque, debugging falls back to log-diving.

This ADR records the decision to make replies **fire-and-forget** — no Handle — with a separate **`ReplyReport`** carried on `ScenarioResult.replies` for observability.

### Background

- [ADR-0014 — Handle-based result model](0014-handle-result-model.md) defines the Handle contract for `expect*()` calls.
- [PRD-008 §User Stories](../prd/PRD-008-scenario-replies.md) explicitly states fire-and-forget semantics: "Reply-level latency budgets and Handle-style assertion" listed as a Non-Goal.
- [ADR-0009 — Log data classification](0009-log-data-classification.md) governs what may appear in reply-report error strings.

### Problem Statement

How do replies expose "did I fire?" to the test author without misrepresenting themselves as assertion targets?

### Goals

- **Zero new assertion surface.** The test author asserts on downstream effects (via `expect`), not on whether the reply fired.
- **First-class debuggability.** On scenario failure, the author can tell from the result alone whether the reply fired, never fired, mismatched candidates, or blew up in the builder.
- **Consistent with `ScenarioResult`.** Reply results surface in the same result object that carries expectation outcomes.
- **Payload-safe.** Reply reports must not leak payload content in `__repr__` or summaries — same standard as Handle ([ADR-0014 §Security Considerations](0014-handle-result-model.md)).

### Non-Goals

- Replacing the Handle model for expectations. Handles remain the assertion primitive.
- Providing a "reply fired" assertion. Tests assert on the downstream cascade.
- Exposing the exact reply bytes in the report. Reports carry counts and states, not payloads.
- Async builder support in v1 — covered under PRD-008 Nice-to-Have.

---

## Decision Drivers

- **Honest API surface.** A reply is not an assertion; it should not masquerade as one.
- **Debuggability.** A reply that fails silently is worse than one that doesn't exist.
- **Single source of truth for scenario outcome.** One `ScenarioResult`, one place to read.
- **Safety.** Error diagnostics must not leak payload data.
- **Reversibility.** If a future ADR decides replies need Handles after all, the report can be extended without breaking tests.

---

## Considered Options

### Option 1: ReplyReport only, fire-and-forget (chosen)

**Description:** `s.on(...).publish(...)` returns a transient `ReplyChain` that, once `.publish()` terminates it, hands control back to the scope and exposes no handle. The reply's observability surface is a new field on `ScenarioResult.replies: tuple[ReplyReport, ...]`. Each report carries topic, a redacted matcher description (see §Security Considerations), a terminal state (`ARMED_NO_MATCH` / `ARMED_MATCHER_MISMATCHED` / `FIRED` / `FIRED_BUILDER_ERROR` — derived at scope exit from the runtime `_ReplyState` in [ADR-0016](0016-reply-lifecycle.md) plus `candidate_count` / `match_count`), `candidate_count`, `match_count`, `reply_published` boolean, and `builder_error` string or `None`.

**Pros:**
- Matches the user's expressed intent ("fire and forget").
- No misuse risk — there's no handle for tests to assert against incorrectly.
- Debuggability fully covered: every failure mode has a distinct state.
- `ReplyChain` can evolve (multi-hop, `.repeats()`, `.describe()`) without breaking report consumers.
- Report lives on `ScenarioResult`, already-known failure surface; `summary()` extends naturally.

**Cons:**
- Reply observability reads differently from expectation observability (report vs Handle). Two mental models in the same DSL.
- Tests that want "this reply must have fired" assertions write `assert report.state is ReplyReportState.REPLIED` against the report rather than `assert handle.was_fulfilled()`. Slightly wordier.

### Option 2: Handle-based (same as `expect`)

**Description:** `s.on(...).publish(...)` returns a `Handle` whose `was_fulfilled()` means "the reply fired at least once".

**Pros:**
- One mental model for all DSL subscriptions.
- Tests who want to assert the reply fired get the same syntax.

**Cons:**
- Conflates "the reply fired" with "the test's assertion passed". Tests end up asserting both the reply Handle and the downstream expect Handle, which is redundant. If a test only asserts the reply Handle, it passes when the downstream SUT is completely broken.
- Handle's four outcomes (PASS / FAIL / TIMEOUT / SLOW) don't map onto reply states. What does `FAIL` mean for a reply? What does `SLOW` mean — a slow reply? A slow builder?
- Implies latency budgets via `within_ms()` that don't have a natural meaning. A reply's latency is the builder's latency plus the transport publish latency; nothing the scope deadline cares about.
- PRD explicitly rejects this.

### Option 3: Hybrid — optional `.expect_fired()` promotes the reply to a Handle

**Description:** Default `on(...).publish(...)` is fire-and-forget, but an optional `.expect_fired(within_ms=...)` returns a Handle that tests can assert on.

**Pros:**
- Preserves the default.
- Gives authors an opt-in assertion when needed.

**Cons:**
- Two APIs for one concept. Authors have to decide per reply whether to opt in.
- The opt-in does not actually add information — if the reply didn't fire, the downstream expect will time out and fail, surfacing the problem.
- Encourages over-assertion: every reply gets `.expect_fired()` defensively, so the opt-in becomes the default.
- Addable later if real demand emerges.

### Option 4: No reporting at all

**Description:** Replies are pure side effects. Nothing surfaces on the result.

**Pros:**
- Minimum new code.
- Nothing to maintain.

**Cons:**
- Breaks the debuggability goal. A reply that silently mismatches every candidate (wrong matcher) looks identical to a reply that never saw a candidate (wrong topic). Both present as downstream expect timeouts.
- Authors fall back to log-diving for common problems.
- Violates PRD-008's P0 reply-report requirements.

---

## Decision

**Chosen Option:** Option 1 — Fire-and-forget replies with `ReplyReport` on `ScenarioResult`.

### Rationale

- Option 1 cleanly separates the two roles in the DSL: subscriptions that are assertion targets (Handles) and subscriptions that are stand-ins for services (reports). A test author reading `handle = s.expect(...)` knows they're being asked to assert something; reading `s.on(...).publish(...)` knows they're declaring a behaviour.
- Option 2 forces a shape onto replies that doesn't fit: latency budgets without a meaningful budget, outcomes without distinct meanings. The additional assertion surface is an attractive nuisance.
- Option 3 resolves the tension by duplicating the API. Two ways to do one thing is worse than one clean way.
- Option 4 trades ~50 lines of reporting code for an order-of-magnitude debuggability cost.
- The four `ReplyReportState` members distinguish failure modes that look identical from outside the reply: `ARMED_NO_MATCH` (wrong topic — zero candidates arrived) versus `ARMED_MATCHER_MISMATCHED` (right topic, wrong shape — candidates arrived but matcher mismatched all of them). Collapsing these into a single "never fired" state would leave the most common debugging question — "is my topic wrong or my matcher wrong?" — unanswerable from the report alone.

---

## Consequences

### Positive

- Test code reads honestly. `expect` is for asserting; `on` is for simulating.
- `ScenarioResult.summary()` has a reply section that surfaces each reply's state in a single line. Debugging "my expect timed out" becomes a two-line read.
- Adding `.describe("instant fill")` in the future enriches summaries without changing the result shape.
- Zero change to `Handle` ([ADR-0014](0014-handle-result-model.md)); existing tests unaffected.

### Negative

- Two result models in the same DSL — Handle for expectations, ReplyReport for replies. Must be documented clearly in the DSL reference.
- A test that wants to assert "reply fired cleanly" writes `assert s_result.reply_at("request.sent").state is ReplyReportState.REPLIED` rather than `assert handle.was_fulfilled()`. Accessor method on `ScenarioResult` eases this.
- If future work decides replies should become Handles, the report is a compatibility layer. Acceptable cost; the report remains informative even alongside a Handle.

### Neutral

- `ReplyChain` is an internal transient type; its `__repr__` does not need to match Handle's.
- Report ordering: replies appear on `ScenarioResult.replies` in registration order (stable).
- Reports are immutable dataclasses, same frozen semantics as Handle after `await_all()` returns.

### Security Considerations

- **No payload in reports.** `ReplyReport` carries state, counts, topic, a redacted matcher description, and optionally a builder-error string. It does **not** carry the triggering payload or the published reply. A `__repr__` that included payload would leak through `summary()` and CI logs — design explicitly rejects this.
- **Builder-error redaction is structural, not string-scrubbed.** `builder_error` is constructed as exactly `f"{type(e).__name__}"` — the exception class name alone. The exception's `args` / `str(e)` is **not** interpolated into the report string, because a builder might include payload-derived values in its own exception message (`raise ValueError(f"bad correlation_id {message_received['correlation_id']}")`). The full traceback is logged separately at ERROR level through the logger, where [ADR-0009 — Log data classification](0009-log-data-classification.md) applies. The report carries the class name only so no log-classification decision is needed for the report itself.
- **Matcher-description redaction for logs.** Matchers from [ADR-0013](0013-matcher-strategy-pattern.md) expose a description string that may include field values (`field_equals("account", "ACME-123")` → `account == "ACME-123"`). When a never-fired reply emits a WARNING at scope exit (see §Warning-log behaviour), the description passes through a redactor that replaces literal values with `<value>`: `account == <value>`. The unredacted description stays on the in-memory `ReplyReport` for post-scope test assertions but is never logged.
- **Pickling refusal.** Reply reports are not pickleable — same `__reduce__` pattern as Handle ([ADR-0014 §Security Considerations](0014-handle-result-model.md)) and Harness ([ADR-0010](0010-secret-management-in-harness.md)). The rationale is the same across all three: cross-process debug artefacts (pytest-xdist workers, cached fixtures, crash dumps) bypass ADR-0009's in-process redaction pipeline, so framework types that may reference payload-derived state refuse to serialise.
- **Cross-scope disclosure.** `ScenarioResult.replies` lists only replies registered in that scope. The scope boundary ([ADR-0002](0002-scoped-registry-test-isolation.md)) plus correlation routing ([ADR-0018](0018-reply-correlation-scoping.md)) prevent a reply from recording a message meant for another scope.

---

## Implementation

### Types

The runtime state enum lives in [ADR-0016](0016-reply-lifecycle.md) (`_ReplyState`, three members). The terminal state enum lives here and is derived at scope exit — it has four members:

```
class ReplyReportState(StrEnum):
    ARMED_NO_MATCH         = "armed_no_match"         # runtime=ARMED, candidate_count == 0
    ARMED_MATCHER_MISMATCHED = "armed_matcher_mismatched" # runtime=ARMED, candidate_count > 0, match_count == 0
    FIRED                  = "fired"                  # runtime=FIRED
    FIRED_BUILDER_ERROR    = "fired_builder_error"    # runtime=FIRED_BUILDER_ERROR


@dataclass(frozen=True)
class ReplyReport:
    trigger_topic: str
    matcher_description: str   # unredacted here for test assertions; redacted before logging
    reply_topic: str
    state: ReplyReportState
    candidate_count: int
    match_count: int
    reply_published: bool
    builder_error: str | None = None   # exception class name only; no str(e)

    def __repr__(self) -> str:
        return (
            f"<ReplyReport trigger={self.trigger_topic} "
            f"reply={self.reply_topic} state={self.state.value}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError("ReplyReport is not pickleable: redaction enforced structurally")
```

### ScenarioResult extension

```
@dataclass(frozen=True)
class ScenarioResult:
    name: str
    correlation_id: str
    handles: tuple[Handle, ...]
    replies: tuple[ReplyReport, ...]          # NEW
    duration_ms: float
    passed: bool

    def reply_at(self, trigger_topic: str) -> ReplyReport:
        for r in self.replies:
            if r.trigger_topic == trigger_topic:
                return r
        raise KeyError(trigger_topic)

    def summary(self) -> str:
        # existing handle section, then:
        # "Replies:"
        # "  request.sent -> reply.received: FIRED (1 match / 1 candidate)"
        # "  heartbeat.request -> heartbeat.response: ARMED_NO_MATCH (0 candidates)"
```

### Resolution flow

1. `ReplyChain.publish()` registers a `_Reply` (runtime state `ARMED`) and returns the prior-state scope.
2. During `await_all()`, dispatch routes messages to the reply per [ADR-0018](0018-reply-correlation-scoping.md) correlation filtering. Each route increments `candidate_count` unconditionally — counting starts the instant a candidate arrives, whether the runtime state is still `ARMED` or already `FIRED` (per [ADR-0016](0016-reply-lifecycle.md) §Fire-once enforcement).
3. Matcher evaluation only runs when runtime state is `ARMED`. On accept: runtime state transitions to `FIRED`, `match_count += 1`, builder invoked, reply published (`reply_published = True`).
4. Builder exception during step 3: runtime state transitions to `FIRED_BUILDER_ERROR`, `reply_published = False`, `builder_error = type(e).__name__` (class name only — see §Security Considerations for why `str(e)` is not captured).
5. End of `await_all()`: terminal `ReplyReportState` computed from runtime state + counts (mapping in §Types docstring).
6. On `__aexit__`, reports are frozen and attached to `ScenarioResult`; subscriptions are deregistered.

### Warning-log behaviour

At scope exit, any reply whose terminal state is `ARMED_NO_MATCH` or `ARMED_MATCHER_MISMATCHED` logs at WARNING. `FIRED_BUILDER_ERROR` logs at ERROR. `FIRED` does not log at WARNING (including `FIRED` with `candidate_count > 1` — the report surfaces the over-count; the log does not).

The WARNING line includes: trigger topic, reply topic, terminal state, candidate count, and a **redacted** matcher description (literal values replaced with `<value>`). The unredacted description remains on the in-memory `ReplyReport` for post-scope test assertions but is never emitted through the logger. See §Security Considerations.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (PRD-008): Four states, basic report, warning log, `summary()` reply section.
- Phase 2 (optional): `.describe("...")` on `ReplyChain` populates a `description` field on the report.
- Phase 3 (optional): `reply_at()` accessor variants (by reply topic, by description).

---

## Validation

### Success Metrics

- **Debuggability test.** A scenario with a reply whose matcher mismatches the single candidate fails a downstream expect. The `summary()` output names the reply by topic and states `ARMED_MATCHER_MISMATCHED (1 candidate, 0 matches)`. Target: passing the string assertion on `summary()`.
- **No-leak test.** A test that publishes sensitive-looking JSON as the reply confirms that `ReplyReport.__repr__`, `ScenarioResult.__repr__`, and `summary()` contain no payload substrings. Target: zero matches.
- **Pickle refusal test.** `pickle.dumps(report)` raises. Target: `TypeError`.
- **State-coverage test.** Four scenario variants, one per terminal `ReplyReportState`, each produces the expected state on the report. Target: four passing tests.
- **Matcher-description redaction test.** A reply registered with `field_equals("account", "ACME-123")` that never fires produces a WARNING log whose text contains `account == <value>` and does **not** contain `ACME-123`. Target: string absent from log, sentinel present.
- **Builder-error redaction test.** A builder that `raise ValueError(f"bad correlation_id {message_received['correlation_id']}")` produces a report with `builder_error == "ValueError"` and no trace of the correlation_id substring in either the report or its `__repr__`. Target: exception class name only.

### Monitoring

- CI gate on the debuggability test (regressions in `summary()` text visible).
- Mypy strict gate ensures `ScenarioResult.replies` is consumed with correct types.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scope boundary.
- [ADR-0009](0009-log-data-classification.md) — Redaction rules for builder-error strings.
- [ADR-0014](0014-handle-result-model.md) — Handle model for `expect*`; deliberately not extended here.
- [ADR-0016](0016-reply-lifecycle.md) — Reply lifecycle populates the report.
- [ADR-0018](0018-reply-correlation-scoping.md) — Correlation filter determines which messages become candidates.

---

## References

- [PRD-008 — Scenario Replies](../prd/PRD-008-scenario-replies.md) §User Story 5
- WireMock verify API — https://wiremock.org/docs/verifying/ (counter-example; verify-by-assertion model rejected here)

---

## Notes

- **Deferred — `.describe()`.** Phase 2. If adopted, the report gains a `description` field and `summary()` prefers it when present. **Owner:** Framework.
- **Deferred — reply Handles.** Only revisit if multiple consumers produce tests where the reply is itself the assertion target rather than plumbing. **Owner:** Platform.
- **Deferred — streaming reports.** For long-running scopes (not applicable in v1), reports could emit lifecycle events as they happen rather than only at scope exit. **Owner:** Framework.

**Last Updated:** 2026-04-17
