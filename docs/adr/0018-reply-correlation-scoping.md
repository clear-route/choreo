# 0018. Reply Correlation Scoping — Reuse of Expect-Filter Contract

**Status:** Proposed
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-008 — Scenario Replies](../prd/PRD-008-scenario-replies.md) §User Story 2, §Functional Requirements

---

## Context

Parallel test isolation in the Harness relies on one mechanism: correlation-ID-scoped routing. Each scope generates a unique `correlation_id`; publishes inject it; inbound messages route to the scope whose ID matches. [ADR-0002](0002-scoped-registry-test-isolation.md) defines the scope boundary; [ADR-0004](0004-dispatcher-correlation-mediator.md) defines the dispatcher that enforces it.

Replies introduced in [PRD-008](../prd/PRD-008-scenario-replies.md) are a new subscription primitive. They raise three correlation questions:

1. **Trigger filtering.** When a message arrives on a reply's trigger topic, which replies (across all live scopes) should see it?
2. **Reply correlation.** When the reply publishes its reply, what correlation ID does the reply carry?
3. **SUT-originated correlation.** Some flows involve the SUT generating a fresh correlation chain (a new correlation_id for a child request, a reply_id that the test never saw). Should replies participate in that?

These are load-bearing for the parallel-isolation guarantee. Getting them wrong means a reply in scenario A fires on scenario B's message, publishing a reply into B's subscription set. Cross-scope contamination is exactly the flake class the harness exists to eliminate.

This ADR records the decision to **reuse the existing expect-filter contract** ([ADR-0004](0004-dispatcher-correlation-mediator.md)) for replies, and to **defer SUT-originated correlation** to a future ADR rather than ship it in v1.

> **Inherited dependency — correlation-echo blocker.** The chosen option only works end-to-end if every hop between the scope's `publish()` and the reply's trigger topic preserves the `correlation_id` field. [ADR-0002](0002-scoped-registry-test-isolation.md) is still **Proposed** with "correlation-echo verification against real downstream services" as an open blocker. The reply primitive inherits that blocker: if any downstream service drops or rewrites `correlation_id` on any hop in the flow PRD-008 motivates, the reply fires under `ARMED_NO_MATCH` and the PRD's parallel-safety success metric collapses. Resolution path: the same spike that unblocks ADR-0002 unblocks this ADR; no separate work.

### Background

