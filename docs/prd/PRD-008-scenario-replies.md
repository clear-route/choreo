# Scenario Replies — Reactive `on / publish` Chains — Product Requirements Document

**Status:** Draft
**Created:** 2026-04-17
**Last Updated:** 2026-04-17
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering

---

## Executive Summary

Add a reactive primitive to the Scenario DSL: `s.on(topic, matcher).publish(topic, builder)`. A reply observes a message on the bus, extracts fields from it, and publishes a reply whose shape depends on what arrived. This gives a test author a single composable way to stand in for a service that the system-under-test (SUT) expects to reply to it. Replies are per-scenario and correlation-scoped, so parallel tests don't cross-talk.

---

## Problem Statement

### Current State

Today the DSL has three operations on a scope ([PRD-002](PRD-002-scenario-dsl.md)):

1. `expect(topic, matcher)` — passive observation with a `Handle`.
2. `publish(topic, payload)` — send one message.
3. `await_all(timeout_ms)` — block until all expectations resolve.

There is no primitive for "when I see X, send Y". Tests that need to stand in for an external service have only two options:

- **Write a bespoke subscriber in test code.** Register a callback via `harness.subscribe(...)`, decode the payload, build a reply, call `harness.publish(...)`. This reinvents correlation filtering, cleanup, and error handling in every test.
- **Use a centralised-fake alternative.** Central, stateful, tied to one protocol or domain. Covers the common paths well, but every new response behaviour needs a strategy class, every new deviation a branch. Tests that need a one-off behaviour can't express it locally.

### User Pain Points

- **Bespoke subscribers leak.** Without scope-bound cleanup, hand-rolled subscribers outlive the test and pollute the next one.
- **Correlation filtering is easy to get wrong.** Hand-rolled callbacks tend to skip the correlation check or re-derive it incorrectly, breaking parallel runs.
- **Centralised fakes grow claws.** A central fake is fine for the common happy path; testing a bespoke variant means a new strategy, a new state enum, a new registration call. The test itself can't say what it wants.
- **The common case reads like plumbing.** "When the SUT publishes `request.sent`, reply on `reply.received` with the same correlation_id, a fresh reply_id, status=COMPLETED" is one sentence in English and thirty lines of wiring today.

### Business Impact

- Adapter-boundary tests take longer to write and review than the behaviour they cover.
- Non-standard flows (partial replies, cancel-replace, reject then retry) are under-tested because the cost of setup is too high.
- Flaky hand-rolled subscribers creep into the suite and get `@skip`'d rather than fixed.

---

## Goals and Objectives

### Primary Goals

1. **One fluent primitive** for "observe, extract, reply". A reply is a single declarative statement inside a scenario.
2. **Correlation-scoped by default.** Replies registered inside a scope only fire for messages carrying that scope's correlation ID. Parallel tests do not see each other's replies.
3. **Access to the triggering message.** The reply builder receives the decoded triggering payload, so replies can echo correlation IDs, parent IDs, or other SUT-generated fields.
4. **Scope-bound lifecycle.** Replies register on scope entry, deregister on scope exit. No cross-test residue.
5. **A local, composable alternative to centralised fakes for the majority of adapter-boundary tests.** Consumers ship reusable reply bundles (e.g. `mocks.instant_reply(s)`) that compose the same primitive in-line.

### Success Metrics

- **Line-count reduction:** the canonical "instant reply" flow drops from ~30 lines of bespoke wiring to ≤3 lines of DSL.
- **Parallel safety:** 100 concurrent scenarios each registering replies on the same topic fire their own replies only, with zero cross-scenario triggers, verified by a targeted parallel-isolation test.
- **Cleanup guarantee:** after scope exit, the transport reports zero residual subscriptions attributable to scope replies, including on exception paths.
- **Adoption target:** within one sprint of landing, ≥2 consumer-side reply bundles replace at least one centralised-fake strategy for the tests that used it.

### Non-Goals

- **Replacing `expect()`.** `on()` is additive. `expect()` remains the assertion primitive.
- **Multi-hop reply chains (`on().publish().on().publish()`).** Deferred to a follow-up PRD once the single-hop primitive is bedded in. The API should not foreclose it.
- **Stateful replies across messages.** Replies are stateless per fire. Anything needing state (e.g. cumulative fill tracking) belongs in a consumer-side bundle that holds state in its own closure, not in the framework.
- **Reply-level latency budgets and `Handle`-style assertion.** Replies are fire-and-forget. Downstream `expect()`s assert the observable effect.
- **A new transport protocol.** Replies use the same `Transport` Protocol as `expect` and `publish`.

