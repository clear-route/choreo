# 0016. Reply Lifecycle — Scope-Bound, Fire-Once, Pre-Publish Registration

**Status:** Proposed
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-008 — Scenario Replies](../prd/PRD-008-scenario-replies.md) §Proposed Solution, §Requirements

---

## Context

[PRD-008](../prd/PRD-008-scenario-replies.md) introduces a reactive primitive to the Scenario DSL: `s.on(topic, matcher).publish(topic, builder)`. A reply observes a message and publishes a reply. The primary use case is standing in for an upstream external service so that a downstream test can exercise state, audit, and projection logic without a real adapter.

Three internal types appear throughout this ADR. Brief definitions on first mention:

- **`_Reply`** — the internal record carrying a reply's trigger topic, matcher, reply specification, and runtime state. One per `on().publish()` call in a scope.
- **`ReplyChain`** — the transient object returned by `on()` and terminated by `.publish()`. Not user-facing beyond the fluent chain; never survives across statements.
- **`_ReplyState`** — the **runtime** state enum (ARMED / FIRED / FIRED_BUILDER_ERROR). Distinct from the **terminal** `ReplyReportState` defined in [ADR-0017](0017-reply-fire-and-forget-results.md), which is derived at scope exit.

Three interlocking lifecycle questions must be answered together, because they are forced by the same invariants:

1. **When can a reply be registered?** Before `publish()` only, or at any point in the scope?
2. **How many times can it fire?** Once, then deregister — or many times until the scope exits?
3. **Who owns its lifetime?** The scope that registered it, or the Harness across scopes?

Getting these wrong makes replies either unsafe in parallel runs or painful to author. Getting them right is hard to reverse — a fire-many reply can never be safely reduced to fire-once without breaking tests written against it, and a harness-lifetime reply changes teardown semantics across the entire framework.

### Background

- [ADR-0002 — Scoped registry + correlation IDs](0002-scoped-registry-test-isolation.md) establishes `async with scenario_scope` as the cleanup boundary.
- [ADR-0012 — Type-state scenario builder](0012-type-state-scenario-builder.md) defines the four-state builder: `ScenarioBuilder → ExpectingScenario → TriggeredScenario → ScenarioResult`.
- [ADR-0014 — Handle result model](0014-handle-result-model.md) shows how `expect()` registers a subscription that survives scope teardown as a passive result reference.
- [PRD-008 §User Stories](../prd/PRD-008-scenario-replies.md) commits to correlation-scoped replies and scope-bound cleanup.

### Problem Statement

When can a reply be registered, how many times may it fire, and at what boundary is its subscription torn down?

### Goals

- **Parallel safety.** Registering a reply in scope A must not affect scope B, even on identical topics.
- **Determinism.** A test reading `on(...).publish(...)` has one clear meaning; no surprising implicit re-firing.
- **Cleanup.** No reply outlives the scope that registered it, including on exception.
- **Fit the type-state model.** Reply registration slots into [ADR-0012](0012-type-state-scenario-builder.md) without adding a fifth state class.
- **Leave multi-hop and multi-fire addable later** without breaking the v1 signature.

### Non-Goals

- Deciding the result / observability model (covered by [ADR-0017](0017-reply-fire-and-forget-results.md)).
- Deciding correlation filtering (covered by [ADR-0018](0018-reply-correlation-scoping.md)).
- Replacing `expect()`. Replies and expectations coexist on the same topics.
- Harness-scoped "always-on" replies (e.g. persistent heartbeat responders). Deferred; see Notes.

---

## Decision Drivers

- **Correctness over cleverness.** A reply that quietly fires twice because the SUT re-sends the trigger under retry is a debugging trap.
- **Authoring clarity.** The common case reads as one statement; advanced cases don't punish the common case with ceremony.
- **Cleanup guarantee.** `async with` semantics must remain the single source of truth for when subscriptions end.
- **Future extensibility.** Multi-hop (chain continuation after `.publish()`) and multi-fire (`.repeats(n)`) should add without renaming or repurposing v1 behaviour.
- **Type-state compatibility.** Reply registration must not break the expect-before-publish invariant ([ADR-0012](0012-type-state-scenario-builder.md)).

---

## Considered Options

### Option 1: Fire-once, pre-publish only, scope-bound (chosen)

**Description:** `on()` is callable on `ScenarioBuilder` and `ExpectingScenario`. Calling it transitions `ScenarioBuilder` to `ExpectingScenario` (replies are a form of subscription, same as `expect`). A reply fires at most once — after its first accepted match it transitions to `FIRED` (or `FIRED_BUILDER_ERROR`) and stops publishing replies, but its subscription stays alive until scope exit so that any subsequent matching messages are counted (without matcher evaluation or reply) for observability. Any reply still subscribed at scope exit is torn down by `__aexit__`. `TriggeredScenario` and `ScenarioResult` do not expose `on()`.