- [ADR-0002 — Scoped registry + correlation IDs](0002-scoped-registry-test-isolation.md) defines the scope and its correlation ID.
- [ADR-0004 — Dispatcher correlation mediator](0004-dispatcher-correlation-mediator.md) defines the single routing implementation; inbound dispatch is `correlation_id → scope`.
- ADR-0011 (planned — see [docs/adr/README.md §Open ADRs](README.md)) will address correlation cross-field sanity when the SUT misroutes IDs. Not yet written.
- Current `Scenario.publish()` auto-injects `correlation_id` into dict payloads unless the author overrides it (see [packages/core/src/core/scenario.py:447](../../packages/core/src/core/scenario.py#L447)).
- Current expectation filter extracts `correlation_id` from inbound dicts and ignores non-matching messages (see [packages/core/src/core/scenario.py:512-521](../../packages/core/src/core/scenario.py#L512-L521)).

### Problem Statement

What correlation ID does a reply use to filter its trigger, and what correlation ID does it stamp on its reply?

### Goals

- **Parallel isolation.** 100 concurrent scopes can register the same reply shape and each one fires for its own scope only.
- **No new correlation contract.** Replies reuse what expect already does; no new extractor registry, no new dispatcher interface.
- **Reply consistency with `Scenario.publish()`.** A reply's reply is stamped the same way an explicit `s.publish()` call would be.
- **Observable failure mode.** A test whose SUT does not echo correlation sees a clear "no candidates" state on the reply report ([ADR-0017](0017-reply-fire-and-forget-results.md)), not a silent mismatch.

### Non-Goals

- **SUT-originated correlation chains.** Tests where the SUT mints a new correlation ID mid-flow (child requests, fresh reply IDs) are explicitly out of scope for v1. See Notes for the deferral.
- **Header-based correlation.** The current correlation contract uses a payload field. Introducing headers would change the contract for every subscription primitive. Out of scope.
- **Per-reply correlation overrides.** `on(..., correlation="echo")` or similar. Out of scope; see Option 3 for why.
- **Adversarial SUT defence.** Tests against a buggy SUT that deliberately returns the wrong correlation ID. Covered by planned ADR-0011.

---

## Decision Drivers

- **Don't break what works.** The existing correlation contract handles expectations correctly; a new primitive should not destabilise it.
- **Load-bearing invariant.** Parallel isolation is the difference between a 10-minute suite and a 100-minute suite. Any weakening must be justified by a concrete need.
- **Principle of least surprise.** A reply's reply should behave like `s.publish()` does today; `on().publish()` is the same publish, just triggered reactively.
- **Observability of the edge case.** When the correlation contract is violated (SUT doesn't echo), the test must fail loudly, not silently.
- **Reversibility.** The decision must not foreclose the SUT-originated-correlation follow-up.

---

## Considered Options

### Option 1: Reuse scope correlation for trigger and reply (chosen)

**Description:** The reply's trigger filter is identical to the expectation filter: the dispatcher's correlation-ID lookup routes inbound messages to scopes; within a scope, each reply's callback runs the matcher only on messages routed to that scope. The reply's reply payload has the scope's correlation ID auto-injected into dict payloads, same rule as `Scenario.publish()`. Bytes payloads pass through without injection. Tests whose SUT does not echo correlation see the reply's state as `ARMED_NO_MATCH` on the report and fall back to the existing serial-per-service strategy ([ADR-0002](0002-scoped-registry-test-isolation.md) §Rationale).

**Pros:**
- Zero new contract surface. The dispatcher is unchanged; the filter is the filter that `expect` already uses.
- Parallel isolation is inherited, not re-derived. The existing cross-scope tests already cover the guarantee.
- Reply stamping is consistent with `Scenario.publish()`; authors who have learned one have learned the other.
- Fallback for non-echoing SUTs is the same fallback that expectations use — no new mode to document.
- Observability is automatic: the reply report ([ADR-0017](0017-reply-fire-and-forget-results.md)) distinguishes "never saw a candidate" from "saw candidates, matcher rejected", so the echo failure is diagnosable.

**Cons:**
- Tests where the SUT generates a new correlation ID (e.g. fresh reply_id chain) cannot be expressed. The reply fires only under the scope's correlation, so the new-ID child flow is invisible to it. Mitigated by deferring this need to a follow-up ADR and documenting the limitation.

### Option 2: First-match correlation capture

**Description:** The reply is registered without a correlation filter. When the first message matching the trigger topic + matcher arrives (from any correlation), the reply captures that correlation ID and pins all subsequent matching messages and the reply to it.

**Pros:**
- Handles the "test doesn't know the correlation upfront" case without additional API.
- Single match ever; fire-once semantics ([ADR-0016](0016-reply-lifecycle.md)) prevent runaway.

**Cons:**
- **Breaks parallel isolation.** Reply A in scope A sees a message from scope B first, captures B's correlation, then fires a reply into B's subscription set — exactly the flake class this framework exists to prevent.
- The failure mode is non-deterministic: which scope's message arrives first depends on thread scheduling.
- Undermines the dispatcher-as-mediator model ([ADR-0004](0004-dispatcher-correlation-mediator.md)). Inbound messages would have to be broadcast to all live replies rather than routed.
- Mitigations (e.g. restrict first-match to scope-visible messages) collapse the option back to Option 1 with more machinery.

### Option 3: Explicit per-reply correlation parameter

**Description:** `on(topic, matcher, correlation="extract-from-this-field")` lets the test configure correlation per reply. Accepts a string literal, a callable extractor, or the sentinel `scope` (default) meaning "use the scope correlation".

**Pros:**
- Gives tests full control when the SUT's correlation behaviour deviates.
- Self-documenting at the call site.

**Cons:**
- Introduces a second correlation contract inside the framework: one for expect (fixed: scope correlation), one for replies (configurable). Debugging a mismatch now requires the author to know which reply is pointing at which correlation field.
- The common case (scope correlation, no echo issues) pays a cognitive cost for the rare case.
- Does not actually solve SUT-originated correlation cleanly; the extractor runs on one message but the reply needs to stamp a freshly generated ID that the extractor has no knowledge of.
- The honest version of this need is a multi-hop reply chain that carries state forward. That's a separate future ADR; half-solving it here locks in the wrong shape.

### Option 4: Header-based correlation as alternative transport

**Description:** Introduce a header-level correlation in the transport Protocol. Replies and expectations can opt into header correlation instead of payload-field correlation.

**Pros:**
- Decouples correlation from payload schema; tests using binary / protobuf payloads no longer need a library-injected field.
- Matches how some production message buses carry correlation today.

**Cons:**
- Changes the transport Protocol — affects every transport, every test, every allowlist.
- Not a PRD-008 problem. If the framework needs header correlation, it should be a separate decision about the transport contract, not ridden in on a reply primitive.
- v1 replies don't need it; payload correlation works.

---

## Decision

**Chosen Option:** Option 1 — Reuse the scope correlation ID for both trigger filter and reply stamping.

### Rationale

- Option 1 preserves the single correlation contract. One invariant means one place to reason about, one place to test, one place to break.
- Option 2's parallel-isolation failure is fatal. Non-deterministic cross-scope contamination is a worse flake class than whatever problem it tries to solve.
- Option 3 fragments the contract for a use case that's more honestly addressed by multi-hop replies. Half-measures here make the future migration harder.
- Option 4 is the right decision to make — but not in this ADR. If the framework wants header correlation, that's a transport-contract change that affects `expect`, `publish`, allowlists, dispatcher, and every existing consumer.
- The reply report's `ARMED_NO_MATCH` state ([ADR-0017](0017-reply-fire-and-forget-results.md)) makes the echo-failure mode diagnosable without adding configuration.

**Reading:** a reply filters by its scope's correlation ID, stamps its reply with the same ID, and makes the correlation echo assumption fail visibly rather than silently.

---

## Consequences

### Positive

- Parallel isolation is inherited without new machinery. The existing 100-scope isolation test covers replies the moment they're scope-registered with correlation routing.
- The dispatcher ([ADR-0004](0004-dispatcher-correlation-mediator.md)) does not gain a new input or a new routing path.
- Test authors writing `on()` reason about it with the same mental model as `expect()`. Documentation can point at the same correlation section.
- Tests against non-echoing SUTs degrade gracefully: the reply shows `ARMED_NO_MATCH`, tests fail with a clear reason, and the existing serial-per-service fallback ([ADR-0002](0002-scoped-registry-test-isolation.md)) remains available.

### Negative

- Tests where the SUT generates a new correlation chain (child request with a new correlation_id) cannot be expressed in v1. Workaround: pre-declare both replies upfront with both correlation IDs, which requires the test to know the SUT's ID-minting scheme. Not always possible. Mitigated by deferring to a follow-up ADR; documented in PRD-008 §Non-Goals.
- "Reuse the filter" sounds like a non-decision; that's precisely the point. Reviewers may ask "did you really consider alternatives?" — documented in Considered Options.

### Neutral

- Reply injection uses `dict.setdefault("correlation_id", scope_id)` — identical rule to `Scenario.publish()`. Authors can override by setting `correlation_id` explicitly in the builder's return value (useful for tests that deliberately send a different ID to verify the SUT's correlation handling).
- Non-dict replies (bytes) skip injection, same as current `publish()`. Consumers using binary codecs must inject correlation in the builder themselves or — preferably — use a codec that handles it.
- The correlation field name remains hardcoded as `"correlation_id"` today. Making it per-scope configurable is a separate ADR (see Notes).

### Security Considerations

- **Cross-scope disclosure.** The primary risk addressed. A reply registered in scope A must never publish a reply visible to scope B's subscribers under A's correlation. The chosen option prevents this by filtering triggers through the dispatcher's correlation lookup — A's reply sees only A's messages, so A's reply is only a reply to A's own trigger.
- **Scope-boundary enforcement.** Reuses [ADR-0002](0002-scoped-registry-test-isolation.md)'s cleanup guarantees. A reply cannot outlive its scope and therefore cannot see another scope's messages post-teardown.
- **Builder does not see correlation of other scopes.** The builder receives the decoded triggering payload, which by construction was routed to this scope only. No information leak from other scopes' correlations.
- **Correlation override is noisy, not silent.** A builder that sets `correlation_id` in its return dict to a value other than the scope's correlation is a cross-scope publishing primitive in disguise — not malicious, but easy to get wrong (scope IDs leak into log lines, shared fixtures, error dumps, so a bug could target another live scope by accident). The framework logs a WARNING on every override and sets a `correlation_overridden: bool = True` flag on the `ReplyReport` ([ADR-0017](0017-reply-fire-and-forget-results.md)). The override is not hard-blocked: Success Metric 4 (§Validation) depends on deliberate override for testing the SUT's correlation handling. A consumer that wants hard enforcement can wrap the reply registration in a helper that asserts the flag remains `False` at scope exit.
- **SUT replays another scope's correlation.** A buggy SUT (or a fixture that accidentally reuses a correlation template across scopes) could emit a message stamped with scope B's ID on a topic scope A's reply is watching. The dispatcher's `correlation_id → scope` lookup ([ADR-0004](0004-dispatcher-correlation-mediator.md)) routes the message to B — so A's reply never sees it and never fires. This is the intended behaviour of the chosen option; B's `ARMED_MATCHER_REJECTED` report surfaces the drift if the matcher rejects the replay. The framework does not attempt to detect correlation collisions across scopes; planned ADR-0011 will address adversarial cases.
- **Missing-correlation disclosure.** A message with no `correlation_id` field is silently ignored by the filter. This is consistent with `expect`; it does not create a new disclosure path.

---

## Implementation

### Filter reuse

The scenario-internal callback already does:

```
correlation = decoded.get("correlation_id") if isinstance(decoded, dict) else None
if correlation != self._context.correlation_id:
    return   # silently ignored; optional DEBUG log
```

Reply dispatch uses the same guard. Implemented once in the scope's shared subscription builder, not duplicated per subscription type.

### Reply stamping

`ReplyChain.publish(topic, payload_spec)`:

- `payload_spec: dict` → static payload. On fire, framework calls `dict.setdefault("correlation_id", scope_correlation_id)` before transport publish.
- `payload_spec: bytes` → passes through unchanged.
- `payload_spec: Callable[[decoded], dict | bytes]` → called with decoded trigger. Return value subject to the same `setdefault` rule if dict.

### Correlation-override detection

Before transport publish, the framework compares the outgoing dict's `correlation_id` to the scope's. If they differ:

1. A WARNING line is logged through the logger with the trigger topic, reply topic, scope correlation, and outgoing correlation — subject to [ADR-0009](0009-log-data-classification.md) classification rules for correlation values.
2. The reply's `ReplyReport.correlation_overridden` flag is set to `True`.
3. The publish proceeds.

This is observability, not enforcement. Tests that deliberately test the SUT's correlation handling (§Validation Success Metric 4) rely on being able to override. Consumers who want enforcement wrap registration in a helper that asserts `correlation_overridden == False` post-scope.

### Debug visibility

Dispatcher logs a DEBUG line for every inbound message whose correlation does not match any live scope. This already exists for expectations ([ADR-0004 §Implementation](0004-dispatcher-correlation-mediator.md) surprise log). Reply mismatches reuse the same surprise log path.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (PRD-008): filter + reply stamping as described; correlation-override detection; **wire [PRD-002](../prd/PRD-002-scenario-dsl.md) `with_correlation(field_name)` through reply filter and injection** so that consumers whose correlation field is named differently to `"correlation_id"` work out of the box. This is a dependency, not a Phase 2 item — shipping with the field name hardcoded to `"correlation_id"` would make the primitive unusable for consumers that use another field name.
- Phase 2 (optional): if a follow-up ADR adds SUT-originated correlation support, reply API gains a new opt-in (e.g. `on(..., correlation=from_field("reply_id"))`). The default remains Option 1.

---

## Validation

### Success Metrics

- **Parallel isolation at 100 scopes.** Same test used for expectations; extended to register one reply per scope on an identical trigger topic. Each reply fires exactly once for its own scope's trigger; zero cross-scope fires. Target: 0 violations across 100 runs.
- **Non-echoing SUT diagnosis.** Test whose mock triggers a message without the correlation field: reply state is `ARMED_NO_MATCH`, `candidate_count == 0`, scope WARNING log names the reply. Target: report state matches; log contains trigger topic.
- **Reply stamping round-trip.** Reply fires; subsequent `expect` on the reply topic resolves against a message whose `correlation_id` equals the scope ID. Target: Handle resolves to PASS.
- **Override path.** Builder returns `{"correlation_id": "OTHER", ...}`. Reply publishes with the override; in-scope `expect` does not resolve. Target: behaviour matches the documented override semantics.

### Monitoring

- CI gate on the parallel isolation test.
- Dispatcher DEBUG logs inspected in the non-echoing diagnosis test.
- No runtime metrics beyond existing dispatcher counters.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — Scope boundary and correlation ID allocation.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher is the single correlation → scope lookup; replies reuse it.
- ADR-0011 (planned) — Adversarial / buggy SUT handling. Will address cases where the SUT misroutes correlation. Tracked in [docs/adr/README.md §Open ADRs](README.md).
- [ADR-0016](0016-reply-lifecycle.md) — Reply lifecycle; fire-once depends on the correlation filter correctly identifying "the one trigger for this scope".
- [ADR-0017](0017-reply-fire-and-forget-results.md) — Reply reports surface correlation mismatches as `ARMED_NO_MATCH`.
- [PRD-008](../prd/PRD-008-scenario-replies.md) — User Story 2 drives this decision.

---

## References

- [framework-design.md §10 (Dispatcher)](../framework-design.md)
- [context.md §6 (Parallel execution strategy)](../context.md)
- Current correlation filter — [packages/core/src/core/scenario.py:512-521](../../packages/core/src/core/scenario.py#L512-L521)
- Current correlation injection — [packages/core/src/core/scenario.py:447](../../packages/core/src/core/scenario.py#L447)

---

## Notes

- **Deferred — SUT-originated correlation chains.** Tests where the SUT mints a new correlation ID that the test does not know up front (child requests, new reply_id chains) are not supported in v1. This is the single largest limitation of the chosen option. When a real consumer test requires it, a follow-up ADR will define the opt-in mechanism — likely a multi-hop chain primitive that carries the triggering message's extracted field forward as the correlation for subsequent replies. **Owner:** Platform / Test Infrastructure.
- **Phase 1 dependency — configurable correlation field name.** Promoted from Deferred to a Phase 1 dependency (see §Timeline). [PRD-002](../prd/PRD-002-scenario-dsl.md)'s `with_correlation(field_name)` must be wired through reply filter and injection before this ADR moves from Proposed to Accepted; the primitive cannot ship with `"correlation_id"` hardcoded because some consumers will use a different field name. **Owner:** Framework.
- **Deferred — header-based correlation.** Separate transport-contract decision. Not a PRD-008 concern. **Owner:** Platform.

**Last Updated:** 2026-04-17