---

## User Stories

### Primary User Stories

**As a** test author writing a request/reply flow test,
**I want to** declare "when the SUT asks the backend for a thing, reply with a completion" in one statement,
**So that** I can focus the rest of the test on asserting the downstream effect (state updates, projections, audit).

**Acceptance Criteria:**
- [ ] `s.on("request.sent").publish("reply.received", lambda message_received: {...})` registers a reply that fires exactly once per scope.
- [ ] The builder callback receives the decoded triggering payload as its only argument.
- [ ] The reply is published on the named topic with the scope's correlation ID auto-injected (same rule as `Scenario.publish`).
- [ ] A downstream `expect("state.changed", ...)` sees the cascade and resolves against the reply's reply.

---

**As a** test author running hundreds of scenarios in parallel,
**I want to** know that my reply only fires for my scenario's messages,
**So that** I never chase a flake caused by another scenario's trigger firing mine.

**Acceptance Criteria:**
- [ ] Replies use the same correlation-ID filter as expectations: a message whose `correlation_id` field does not match the scope is ignored silently.
- [ ] A parallel-isolation test registers the same reply in 100 scopes and asserts each reply fires exactly once for its own scope's trigger.
- [ ] Messages with no correlation field follow the same rule as for `expect`: silently ignored, logged at DEBUG for diagnostics.

---

**As a** test author replacing a centralised-fake strategy,
**I want to** package a reusable reply bundle in my consumer repo,
**So that** common behaviours are one helper call, while bespoke flows stay in-line.

**Acceptance Criteria:**
- [ ] A consumer can write `def instant_reply(s): s.on(...).publish(...)` and call `instant_reply(s)` inside any scope.
- [ ] Bundles compose — calling `instant_reply(s)` and `audit_capture(s)` in the same scope registers both replies independently.
- [ ] Framework ships no domain-specific reply; consumers own their own bundles.

---

**As a** framework maintainer,
**I want to** guarantee reply cleanup on scope exit,
**So that** a test that raises mid-execution doesn't leave a reply armed for the next scenario.

**Acceptance Criteria:**
- [ ] Scope `__aexit__` unregisters every reply registered in the scope.
- [ ] Cleanup runs on exception.
- [ ] Residue check in the test suite passes after scope exit.

---

**As a** test author debugging a scenario where nothing happened,
**I want to** see whether my reply fired,
**So that** I can tell "trigger never arrived" apart from "trigger arrived but my matcher rejected it".

**Acceptance Criteria:**
- [ ] `ScenarioResult` carries a reply report: per-reply, how many candidate messages arrived, how many the matcher accepted, whether the reply was published.
- [ ] A reply that never fired logs a WARNING at scope exit, including topic + matcher description.
- [ ] Builder exceptions are caught, logged at ERROR with the triggering payload redacted per [ADR-0009](../adr/0009-log-data-classification.md), and surfaced in the reply report. Scenario does not hard-fail on builder error.

---

## Proposed Solution

### High-Level Approach

`on()` registers a **reply** — a subscriber that reacts to a matched message by publishing a reply. It is the fluent composition of an expect-shaped trigger and a publish-shaped reply, bound to the scope's correlation ID and lifecycle.

At the type-state level ([ADR-0012](../adr/0012-type-state-scenario-builder.md)):

- `ScenarioBuilder.on(topic, matcher=None) -> ReplyChain` and `ExpectingScenario.on(topic, matcher=None) -> ReplyChain`. Registering a reply transitions `ScenarioBuilder` to `ExpectingScenario` (it is a form of subscription — the same invariant as `expect`).
- `ReplyChain.publish(topic, payload) -> ExpectingScenario`. The chain's `publish` is the **reply spec**, not the scenario trigger. It returns the scope in its prior type-state so further `expect`, `on`, or `publish` calls are available.
- `TriggeredScenario` does **not** expose `on()` in v1. Replies are registered before the trigger fires. Post-trigger registration is a follow-up.