**Pros:**
- Covers the primary use case (one request, one reply per scope) with no configuration.
- Scope-bound cleanup inherits [ADR-0002](0002-scoped-registry-test-isolation.md)'s existing teardown path; no new lifetime machinery.
- Pre-publish registration fits the expect-before-publish invariant; the scope is fully armed before the trigger fires.
- Multi-fire is addable later as an opt-in modifier (`.repeats(n)`) that relaxes the post-FIRED bypass; default behaviour unchanged.
- Multi-hop chain continuation is source-compatible for the single-expression `s.on(...).publish(...)` form; callers that reassign `s = s.on(...).publish(...)` will need to account for a union return type under mypy strict (see Negative Consequences).

**Cons:**
- Tests whose SUT re-sends the trigger (e.g. retry on timeout) need an explicit `.repeats()` once that modifier exists; until then, the second trigger is recorded on the report (post-FIRED candidates increment `candidate_count`) but no second reply is published. Test authors see `FIRED` with `candidate_count > 1` and can debug. See [ADR-0017](0017-reply-fire-and-forget-results.md) for the terminal report shape.
- "Pre-publish only" forecloses mid-scenario reply registration. The follow-up use case (register a second reply in response to the first publish's cascade) needs either post-publish registration — a follow-up ADR — or is expressed by declaring both replies upfront.

### Option 2: Fire-many with manual deregistration

**Description:** Replies fire every time a matching message arrives. The chain returns an object with `.off()` or `.cancel()` that deregisters manually, plus an auto-cleanup on scope exit.

**Pros:**
- Natural fit for tests that expect multiple identical triggers (polling, keep-alive).
- No surprise when the SUT re-sends.

**Cons:**
- Every authored reply carries the burden of "will I fire twice for the same flow?" for the author to reason about — the common case is once, so the default should be once.
- Accidental double-fires lead to cascaded duplicate replies; the SUT sees two execution reports for one order.
- `.off()` is a new state-management primitive; forgetting to call it leaves the reply armed until scope exit anyway, so the manual control provides negligible safety gain.

### Option 3: Harness-bound replies (persistent across scenarios)

**Description:** Replies are registered on the `Harness` itself, not the scope. They outlive any one scenario and fire on any scope's matching message.

**Pros:**
- Fits the "standing mock" pattern (heartbeat responder that should run across the whole suite).
- Register once per session, not once per scenario.

**Cons:**
- Defeats the parallel-isolation guarantee — two scenarios running concurrently with identical topics now share a reply, and a reply meant for A can reach B.
- Breaks [ADR-0002](0002-scoped-registry-test-isolation.md)'s scope-bound cleanup model. Teardown becomes global, which is the opposite of what the framework elsewhere enforces.
- Puts correlation handling onto each caller of the harness registration — the reply cannot know which scope to reply under.

### Option 4: Post-publish registration allowed

**Description:** `on()` is available on every type-state including `TriggeredScenario`. A reply registered after the trigger can still fire on messages that arrive while `await_all()` is waiting.

**Pros:**
- Lets a test arm a follow-up reply in reaction to the first publish's cascade (e.g. after first `publish`, see one intermediate message and then arm the reply).
- More flexible authoring.

**Cons:**
- Invites race conditions: a reply registered after the trigger may miss early messages; the author must understand the dispatch order. The whole point of type-state is to make order mistakes impossible ([ADR-0012](0012-type-state-scenario-builder.md)).
- The realistic form of this need is multi-hop, which is a cleaner API (`on(A).publish(B).on(C).publish(D)` arms C automatically after B fires) and is listed as a future extension.
- Defers the authoring cliff rather than removing it: the author now has to pick between pre-publish and post-publish, which is cognitive load the primitive does not need.

---

## Decision

**Chosen Option:** Option 1 — Fire-once, pre-publish only, scope-bound.

### Rationale

- Option 1 keeps the API minimum viable while leaving the extension points open. `.repeats(n)` is a pure addition, not a redefinition. Multi-hop continuation is a pure addition on `ReplyChain.publish()`'s return type.
- Option 2's default of fire-many makes the common case pay for the uncommon case. The reply report (see [ADR-0017](0017-reply-fire-and-forget-results.md)) already surfaces candidate counts, so tests that care about repeated triggers can detect them without needing multi-fire as default.
- Option 3 breaks the parallel-isolation guarantee the PRD's success metrics depend on. A harness-bound reply cannot know which scope's correlation to echo; the reply either leaks across scopes or requires per-reply correlation extraction that the scope-bound design already handles for free.
- Option 4 re-opens the race-condition class of bugs that [ADR-0012](0012-type-state-scenario-builder.md) structurally closed. The authoring flexibility it offers is served better by the explicit multi-hop follow-up.

The reading is: **a reply is a scoped, single-fire subscription that fires before the scenario's trigger and dies when the scope dies.**

---

## Consequences

### Positive

- Scope cleanup is an existing, tested code path; no new teardown state.
- The reply report (see [ADR-0017](0017-reply-fire-and-forget-results.md)) distinguishes `FIRED` with `candidate_count == 1` ("fired on the one trigger") from `FIRED` with `candidate_count > 1` ("fired, but more triggers arrived after"). The post-FIRED subscription stays alive specifically to make this observable, not silent.
- The scenario's externally-visible states stay at four ([ADR-0012](0012-type-state-scenario-builder.md)); the transient `ReplyChain` is an implementation type invisible to users at the scope boundary.
- Parallel isolation is an emergent property of scope-bound registration + correlation filtering, not a feature requiring per-reply configuration.

### Negative

- Fire-many tests (polling, heartbeat) cannot be expressed in v1. Those tests either wait for the `.repeats()` follow-up or use a manual `harness.subscribe()` callback. The reply report flags the second trigger so the symptom is visible.
- Multi-hop tests cannot be expressed in v1. Authors work around by declaring all replies upfront; the report clarifies ordering. Adding multi-hop later will change `ReplyChain.publish()`'s return type to a union (`ExpectingScenario | ReplyChain`), which is source-compatible for chained callers but needs an explicit cast under mypy strict for callers that reassign to the scope variable.
- Post-publish reply registration is rejected at the type level. A test that wants this gets a mypy error and must restructure. Documented in the DSL reference.

### Neutral

- `on()` transitioning `ScenarioBuilder` → `ExpectingScenario` is consistent with `expect()`; the two subscription primitives behave the same way for type-state purposes.
- Cleanup order at `__aexit__`: replies deregister before expectations by registration order reversal (last-in-first-out). No functional consequence because subscriptions are independent.

### Security Considerations

- **Silent-fire risk.** A reply firing on a message from a misclassified correlation ID could publish sensitive data to a scope that is not entitled to see it. Mitigated by the correlation filter ([ADR-0018](0018-reply-correlation-scoping.md)) and by scope boundary enforcement ([ADR-0002](0002-scoped-registry-test-isolation.md)).
- **Cleanup failure.** A reply left subscribed after scope exit could fire on the next scenario's trigger, corrupting state. Mitigated by `__aexit__` deregistering in a `try / finally`; residue check in the test suite asserts no subscriptions remain post-scope.
- **Builder as code injection surface.** The builder is a callable chosen by the test author; replies do not deserialise callables from payloads. No dynamic-code surface introduced.
- **Fire-once TOCTOU.** The state transition `ARMED → FIRED` and the suppression of further replies must be atomic from the dispatcher's perspective, or two queued candidates could both enter the matcher-and-publish path. The dispatcher ([ADR-0004](0004-dispatcher-correlation-mediator.md)) already serialises callbacks via `loop.call_soon_threadsafe` ([ADR-0003](0003-threadsafe-call-soon-bridge.md)), giving the same serialisation guarantee the filter already relies on. The implementation still guards entry with a `state == ARMED` check; see §Fire-once enforcement.
- **Malformed-payload denial of the stand-in.** A SUT that emits a payload the builder cannot handle crashes into `FIRED_BUILDER_ERROR`, which deregisters the reply path for the rest of the scope. This is a bounded, observable failure (not a silent wedge), but it means one malformed message permanently disables the stand-in for that scope. Consumer-owned reply bundles should be defensive against unexpected input shapes; the framework does not retry the builder.

---

## Implementation

### Type-state transition

```
ScenarioBuilder.on(topic, matcher=None) -> ReplyChain
    # internally flips scope to ExpectingScenario state
    # (same effect as ScenarioBuilder.expect)

ExpectingScenario.on(topic, matcher=None) -> ReplyChain
    # scope already in expecting state; no transition

ReplyChain.publish(reply_topic, payload) -> ExpectingScenario
    # binds the reply spec; chain is now terminal; returns prior-state scope

# TriggeredScenario / ScenarioResult: .on attribute does not exist
```

### Fire-once enforcement

```
class _ReplyState(StrEnum):
    ARMED               = "armed"
    FIRED               = "fired"
    FIRED_BUILDER_ERROR = "fired_builder_error"


class _Reply:
    trigger_topic: str
    matcher: Matcher | None
    reply_topic: str
    reply_spec: dict | bytes | Callable[[Any], dict | bytes]
    state: _ReplyState           # runtime state, see above
    candidate_count: int = 0
    match_count: int = 0
    _deregister: Callable[[], None]
```

The runtime enum has three members. The terminal report enum in [ADR-0017](0017-reply-fire-and-forget-results.md) (`ReplyReportState`) has four members; it is derived from the runtime state plus `candidate_count` / `match_count` at scope exit.

When dispatch routes a message to a reply:

1. `candidate_count += 1` (unconditional — counting starts the instant a candidate arrives, regardless of runtime state).
2. If `state != ARMED`: return (post-FIRED bypass — no matcher evaluation, no builder invocation, no reply). The incremented count surfaces on the terminal report.
3. Matcher evaluated (or auto-pass if `matcher is None`).
4. On match: re-check `state == ARMED` under the dispatcher's single-threaded guarantee (see [ADR-0003](0003-threadsafe-call-soon-bridge.md)); transition state to `FIRED`; `match_count += 1`; builder invoked (or static payload used); reply published with scope correlation auto-injected.
5. On builder exception during step 4: state transitions to `FIRED_BUILDER_ERROR`; `match_count` still incremented (the match happened; only the publish failed); error recorded on the report (see [ADR-0017](0017-reply-fire-and-forget-results.md)); `reply_published == False`.
6. On matcher rejection: state unchanged; reply remains `ARMED`.

### Cleanup on `__aexit__`

Scope's `__aexit__` iterates registered replies (in LIFO order) and calls `_deregister()`. Replies in any state — `ARMED`, `FIRED`, `FIRED_BUILDER_ERROR` — are safe to deregister (no-op if already deregistered, though fire-once v1 only deregisters at scope exit).

### Migration Path

Not applicable — greenfield primitive.

### Timeline

- Phase 1 (PRD-008): fire-once, pre-publish-only, scope-bound as described.
- Phase 2 (follow-up ADR): multi-fire via explicit `.repeats(n)` modifier. No change to default.
- Phase 3 (follow-up ADR): multi-hop chain continuation. `ReplyChain.publish()` returns a union including `ReplyChain` when further `.on()` is chained. Single-expression callers (`s.on(...).publish(...)`) are source-compatible. Callers that reassign (`s = s.on(...).publish(...)`) will need an explicit cast under mypy strict once the union lands.

---

## Validation

### Success Metrics

- **Parallel isolation test** — 100 concurrent scopes, identical topic, distinct correlation IDs, one reply each: each reply fires exactly once for its own scope's trigger. Target: zero cross-scope fires.
- **Cleanup residue test** — scope enters, registers reply, raises inside the `async with` block: after exit, transport reports zero active subscriptions attributable to the scope. Target: zero leaked subscriptions.
- **Type-state rejection test** — `TriggeredScenario.on` access fails at mypy strict. Target: mypy strict errors with "has no attribute 'on'".
- **Fire-once observable test** — trigger topic receives two matching messages; reply fires on first, second bypasses matcher and builder per §Fire-once enforcement step 2. Target: one reply published, report shows terminal state `FIRED` with `candidate_count == 2`, `match_count == 1`, `reply_published == True`.

### Monitoring

- Dispatcher emits a DEBUG entry for every reply dispatch (matched / rejected / post-fire).
- Test-suite CI gate on the cleanup residue test.
- Mypy strict gate in CI.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scope cleanup boundary reused.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher routes reply triggers.
- [ADR-0012](0012-type-state-scenario-builder.md) — Type-state extended with `on()` on pre-publish states.
- [ADR-0014](0014-handle-result-model.md) — Deliberately not extended; see [ADR-0017](0017-reply-fire-and-forget-results.md).
- [ADR-0017](0017-reply-fire-and-forget-results.md) — Reply result model.
- [ADR-0018](0018-reply-correlation-scoping.md) — Correlation filter for reply triggers.

---

## References

- [PRD-008 — Scenario Replies](../prd/PRD-008-scenario-replies.md)
- WireMock stateful scenarios — https://wiremock.org/docs/stateful-behaviour/

---

## Notes

- **Deferred — harness-bound replies.** A session-wide heartbeat responder cannot be expressed in v1. If this need emerges, a follow-up ADR will define a separate `Harness.on()` surface with explicit correlation handling. **Owner:** Platform / Test Infrastructure.
- **Deferred — multi-fire `.repeats(n)`.** Tracked for the first test that cannot be expressed without it. **Owner:** Framework.
- **Deferred — multi-hop chains.** Tracked for the first consumer case requiring two-phase reply flows. **Owner:** Framework.
- **Deferred — post-publish registration.** Rejected in v1; revisit only if multi-hop proves insufficient. **Owner:** Framework.

**Last Updated:** 2026-04-17