The reply's `publish` accepts:
- A **dict or bytes** — static reply payload, identical rules to `Scenario.publish`; correlation auto-injected into dicts.
- A **callable** `(received_payload) -> dict | bytes` — the builder. Invoked on each match; its return value is published. If it raises, the error is logged, the scenario continues, and the reply report flags the error.

Replies fire **once**, then deregister. This matches the primary use case (mocking one round-trip) and keeps the mental model small. Multi-fire replies are a follow-up (see Future Considerations).

### User Experience

**Canonical request/reply flow (the motivating case):**

```python
async with harness.scenario("reply_updates_state") as s:
    h_state = s.expect("state.changed", contains_fields({"count": 1000}))

    s.on("request.sent").publish(
        "reply.received",
        lambda message_received: {
            "correlation_id": message_received["correlation_id"],
            "reply_id": f"REP-{message_received['correlation_id']}-1",
            "status": "COMPLETED",
            "amount": message_received["amount"],
            "count": message_received["count"],
        },
    )

    s = s.publish("events.created", event_fixture)
    result = await s.await_all(timeout_ms=500)
    assert result.passed
    assert h_state.was_fulfilled()
```

**Bundle-level reuse on the consumer side:**

```python
# consumer_repo/tests/mocks.py
def instant_reply(scenario, *, reply_id=new_reply_id):
    scenario.on("request.sent").publish(
        "reply.received",
        lambda message_received: {
            "correlation_id": message_received["correlation_id"],
            "reply_id": reply_id(),
            "status": "COMPLETED",
            "amount": message_received["amount"],
            "count": message_received["count"],
        },
    )


def reject_with(reason):
    def _register(scenario):
        scenario.on("request.sent").publish(
            "reply.received",
            lambda message_received: {
                "correlation_id": message_received["correlation_id"],
                "status": "REJECTED",
                "reason": reason,
            },
        )
    return _register


# consumer_repo/tests/test_replies.py
async def test_a_reply_should_update_state(harness):
    async with harness.scenario("reply") as s:
        mocks.instant_reply(s)
        h_state = s.expect("state.changed", contains_fields({"count": 1000}))
        s = s.publish("events.created", event_fixture)
        result = await s.await_all(timeout_ms=500)
        assert h_state.was_fulfilled()
```

**Matcher on the trigger (filter which request to react to):**

```python
s.on("request.sent", contains_fields({"region": "eu-west"})).publish(
    "reply.received",
    lambda message_received: {...},
)
```

**Static reply (no builder):**

```python
s.on("heartbeat.request").publish("heartbeat.response", {"ok": True})
```

### Key Components

- **`Scenario.on(topic, matcher=None)`** — new entry point. Registers a reply under the scope's correlation ID. Returns a `ReplyChain`.
- **`ReplyChain.publish(topic, payload)`** — terminates the chain. Binds the reply spec. Returns the scenario in its prior type-state.
- **`_Reply`** (internal) — record holding trigger topic, matcher, reply topic, reply builder, fire-count, and a deregistration hook.
- **`ReplyReport`** — per-reply observability record held on `ScenarioResult`: topic, matcher description, candidate count, match count, reply-publish count, builder-error (if any).
- **Scope lifecycle extension** — `__aexit__` deregisters replies alongside expectations.

---

## Scope

### In Scope

- `Scenario.on(topic, matcher=None)` on `ScenarioBuilder` and `ExpectingScenario`.
- `ReplyChain.publish(topic, payload)` accepting `dict`, `bytes`, or `Callable[[decoded], dict | bytes]`.
- Correlation filtering identical to [ADR-0004](../adr/0004-dispatcher-correlation-mediator.md): messages whose `correlation_id` field does not match the scope are ignored.
- Auto-injection of `correlation_id` into dict replies (same rule as `Scenario.publish`; builder output can override by setting the field explicitly).
- One-fire semantics: reply deregisters after first accepted match.
- `ScenarioResult.replies: list[ReplyReport]`.
- Warning log on scope exit for replies that never fired.
- Error log + reply-report flag on builder exceptions; scenario continues.
- Scope-bound cleanup via `__aexit__`, on both happy and exception paths.
- `summary()` on `ScenarioResult` includes reply section (fired / not-fired, builder errors).
- Unit tests: single-scope reply, multi-reply scope, no-match, matcher-rejection, builder exception, parallel-scope isolation, cleanup-on-exception.

### Out of Scope

- **Multi-hop chains** (`on(...).publish(...).on(...).publish(...)`). The return type is chosen so multi-hop is addable without breaking change, but v1 ships single-hop.
- **Multi-fire replies** (`on(...).publish(...).repeats()`). Single-fire only in v1.
- **Header-level access in the builder** — builder sees decoded payload only, consistent with matcher inputs (see [ADR-0013](../adr/0013-matcher-strategy-pattern.md)).
- **Custom correlation overrides per reply** — reply's trigger filter and reply injection both use the scope's correlation ID. Tests where the SUT generates a fresh correlation ID mid-flow need either (a) a downstream scope-less helper, or (b) a follow-up PRD extending the correlation contract.
- **Protocol-specific reply helpers** (e.g. a protocol analogue of `expect` / `publish`). Follow-up when a protocol mock actually ships.
- **Removing a centralised-fake alternative.** The two can coexist; consumers migrate per-test. Deprecation, if any, is a separate decision.

### Future Considerations

- **Multi-hop** — `on(A).publish(B).on(C).publish(D)`; the second `on` arms only after the first publish fires.
- **Multi-fire** — `.repeats(n)` or `.until(predicate)` on the chain.
- **Header access** — extend the builder signature to `(payload, envelope)` once envelope access is exposed to matchers too.
- **Scheduled replies** — `publish_after(ms=50, ...)` for modelling upstream latency without a `sleep` in the builder.
- **Stateful bundles** — framework-provided state container for consumer bundles that track sequence numbers, cumulative fills, etc.

---

## Requirements

### Functional Requirements

**Must Have (P0):**
- [ ] `ScenarioBuilder.on(topic, matcher=None)` returns a `ReplyChain` and transitions the scope to `ExpectingScenario`.
- [ ] `ExpectingScenario.on(topic, matcher=None)` returns a `ReplyChain` and remains in `ExpectingScenario`.
- [ ] `ReplyChain.publish(topic, payload)` accepts `dict`, `bytes`, or `Callable[[decoded], dict | bytes]`.
- [ ] `ReplyChain.publish()` returns the scenario in its prior type-state.
- [ ] Calling `ReplyChain.publish()` more than once raises `ReplyAlreadyBoundError`.
- [ ] Replies filter by scope correlation ID the same way expectations do.
- [ ] Static-dict replies auto-inject `correlation_id` unless the builder / static dict sets it explicitly.
- [ ] A reply fires at most once per scope, then deregisters.
- [ ] Builder exceptions are caught, logged at ERROR (with redaction per [ADR-0009](../adr/0009-log-data-classification.md)), and recorded on the `ReplyReport`; the scenario continues.
- [ ] `ScenarioResult.replies` lists one `ReplyReport` per registered reply with: topic, matcher description, candidate count, match count, reply-published flag, builder-error string or `None`.
- [ ] Replies registered in a scope are unregistered on `__aexit__` including on exception.

**Should Have (P1):**
- [ ] Warning logged at scope exit for replies that never matched a message (topic + matcher description included).
- [ ] `ScenarioResult.summary()` includes a reply section when any replies were registered.
- [ ] Debug log entry when a message arrives on a reply topic but correlation-ID filter rejects it (useful for diagnosing cross-scope flakes).

**Nice to Have (P2):**
- [ ] `ReplyChain.publish()` accepts an `async` builder (`Callable[[decoded], Awaitable[dict | bytes]]`).
- [ ] Inline reply description (`.describe("instant fill")`) surfaced in summaries.

### Non-Functional Requirements

**Performance:**
- Reply registration overhead <100μs (comparable to `expect`).
- Reply dispatch adds <200μs on the hot path, dominated by the builder callback.
- No additional quadratic behaviour as replies are added: dispatch is per-topic O(subscribers).

**Reliability:**
- 100% of replies clean up on `__aexit__`, verified by a residue-check test that asserts no subscription is held post-scope.
- Parallel-isolation test with 100 concurrent scopes, identical topic, distinct correlation IDs: each reply fires exactly once for its own scope.

**Observability:**
- Every reply lifecycle event (registered, triggered, rejected, fired, builder-error, deregistered) logs at appropriate level with the scope's correlation ID attached.
- Reply report in `ScenarioResult` distinguishes the four states: `NEVER_ARMED` (registration failed), `ARMED_NO_MATCH` (no candidates arrived), `ARMED_MATCHER_REJECTED` (candidates arrived, matcher rejected all), `FIRED` (fired cleanly), `FIRED_BUILDER_ERROR` (fired, builder raised).

**Compatibility:**
- Python 3.11+ (consistent with [PRD-002](PRD-002-scenario-dsl.md)).
- Compatible with every `Transport` implementation conforming to the five-method Protocol. No new transport requirements.
- Works with both the `MockTransport` unit suite and the `NatsTransport` e2e suite unchanged.

---

## Dependencies

### Internal Dependencies

- **[PRD-002 — Scenario DSL](PRD-002-scenario-dsl.md)** — `on()` extends the type-state builder; `ReplyChain` is a new transient state class alongside the existing four. **Also a Phase 0 blocker:** `with_correlation(field_name)` must be wired through expect + publish before reply filter / injection can be built on top.
- **[ADR-0002 — Scoped registry + correlation IDs](../adr/0002-scoped-registry-test-isolation.md)** — **Phase 0 blocker.** ADR-0002 is still Proposed with "correlation-echo verification against real downstream services" as an open spike. The reply primitive inherits that blocker: if any downstream service drops or rewrites `correlation_id` on any hop, reply parallel safety collapses. The spike unblocks both simultaneously.
- **[ADR-0004 — Dispatcher correlation mediator](../adr/0004-dispatcher-correlation-mediator.md)** — reply filtering reuses the correlation-routing contract.
- **[ADR-0012 — Type-state scenario builder](../adr/0012-type-state-scenario-builder.md)** — extended with one new transient state.
- **[ADR-0013 — Matcher strategy pattern](../adr/0013-matcher-strategy-pattern.md)** — reply triggers use the same matcher Protocol. Matcher descriptions must support log-redaction (ADR-0017 §Security Considerations).
- **[ADR-0014 — Handle result model](../adr/0014-handle-result-model.md)** — replies do **not** return Handles; they return a transient chain object and surface results via `ReplyReport` on `ScenarioResult`.

### External Dependencies

- None beyond the existing framework.

---

## Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Builder exceptions silently swallow the reply and the test times out mysteriously | Medium | Medium | ERROR-level log with redacted triggering payload; builder-error flagged on the reply report; `summary()` surfaces it. |
| Author forgets `.publish()` after `.on()`, leaving a no-op chain | Low | Medium | `ReplyChain.__del__` logs a WARNING if garbage-collected unbound. Static analysis rule in a follow-up. |
| Reply fires on another scope's message because correlation ID is absent | High | Low | Correlation filter identical to `expect`; parallel-isolation test guards against regression. Tests with non-echoing services fall back to serial-per-service, as today. |
| **Target services do not echo `correlation_id` end-to-end through the downstream cascade** (Phase 0 blocker, shared with ADR-0002) | Critical | Unknown until spike | Reply primitive inherits ADR-0002's open blocker. If any downstream service drops the field on any hop, the PRD's line-count and parallel-safety success metrics do not land and the primitive is functionally useless for the cases it was designed for. Decision point before Phase 1 starts. |
| **Builder sets `correlation_id` in its return dict, deliberately or by mistake, publishing into another scope** | High | Medium | [ADR-0018](../adr/0018-reply-correlation-scoping.md) mandates WARN + `correlation_overridden` flag on every override. Consumers wanting hard enforcement wrap registration in an assert-flag-false-at-scope-exit helper. |
| **Contract-test obligation not transferred to consumers** when a consumer bundle replaces a centralised-fake strategy | Medium | High | Consumers must keep their real-adapter contract test running against their bundle's output. Framework cannot own it because the framework no longer emits the message. |
| One-fire semantics trip up tests expecting the reply to keep firing | Medium | Medium | Clear documentation, `summary()` shows fire count, `.repeats()` planned for a follow-up. |
| `ReplyChain` return type confuses static analysis | Low | Medium | Narrow return type is the prior scope type; mypy strict check in CI. |
| Reply + `expect` on the same topic causes surprising ordering | Low | Low | Dispatch order is registration order (current behaviour). Documented. Multi-subscriber per topic is already a supported case. |

---

## Alternatives Considered

### Alternative 1: Single-call reply (`s.react(trigger=..., reply=...)`)

**Description:** One method call takes trigger topic, matcher, reply topic, and reply builder as kwargs.

**Why not chosen:**
- Less readable than the chain for multi-argument cases.
- Forecloses multi-hop (the chain form naturally extends).
- Doesn't match the existing fluent shape of the DSL.

### Alternative 2: Decorator-based replies (`@s.on("topic")`)

**Description:** Register replies by decorating a function inside the scope.

**Why not chosen:**
- Breaks the fluent flow; the scope no longer reads top-to-bottom.
- Harder to reason about scope lifetime (decorated functions outlive the scope's `async with` block).
- Doesn't compose with bundle functions as cleanly.

### Alternative 3: Extend a centralised-fake alternative

**Description:** Keep the reply behaviour centralised in a stateful fake; add a "dynamic strategy" that takes a builder callable.

**Why not chosen:**
- Locks the pattern to one protocol or domain. The whole point is that any adapter-boundary mock benefits from this primitive.
- Keeps the strategy-registration overhead the user called out as being over-complex.
- A framework-level primitive plus a consumer-side bundle is simpler than a central fake with pluggable strategies.

### Alternative 4: Reply-returns-Handle

**Description:** `s.on(...).publish(...)` returns a `Handle` whose fulfillment means "the reply fired".

**Why not chosen:**
- The user explicitly asked for fire-and-forget; a Handle implies assertion and budget.
- Downstream `expect()` already provides the observable effect to assert on.
- The reply report gives visibility without adding a second assertion primitive.

---

## Open Questions

1. **Async builders.** Should `ReplyChain.publish` accept `async` callables in v1 or defer to P2? Current plan: v1 is sync-only; P2 adds async. Anything that needs `await` in the builder can probably be a consumer-side bundle first.
   - **Status:** Open — default P2 unless a concrete need surfaces.
   - **Owner:** Framework

2. **`matcher=None` default semantics.** When `on()` is called without a matcher, does it match every message on the topic, or does it require an explicit `any_()` matcher? Current plan: no-matcher means match-any; matches the ergonomic "just react to this topic" case.
   - **Status:** Open — confirm with first consumer.
   - **Owner:** Framework

3. **Reply ordering when multiple replies target the same topic.** Registration order (first-registered fires first) is the default. Does any realistic test need a different semantics (e.g. priority)?
   - **Status:** Open — default registration order; revisit if a real case emerges.
   - **Owner:** Framework

4. **Scope-less replies on `Harness` directly.** `harness.on(topic, matcher).publish(topic, builder)` for replies that should live for the session, not a scope. Useful for "heartbeat responder" patterns. Current plan: defer. Scope-bound covers the primary use case.
   - **Status:** Deferred
   - **Owner:** Framework

5. **Centralised-fake deprecation path.** If reply bundles cover the majority of cases in a given consumer, is their centralised-fake retained-as-convenience or merged (its strategies re-implemented as bundles)? Decision belongs to the consumer team.
   - **Status:** Open — tracked as a follow-up.
   - **Owner:** Platform

---

## Timeline and Milestones

### Phases

**Phase 0: Unblock correlation contract** (prerequisite — may overlap with Phase 1 if started in parallel)
- **Wire [PRD-002](PRD-002-scenario-dsl.md)'s `with_correlation(field_name)`** through the existing `expect` filter and `Scenario.publish` injection at [packages/core/src/choreo/scenario.py:447](../../packages/core/src/choreo/scenario.py#L447) and [:512-521](../../packages/core/src/choreo/scenario.py#L512-L521). The field name must reach `_Reply` dispatch and reply-stamping without a second hardcoded path. Per [ADR-0018](../adr/0018-reply-correlation-scoping.md) §Timeline, shipping with `"correlation_id"` hardcoded would make the primitive unusable for consumers whose correlation field has a different name.
- **Verify correlation-echo in the target services** (shared blocker with [ADR-0002](../adr/0002-scoped-registry-test-isolation.md)). A spike confirming that every downstream service round-trips the correlation field end-to-end through the cascade. If any hop drops or rewrites the field, the reply primitive inherits ADR-0002's blocker: parallel safety collapses to serial-per-service and the primary PRD success metric (3-line "instant reply" replacing 30-line bespoke wiring) does not land. Decision owner must sign off on this before Phase 1 starts implementation.

**Phase 1: Core primitive** (Target: +1 week after Phase 0 unblock)
- `Scenario.on()` on both pre-publish states ([ADR-0016](../adr/0016-reply-lifecycle.md))
- `ReplyChain.publish()` accepting dict, bytes, and sync callable
- Correlation filter reuse from `expect`, honouring the configured field name from Phase 0
- Fire-once semantics: post-FIRED subscription stays alive for candidate counting, matcher / builder bypassed (per [ADR-0016](../adr/0016-reply-lifecycle.md) §Fire-once enforcement)
- Correlation-override detection: WARN + `correlation_overridden` flag on the report (per [ADR-0018](../adr/0018-reply-correlation-scoping.md) §Correlation-override detection)
- Scope cleanup on `__aexit__`
- Unit tests covering the core happy path, no-match, matcher-rejection, builder exception, parallel-scope isolation

**Phase 2: Observability** (Target: +1 week after Phase 1)
- `ReplyReport` + `ScenarioResult.replies`
- `summary()` reply section
- Warning / debug logging per lifecycle event
- Residue-check test post-scope

**Phase 3: Bundle pattern + docs** (Target: +1 week after Phase 2)
- Two worked consumer-side bundle examples in the docs (instant-reply, reject-with)
- Migration note against a centralised-fake alternative (what moves, what stays)
- Update [framework-design.md](../framework-design.md) §5 DSL design
- E2E reply test against the NATS compose stack

### Key Milestones

- [ ] PRD Approved: TBD
- [ ] ADRs Accepted (ADR-0016 / 0017 / 0018): TBD
- [ ] **Phase 0 blocker cleared: correlation-echo spike signed off** (shared with [ADR-0002](../adr/0002-scoped-registry-test-isolation.md)): TBD
- [ ] **Phase 0 blocker cleared: `with_correlation` wired through expect + publish**: TBD
- [ ] Implementation Started (Phase 1): TBD
- [ ] First reply-replacement scenario passes on `MockTransport`: TBD
- [ ] Parallel-isolation test at 100 scopes passes: TBD
- [ ] Consumer-side bundle pattern worked into docs: TBD

---

## Appendix

### Related Documents

- [framework-design.md](../framework-design.md) §5 (DSL design)
- [context.md](../context.md) §5 (DSL principles)
- [PRD-002 — Scenario DSL](PRD-002-scenario-dsl.md) — type-state builder this extends
- [ADR-0004 — Dispatcher correlation mediator](../adr/0004-dispatcher-correlation-mediator.md)
- [ADR-0012 — Type-state scenario builder](../adr/0012-type-state-scenario-builder.md)
- [ADR-0013 — Matcher strategy pattern](../adr/0013-matcher-strategy-pattern.md)
- [ADR-0014 — Handle result model](../adr/0014-handle-result-model.md)
- [ADR-0016 — Reply lifecycle (scope-bound, fire-once, pre-publish)](../adr/0016-reply-lifecycle.md)
- [ADR-0017 — Fire-and-forget reply results with ReplyReport](../adr/0017-reply-fire-and-forget-results.md)
- [ADR-0018 — Reply correlation scoping (reuse of expect-filter)](../adr/0018-reply-correlation-scoping.md)

### References

- WireMock stateful scenarios — https://wiremock.org/docs/stateful-behaviour/ (inspiration for scope-bound, correlation-scoped reactive mocks)
- Pact provider states — https://docs.pact.io/getting_started/provider_states (inspiration for consumer-side bundle pattern)

### Glossary

- **Reply** — a scope-bound subscriber that reacts to a matched message by publishing a reply.
- **Reply chain** — transient type returned by `on()`; terminated by `.publish(...)`.
- **Builder** — callable passed to `ReplyChain.publish()` that receives the decoded trigger payload and returns the reply payload.
- **Bundle** — a consumer-side helper function that registers one or more replies on a scope; ships with the consumer repo, not the framework.
- **Fire** — one successful match-and-publish cycle. Replies fire at most once per scope in v1.
- **Scope** — `async with harness.scenario(name)` context, lifetime of one test.

---

**Approval Signatures:**

- [ ] Product Owner: _________________ Date: _______
- [ ] Technical Lead: _________________ Date: _______
- [ ] Security Review: _________________ Date: _______
