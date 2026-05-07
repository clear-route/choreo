# 0027. Stage Coordinator — Integration Test Plan (Negative Behaviours)

**Status:** Active — Groups A-E shipped, Group F next
**Date:** 2026-05-04
**Owner:** Implementing engineer (TDD red phase)
**Drives:** PRD-011, ADR-0027

---

## Implementation status

This plan is the input that drives implementation. As groups land, scenarios may
split, additional regression tests may join, and the production code may evolve
in ways the original plan did not anticipate. The truth-of-record for what is
actually shipped is `packages/core/tests/integration/`; this plan is the
specification, not the manifest.

**Plan-vs-implementation deltas (2026-05-04):**

- **B5 split into B5a + B5b.** The plan's single B5 conflated "second disconnect
  does not raise" with "second disconnect does not re-invoke harness disconnect".
  The shipped tests are
  `test_stage_disconnect_should_not_raise_when_called_a_second_time` (B5a, the
  surface contract) and
  `test_stage_disconnect_should_not_re_invoke_harness_disconnect_on_second_call`
  (B5b, the call-count observable via `_RecordingMockTransport`).
- **D4 split into D4a + D4b.** Same pattern: idempotency-after-failure and
  terminal-state-after-failure are separate behaviours.
- **A6 parametrises into 5 inputs** (None / int / bytes / float / empty list).
  One scenario, five test ids. This is correct one-behaviour-per-test discipline,
  not plan drift.
- **Group N's N1+N2 (boundary brackets) are subsumed by A8+A9.** A8
  (`...returning_an_oversized_string`) covers N2 — one char over the
  `_MAX_WIRE_ID_LEN` limit. A9
  (`...accept_a_bridge_to_wire_at_exactly_the_length_limit`) covers
  N1 — exactly at the limit. Group N is intentionally not duplicated;
  the boundary is bracketed within Group A and the cumulative count
  table reflects that.
- **Unplanned regression tests added during the stabilise pass:**
  - `test_redact_should_quote_short_strings_unredacted_and_truncate_long_ones`
    (boundary-test for `_redact()`)
  - `test_stage_construction_should_emit_an_audit_log_naming_the_bridge_class`
    (the `stage_initialised` log line ADR-0027 §Security Considerations promises)
  - `test_stage_construction_should_accept_a_mapped_bridge_advertising_transports_as_a_list`
    (the `frozenset` coercion fix in `_validate_bridge_transport_set`)
  - `test_force_disconnect_should_*` ×3 (the `Harness.force_disconnect` contract
    that closed the rollback encapsulation break)
- **Test plan IDs rename in code:** scenarios in the plan are referenced by ID
  (A1, B5a, etc.) in test docstrings; the test function names follow the
  `should_*` convention and may rephrase the plan's wording.

**Cumulative test count by group (cumulative):**

| Group | Scenarios in plan | Tests shipped | Cumulative total |
|-------|-------------------|---------------|------------------|
| A | 9 | 14 (A6 ×5 params + 1 unplanned) | 14 |
| B | 6 | 7 (B5 split) | 21 |
| C | 6 | 6 | 27 |
| D | 4 | 5 (D4 split) | 32 |
| E | 5 | 5 | 37 |

---

## Purpose

This document is the test-plan input for the TDD red phase of the `Stage` multi-transport coordinator. It enumerates the **negative-behaviour scenarios** that integration tests must cover: every error path, every partial-failure mode, every edge case named in the ADR's §Validation §Success Metrics, plus the failure modes surfaced by the comprehensive code review.

**This is not a positive-behaviour plan.** The happy-path round-trip is covered by one canonical scenario at the end (the `≤15 lines` ADR success metric); everything else here is something that should fail in a specific, observable, named way. The TDD discipline is: write each scenario as a failing test first, then make it pass.

## Test layer

- **Location:** `packages/core/tests/integration/test_stage_*.py` (new directory under `tests/`).
- **Layer:** integration. Real `Stage` + real `Harness` + real bridge + `MockTransport`. No unit-isolated tests of individual classes here — those belong in `tests/unit/test_stage_*.py` and are out of scope for this plan.
- **Real brokers:** explicitly out of scope. E2E tests against `NatsTransport` + `KafkaTransport` are gated by `pytest -m e2e` and live in `packages/core/tests/e2e/test_stage_e2e.py`. This plan covers the in-memory `MockTransport` integration suite only.
- **Async runner:** `pytest-asyncio`, session-scoped event loop per existing `conftest.py`.

## Naming and assertion conventions

Per `CLAUDE.md` §Test style, every test is named `test_<subject>_should_<observable_behaviour>` or `test_<subject>_should_not_<observable_behaviour>`. Tests assert on observable effects (raised exceptions, resolved handle outcomes, structured log records, transport `is_connected()` state) — never on private attributes.

Each scenario in this document maps to **one** test function. If a scenario seems to need two assertions, it is two scenarios.

Scenarios are grouped by the failure surface they exercise. Each is annotated with the ADR-0027 success metric it implements (where one exists) and the comprehensive-review item (R1-R25) it closes.

---

## Test fixtures and doubles required

Before any scenario runs, these collaborators need to exist. They are not in the codebase today; building them is part of the TDD scaffolding pass.

### Bridges

```python
class _RecordingBridge:
    """Calls forwarded to wrapped bridge; every call recorded for assertions."""

class _CollidingBridge:
    """Returns the same wire id for every transport. For BridgeAmbiguityError."""

class _SmokeTestEscapeBridge:
    """Returns distinct values for the smoke-test synthetic input but the
    same value for all other inputs. Defeats the startup smoke test;
    triggers per-scope distinctness check."""

class _RaisingBridge:
    """Configurable: raise on fresh / to_wire / from_wire. Used to exercise
    BridgeTranslationError code paths."""

class _TypeBrokenBridge:
    """to_wire returns: None | int | "" | "x" * 2000. Parametrised."""

class _SlowBridge:
    """to_wire calls time.sleep. Used to document (not assert) that the
    Stage does not enforce a wall-clock budget."""

class _StatefulBridge:
    """Caches (logical, transport) -> wire on the instance. Used to document
    the cross-scope state-leak risk; this fixture is a regression
    canary, not an aspirational behaviour."""
```

### Transports

```python
class FailingMockTransport(MockTransport):
    """MockTransport whose connect / disconnect / unsubscribe raise on
    demand. Every method has a `fail_next: int` counter."""

class ResourceLeakingMockTransport(MockTransport):
    """connect() opens a side-effect resource (increments a counter on a
    shared sentinel) BEFORE raising. disconnect() releases the resource.
    Used to assert the rollback path disconnects the failing transport."""

class DroppableMockTransport(MockTransport):
    """Exposes .drop() that simulates a broker drop: subsequent publish
    raises RuntimeError; subscriber callbacks stop receiving messages.
    Used for mid-scenario broker-drop scenarios."""
```

### Helpers

```python
@pytest.fixture
def two_mock_stage(): ...
    # Returns (stage, nats_mock, kafka_mock) wired with MappedBridge that
    # round-trips deterministically. Used by every scenario that doesn't
    # need a custom bridge or transport.

@pytest.fixture
def caplog_structured(caplog): ...
    # Helper to assert structured log events by name/extra without
    # re-implementing log-record matching in every test.
```

---

## Group A — Stage construction

### A1. test_stage_construction_should_reject_an_empty_harness_mapping

**Setup:** none.
**Action:** `Stage(harnesses={}, bridge=IdentityBridge())`.
**Expected:** raises `ValueError` with a message naming the invariant ("at least one harness").
**Covers:** ADR §Implementation `Stage.__init__` early guard.

### A2. test_stage_construction_should_reject_a_bridge_that_collides_on_the_smoke_test

**Setup:** `_CollidingBridge` (returns `"same"` for every transport).
**Action:** `Stage(harnesses={"nats": h1, "kafka": h2}, bridge=collider)`.
**Expected:** raises `BridgeAmbiguityError` whose `.transports` is `("nats", "kafka")` (the colliding pair) and whose message redacts the wire id.
**Covers:** R6 (smoke-test claim weakened, but the smoke test still has to fire); ADR §Validation "BridgeAmbiguityError at startup smoke test".

### A3. test_stage_construction_should_reject_a_mapped_bridge_missing_a_registered_transport

**Setup:** `MappedBridge(forwards={"nats": ...})`; harnesses register `{"nats", "kafka"}`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTransportMismatchError` (NOT `BridgeTranslationError`); `.bridge_transports == ("nats",)`; `.registered_transports == ("kafka", "nats")`.
**Covers:** R7-adjacent (typed error vs generic translation failure); ADR §Validation "BridgeTransportMismatchError for MappedBridge".

### A4. test_stage_construction_should_reject_a_mapped_bridge_with_an_extra_transport

**Setup:** `MappedBridge(forwards={"nats": ..., "kafka": ..., "extra": ...})`; harnesses register `{"nats", "kafka"}`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTransportMismatchError`; both sets present on the typed attributes.
**Covers:** symmetry with A3.

### A5. test_stage_construction_should_wrap_a_bridge_to_wire_exception_during_smoke_test

**Setup:** `_RaisingBridge(raise_on="to_wire")`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTranslationError(method="to_wire", transport=<first>)`; `.original` is the underlying exception; `__cause__` is also set.
**Covers:** R19 (`.original` attribute populated); ADR §Validation "BridgeTranslationError carries .original".

### A6. test_stage_construction_should_reject_a_bridge_to_wire_returning_a_non_string

**Setup:** parametrised `_TypeBrokenBridge` over `[None, 42, b"bytes", 3.14, []]`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTranslationError`; `.original` is a `TypeError` whose message names the actual return type.
**Covers:** R9 (return-type validation); ADR §Validation "to_wire return-type validation".

### A7. test_stage_construction_should_reject_a_bridge_to_wire_returning_an_empty_string

**Setup:** `_TypeBrokenBridge(returns="")`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTranslationError`; `.original` is a `TypeError`.
**Covers:** R9.

### A8. test_stage_construction_should_reject_a_bridge_to_wire_returning_an_oversized_string

**Setup:** `_TypeBrokenBridge(returns="x" * (_MAX_WIRE_ID_LEN + 1))`.
**Action:** construct `Stage`.
**Expected:** raises `BridgeTranslationError`; `.original` is a `ValueError` whose message names both the actual length and the limit.
**Covers:** R16 (bounds enforcement).

### A9. test_stage_construction_should_be_a_no_op_for_a_bridge_at_exactly_the_length_limit

**Setup:** `_TypeBrokenBridge(returns="x" * _MAX_WIRE_ID_LEN)`.
**Action:** construct `Stage`.
**Expected:** does not raise. Boundary condition: the limit is inclusive.
**Covers:** R16 boundary.

---

## Group B — Stage lifecycle

### B1. test_stage_connect_should_raise_state_error_when_called_twice

**Setup:** Stage constructed, `connect()` already awaited.
**Action:** await `stage.connect()` again.
**Expected:** raises `StageStateError` whose message names the current state (`"connected"`).
**Covers:** R7 (explicit state machine); ADR §Validation "State machine rejects re-use".

### B2. test_stage_connect_should_raise_state_error_when_called_after_disconnect

**Setup:** Stage constructed, `connect()` then `disconnect()` both awaited.
**Action:** await `stage.connect()` again.
**Expected:** raises `StageStateError`.
**Covers:** R7.

### B3. test_stage_scenario_should_raise_state_error_when_called_before_connect

**Setup:** Stage constructed, never connected.
**Action:** `stage.scenario("x")`.
**Expected:** raises `StageStateError` whose message names the current state (`"new"`).
**Covers:** R7; ADR §Validation "stage.scenario() rejects pre-connect".

### B4. test_stage_scenario_should_raise_state_error_when_called_after_disconnect

**Setup:** Stage constructed, connected, disconnected.
**Action:** `stage.scenario("x")`.
**Expected:** raises `StageStateError`.
**Covers:** R7.

### B5. test_stage_disconnect_should_be_idempotent

**Setup:** Stage constructed, connected, disconnected once.
**Action:** await `stage.disconnect()` again.
**Expected:** returns without raising; harness `disconnect()` methods are NOT called a second time.
**Covers:** R7; ADR §Validation "disconnect() is idempotent".

### B6. test_stage_disconnect_should_be_idempotent_when_never_connected

**Setup:** Stage constructed, never connected.
**Action:** await `stage.disconnect()`.
**Expected:** returns without raising; harness `disconnect()` methods are not called.
**Covers:** R7.

---

## Group C — Connect rollback

### C1. test_stage_connect_should_disconnect_already_connected_siblings_when_a_later_transport_fails

**Setup:** three harnesses; the third's `connect()` raises.
**Action:** await `stage.connect()`.
**Expected:** raises `StageConnectError(failing_transport="<third>")`. After the raise, harnesses 1 and 2 report `is_connected() is False`.
**Covers:** ADR §Validation "Connect rollback cleanly leaves no transport up".

### C2. test_stage_connect_should_disconnect_the_failing_transport_itself

**Setup:** `ResourceLeakingMockTransport` whose `connect()` increments a sentinel counter, then raises. Two harnesses; this one is the second.
**Action:** await `stage.connect()`.
**Expected:** raises `StageConnectError`. The sentinel counter is back at zero (the failing transport's `disconnect()` ran via the rollback path).
**Covers:** R4 (rollback gap closed); ADR §Validation "Connect rollback disconnects the failing transport too".

### C3. test_stage_connect_should_swallow_a_failing_rollback_disconnect_and_log_warning

**Setup:** two harnesses; the second's `connect()` raises; the first's `disconnect()` also raises.
**Action:** await `stage.connect()`.
**Expected:** raises `StageConnectError` (the original connect failure, not the rollback failure). A structured log record `stage_rollback_sibling_disconnect_failed` is emitted with `extra={"transport": "<first>"}` and the rollback exception in the traceback.
**Covers:** R15-adjacent (rollback swallowing documented); ADR §Monitoring structured-log assertions.

### C4. test_stage_connect_should_log_warning_when_failing_transport_disconnect_itself_raises

**Setup:** failing transport's `connect()` raises; same transport's `disconnect()` also raises.
**Action:** await `stage.connect()`.
**Expected:** raises `StageConnectError` (the original connect failure). A structured log record `stage_rollback_failing_transport_disconnect_failed` is emitted with the failing transport's name.
**Covers:** R4 + R15.

### C5. test_stage_connect_should_leave_state_as_new_after_rollback

**Setup:** any failing-connect rollback scenario.
**Action:** observe `stage._state` after the raise (via behaviour: subsequent `stage.scenario("x")` raises `StageStateError` with state `"new"`, NOT `"connected"`).
**Expected:** state is `NEW`. (Asserted via the `StageStateError` message in a follow-up call, since `_state` is private.)
**Covers:** R7; the state machine's "rollback returns to NEW" property.

### C6. test_stage_connect_should_attempt_no_further_transports_after_first_failure

**Setup:** three harnesses; the second's `connect()` raises; the third has a `RecordingMockTransport` that counts `connect()` calls.
**Action:** await `stage.connect()`.
**Expected:** raises `StageConnectError`. The third harness's `connect()` was never called.
**Covers:** ADR §Implementation "fail-fast" guarantee.

---

## Group D — Disconnect aggregation

### D1. test_stage_disconnect_should_raise_exception_group_when_one_transport_fails

**Setup:** Stage connected; one harness's `disconnect()` raises.
**Action:** await `stage.disconnect()`.
**Expected:** raises `StageDisconnectError`; `isinstance(err, ExceptionGroup) is True`; `err.exceptions` is a 1-tuple containing the original exception.
**Covers:** R11 (ExceptionGroup); ADR §Validation "StageDisconnectError is an ExceptionGroup".

### D2. test_stage_disconnect_should_raise_exception_group_when_multiple_transports_fail

**Setup:** Stage connected; two harnesses both raise on `disconnect()`.
**Action:** await `stage.disconnect()`.
**Expected:** raises `StageDisconnectError`; `err.exceptions` lists both originals; `try/except* StageDisconnectError as eg:` walks both.
**Covers:** R11.

### D3. test_stage_disconnect_should_attempt_every_transport_even_when_one_fails

**Setup:** three harnesses; the middle one's `disconnect()` raises; the other two record their `disconnect()` calls.
**Action:** await `stage.disconnect()`.
**Expected:** all three `disconnect()` methods were called, in reverse registration order.
**Covers:** R11; ADR §Implementation "disconnect best-effort across all transports".

### D4. test_stage_disconnect_should_transition_to_disconnected_state_even_after_failure

**Setup:** Stage connected; all transports raise on `disconnect()`.
**Action:** await `stage.disconnect()` (catching the raise); then call `stage.disconnect()` again.
**Expected:** the second call is a no-op (idempotent); a third call to `connect()` raises `StageStateError` (state is DISCONNECTED, not stuck in some intermediate state).
**Covers:** R7; ADR §Implementation lifecycle invariant.

---

## Group E — Bridge protocol enforcement at scope entry

### E1. test_stage_scenario_should_raise_translation_error_when_fresh_raises

**Setup:** Stage with `_RaisingBridge(raise_on="fresh")`.
**Action:** `async with stage.scenario("x") as s:` (i.e. `__aenter__`).
**Expected:** raises `BridgeTranslationError(method="fresh", transport=None)`; `.original` is the underlying exception.
**Covers:** R19; ADR §Validation "BridgeTranslationError wraps consumer-bridge exceptions".

### E2. test_stage_scenario_should_raise_translation_error_when_to_wire_raises_during_eager_mint

**Setup:** Stage with bridge that raises on `to_wire` only for the second registered transport.
**Action:** `async with stage.scenario("x") as s:`.
**Expected:** raises `BridgeTranslationError(method="to_wire", transport="<second>")`; the first transport's child context was minted but is then torn down before the raise propagates.
**Covers:** R8 (eager mint); R19.

### E3. test_stage_scenario_should_raise_ambiguity_error_when_real_logical_id_collides

**Setup:** `_SmokeTestEscapeBridge` (passes startup smoke test, collides on real `fresh()` value).
**Action:** `async with stage.scenario("x") as s:`.
**Expected:** raises `BridgeAmbiguityError`; `.transports` names both colliding transports.
**Covers:** R6 (per-scope re-validation); ADR §Validation "BridgeAmbiguityError at scope entry (smoke-test escape)".

### E4. test_stage_scenario_should_clean_up_partially_minted_children_when_mint_fails

**Setup:** three transports; bridge that raises on `to_wire` only for the third.
**Action:** `async with stage.scenario("x") as s:` (raises during mint).
**Expected:** raises `BridgeTranslationError`. Subsequently opening a fresh `stage.scenario("y")` succeeds; no callback registrations or subscriber leaks from the failed mint observable in any transport's `active_subscription_count()`.
**Covers:** R8 (eager mint cleanup path); ADR §Implementation `_StageScenarioScope._teardown` on `__aenter__` failure.

### E5. test_stage_scenario_should_not_be_re_entrant

**Setup:** Stage scope opened.
**Action:** call `__aenter__` twice on the same `StageScenarioScope` instance.
**Expected:** raises `StageStateError` on the second entry.
**Covers:** ADR §Validation "StageScenarioScope not re-entrant".

---

## Group F — DSL error semantics inside a scope

### F1. test_stage_expect_should_raise_missing_transport_error_when_on_is_omitted

**Setup:** open a Stage scope.
**Action:** `s.expect("topic", matcher)` (no `on=`).
**Expected:** raises `MissingTransportError` (NOT `TypeError`); message names the framework concept.
**Covers:** R3 (dead-letter type fixed); ADR §Validation "MissingTransportError raised on on=None".

### F2. test_stage_publish_should_raise_missing_transport_error_when_on_is_omitted

**Setup:** open a Stage scope.
**Action:** `s.publish("topic", payload)`.
**Expected:** raises `MissingTransportError`.
**Covers:** R3.

### F3. test_stage_on_should_raise_missing_transport_error_when_on_is_omitted

**Setup:** open a Stage scope.
**Action:** `s.on("topic")`.
**Expected:** raises `MissingTransportError`.
**Covers:** R3.

### F4. test_stage_expect_should_raise_unknown_transport_error_when_on_is_a_typo

**Setup:** open a Stage scope; transports `{"nats", "kafka"}`.
**Action:** `s.expect("topic", matcher, on="ntas")`.
**Expected:** raises `UnknownTransportError` whose message lists `["nats", "kafka"]` (sorted, deterministic order).
**Covers:** ADR §Validation "UnknownTransportError raised on on='typo'".

### F5. test_stage_publish_should_raise_unknown_transport_error_when_on_is_a_typo

**Setup:** as F4.
**Action:** `s.publish("topic", payload, on="kfka")`.
**Expected:** raises `UnknownTransportError`.
**Covers:** symmetry with F4.

### F6. test_stage_reply_chain_publish_should_raise_unknown_transport_error_when_response_on_is_a_typo

**Setup:** open a Stage scope.
**Action:** `s.on("trigger", on="kafka").publish("response", on="natz", build=lambda m: m)`.
**Expected:** raises `UnknownTransportError` for the response transport name.
**Covers:** cross-transport reply DSL, naming the response side.

### F7. test_stage_expect_should_not_set_handle_transport_via_post_hoc_mutation

**Setup:** open a Stage scope.
**Action:** `handle = s.expect("topic", matcher, on="kafka")`.
**Expected:** `handle.transport == "kafka"` immediately after return; verified by reading the `_register_expectation` source via a regression-style code-pattern test (or by asserting that the dispatcher cannot resolve the handle with `transport=None` — race construction).
**Covers:** R2 (race fix). Implementation note: this test is hard to assert directly; the canonical version is a synthetic where the dispatcher resolves the handle before the supposed assignment line, and we assert `handle.transport == "kafka"` from the dispatcher callback.

---

## Group G — Scope teardown error isolation

### G1. test_stage_scope_aexit_should_complete_when_one_unsubscribe_raises

**Setup:** two transports; both have one expectation each. The first transport's `unsubscribe` raises; the second's records its calls.
**Action:** `async with stage.scenario("x") as s: s.expect("a", m, on="t1"); s.expect("b", m, on="t2")` then exit.
**Expected:** the second transport's `unsubscribe` IS called (the first's failure does not abort the loop). A structured log `stage_scope_unsubscribe_failed` is emitted for the first transport.
**Covers:** R1 (isolation, the most-flagged item); ADR §Validation "__aexit__ survives a unsubscribe failure on one transport".

### G2. test_stage_scope_aexit_should_complete_even_when_every_unsubscribe_raises

**Setup:** two transports; both `unsubscribe` methods raise.
**Action:** open scope, register an expectation on each, exit.
**Expected:** `__aexit__` returns without raising. Two structured logs emitted, one per transport.
**Covers:** R1.

### G3. test_stage_scope_aexit_should_clear_subscriber_refs_even_when_unsubscribe_raises

**Setup:** as G1.
**Action:** open scope, register expectations, exit, then immediately reopen a second scope with the same Stage.
**Expected:** the second scope sees no leaked callbacks; `harness.active_subscription_count()` for both transports is zero at the start of the second scope.
**Covers:** R1; documents that the failure mode does not persist into the next scope.

### G4. test_stage_scope_aexit_should_propagate_a_body_exception_unmodified

**Setup:** open a Stage scope; the body of `async with` raises a `RuntimeError("boom")`.
**Action:** observe what propagates.
**Expected:** `RuntimeError("boom")` propagates to the caller; `__aexit__` ran (subscriber refs cleared).
**Covers:** ADR §Implementation `__aexit__` documented contract ("body exceptions propagate unmodified").

### G5. test_stage_scope_aexit_should_propagate_a_body_exception_even_when_teardown_logs_warnings

**Setup:** body raises `RuntimeError("boom")`; one transport's `unsubscribe` also raises during teardown.
**Action:** observe what propagates.
**Expected:** `RuntimeError("boom")` propagates (NOT the teardown exception); the teardown raise is logged as a warning.
**Covers:** ADR §Implementation contract: teardown raises do not mask body raises.

---

## Group H — Mid-scenario broker drop

### H1. test_stage_scope_should_resolve_handles_as_timeout_when_subscribed_transport_drops

**Setup:** open a Stage scope; register `s.expect("topic", matcher, on="nats")`; simulate broker drop on the NATS transport via `DroppableMockTransport.drop()`; `await s.await_all(timeout_ms=50)`.
**Action:** as above.
**Expected:** result is failed; `handle.outcome == TIMEOUT`; `handle.transport == "nats"`; `handle.reason` mentions deadline.
**Covers:** R5 (broker drop documented); ADR §Validation "Inbound after broker drop resolves as TIMEOUT".

### H2. test_stage_publish_should_raise_when_target_transport_dropped

**Setup:** open a Stage scope; drop the Kafka transport; attempt `s.publish("topic", payload, on="kafka")`.
**Action:** as above.
**Expected:** raises `RuntimeError` (or whatever the existing `Harness.publish` raises when not connected — verify the existing exception type and assert exactly that). The exception propagates out of the `async with` body.
**Covers:** R5; ADR §Implementation "publish on dropped transport".

### H3. test_stage_scope_aexit_should_complete_when_unsubscribe_raises_due_to_dropped_transport

**Setup:** open scope; register expectation on Kafka; drop Kafka mid-scenario; exit scope.
**Action:** observe teardown.
**Expected:** `__aexit__` returns; `stage_scope_unsubscribe_failed` log emitted naming Kafka. No exception propagates beyond the `async with` (because no body exception occurred).
**Covers:** R1 + R5 intersection.

### H4. test_stage_scope_should_resolve_only_dropped_transports_handles_as_timeout

**Setup:** two-transport scope; expectations on both; drop one transport; the other receives a matching message.
**Action:** `await s.await_all(timeout_ms=100)`.
**Expected:** the live transport's handle resolves PASS; the dropped transport's handle resolves TIMEOUT. Result.passed is False (TIMEOUT propagates to scenario failure).
**Covers:** R5; per-transport handle outcome differentiation.

---

## Group I — Cross-transport reply lifecycle

### I1. test_stage_cross_transport_reply_should_emit_response_on_other_transport

**Setup:** open scope; register `s.on("orders.new", on="kafka").publish("results", on="nats", build=lambda m: {...})`; publish a triggering message on `orders.new` via Kafka.
**Action:** `await s.await_all(timeout_ms=100)`.
**Expected:** the response is published on NATS with the NATS-side wire id; an expectation on `results` on NATS would resolve.
**Covers:** R10; ADR §Validation "Same-transport reply via Stage matches single-transport reply behaviour" (cross-transport sibling case).

### I2. test_stage_cross_transport_reply_should_fire_only_once_when_trigger_arrives_twice

**Setup:** as I1; publish two trigger messages.
**Action:** `await s.await_all`.
**Expected:** exactly one response published on NATS; the second trigger is recorded in `ReplyReport.candidate_count` but does NOT cause a second emit. `_Reply.state` is `FIRED` (not double-FIRED).
**Covers:** R10 (fire-once preserved cross-transport); ADR §Validation "Cross-transport reply fires once per scope".

### I3. test_stage_cross_transport_reply_should_record_failed_when_response_transport_dropped

**Setup:** open scope; register cross-transport reply (trigger Kafka, response NATS); drop NATS; publish trigger on Kafka.
**Action:** `await s.await_all`.
**Expected:** the trigger callback fires; the response emit raises (NATS not connected); `ReplyReport.state == FIRED_BUILDER_ERROR`; `ReplyReport.response_transport == "nats"`; the wrapped exception is captured.
**Covers:** R10; ADR §Validation "Cross-transport reply with response transport disconnected".

### I4. test_stage_cross_transport_reply_should_record_armed_no_match_when_trigger_transport_dropped

**Setup:** open scope; register cross-transport reply; drop Kafka (trigger transport); publish nothing.
**Action:** `await s.await_all(timeout_ms=50)`.
**Expected:** the trigger callback never fires (broker dropped); `ReplyReport.state == ARMED_NO_MATCH`; no response emitted on NATS.
**Covers:** R10.

### I5. test_stage_cross_transport_reply_should_record_failed_when_builder_raises

**Setup:** open scope; register reply with `build=lambda m: 1/0`; publish trigger.
**Action:** `await s.await_all`.
**Expected:** `ReplyReport.state == FIRED_BUILDER_ERROR`; the `ZeroDivisionError` is captured; no response emitted on the response transport.
**Covers:** ADR §Implementation reply builder failure path.

### I6. test_stage_same_transport_reply_via_stage_should_match_single_transport_reply_behaviour

**Setup:** parametrised pair: one test runs `harness.scenario(...).on("a").publish("b")` directly on a Harness; the other runs `stage.scenario(...).on("a", on="t").publish("b", on="t")` against a Stage with one transport.
**Action:** publish trigger; `await await_all`.
**Expected:** both produce identical observable behaviour (same response published, same fire-once enforcement, same `ReplyReport`).
**Covers:** R10 (no regression on the same-transport path); ADR §Validation "Same-transport reply via Stage matches".

### I7. test_stage_cross_transport_reply_should_use_response_context_correlation_id_for_emit

**Setup:** open scope with `MappedBridge` that gives different wire ids per transport; register cross-transport reply.
**Action:** publish trigger on Kafka, observe what's stamped on the NATS-side response.
**Expected:** the published response carries the NATS-side wire id (per the Kafka-side trigger context's logical → NATS `to_wire`), NOT the Kafka-side wire id. This is the correlation-translation property.
**Covers:** R10; the load-bearing reason the bridge exists at all.

### I8. test_stage_cross_transport_reply_should_not_mutate_reply_state_on_response_context

**Setup:** as I1.
**Action:** after `await_all`, inspect both contexts' `_Reply` records.
**Expected:** the `_Reply` exists ONLY on the trigger context (single-writer per ADR-0016); the response context has no `_Reply` records.
**Covers:** R10 (single-writer ownership invariant — this is the test that proves it).

---

## Group J — Parallel isolation

### J1. test_stage_should_isolate_one_hundred_concurrent_scopes

**Setup:** Stage with two `MockTransport` instances and a deterministic `MappedBridge`. Launch 100 concurrent `async with stage.scenario(name)` blocks. Each publishes on transport A and expects on transport B (cross-transport round-trip via a synthetic AUT-stand-in callback installed on each MockTransport).
**Action:** `await asyncio.gather(*scopes)`.
**Expected:** every scope's handles resolve from messages tagged with that scope's wire ids on both transports. Zero cross-scope matches across all 100. (Asserted by giving each scope a unique payload tag and verifying the resolved `handle.message` carries the scope's own tag.)
**Covers:** ADR §Validation "Parallel isolation at 100 Stage scopes".

### J2. test_stage_should_document_state_leak_when_bridge_fresh_collides

**Setup:** custom bridge whose `fresh()` returns the same value for every call. Two scopes opened concurrently.
**Action:** publish in scope A; expect in scope B (or vice versa).
**Expected:** scope B sees scope A's message (cross-scope leak observed). This test is a **canary**: it documents the failure mode named in §Security Considerations ("If `fresh()` is collision-prone, two scopes can land on the same logical id"). The assertion is that the leak occurs, naming the scenario as "expected failure mode under the documented misuse" via `pytest.xfail` or equivalent.
**Covers:** R6 + Security Considerations; documents the boundary of the framework's correctness guarantee.

---

## Group K — Result and Handle shape

### K1. test_stage_scenario_result_by_transport_should_omit_handles_with_no_transport

**Setup:** mixed-mode test: one Stage scope and one single-Harness scenario coexisting in the same process.
**Action:** inspect both `ScenarioResult.by_transport` views.
**Expected:** the Stage result has keys for every touched transport (e.g. `{"nats", "kafka"}`); the single-Harness result has an empty mapping (`result.by_transport == {}`). `result.by_transport.get("anything") is None` for the single-Harness case.
**Covers:** R24 (the empty-string key removed); ADR §Validation "ScenarioResult.by_transport keys are real transport names only".

### K2. test_stage_handle_transport_should_match_the_on_selector

**Setup:** open scope; register expectations on multiple transports.
**Action:** inspect each handle's `transport` attribute immediately after `expect()` returns (before any await).
**Expected:** every handle's `transport` matches the `on=` value from its registration.
**Covers:** R2 + ADR §Validation "Handle.transport populated at construction".

### K3. test_single_harness_handle_transport_should_be_none

**Setup:** existing single-`Harness` scenario.
**Action:** register an expectation; inspect handle.
**Expected:** `handle.transport is None`.
**Covers:** ADR §Consequences §Neutral "None for handles created by a single-harness scenario".

### K4. test_stage_handle_repr_should_include_transport_name

**Setup:** open Stage scope; register expectation on `"kafka"`.
**Action:** `repr(handle)`.
**Expected:** the repr string contains `"kafka"`. (Verifies the §Security-Considerations claim that `Handle.transport` is observable in repr — and the corresponding consumer warning that transport names must not carry sensitive identifiers.)
**Covers:** R14; ADR §Security Considerations.

---

## Group L — Coexistence

### L1. test_stage_and_harness_should_coexist_in_one_process

**Setup:** within a single test, construct one `Stage` (multi-transport) and one stand-alone `Harness` (single-transport, possibly using one of the same transport instances? — verify: separate transport instances to avoid state-sharing).
**Action:** run a scenario on each, concurrently or sequentially.
**Expected:** neither leaks state into the other. Connection lifecycles are independent.
**Covers:** ADR §Validation "Mixed-mode coexistence"; PRD §Open Questions "Mixed-mode scenarios".

### L2. test_stage_and_harness_should_not_share_a_transport_instance

**Setup:** attempt to construct a Stage that registers a `Harness` whose `transport` is also held by an unrelated standalone `Harness`. **Expected behaviour:** undefined / probably surprising. This test documents the **non-supported** case: the test asserts that observable cross-talk occurs, naming the scenario as a misuse via `pytest.xfail`. Goal: prevent a future engineer from assuming this is supported.
**Covers:** documented constraint that transports are not shared between harnesses (implicit invariant in the existing codebase, made explicit by this test).

---

## Group M — Bridge call observability

### M1. test_stage_should_log_bridge_class_name_at_startup

**Setup:** any Stage construction.
**Action:** capture structured logs at INFO during `__init__`.
**Expected:** one log record names the bridge class (e.g. `MappedBridge`), no instance pointer or state is logged.
**Covers:** ADR §Security Considerations "Recording the bridge class name in the structured startup log".

### M2. test_stage_should_log_warning_when_from_wire_raises_during_diagnostics

**Setup:** Stage with a bridge whose `from_wire` raises. Open a scope; deliver an inbound message that does NOT match the active scope's wire id (so the diagnostic path is taken).
**Action:** observe logs.
**Expected:** a `stage_from_wire_failed` warning is emitted with the bridge class name and the transport. The inbound message is silently treated as unmatched. The scope continues running normally.
**Covers:** R20 (`from_wire` failure path); ADR §Implementation "Errors during diagnostic from_wire() are logged at WARNING".

---

## Group N — Bridge type-validation edge cases

### N1. test_stage_init_should_accept_a_bridge_returning_strings_at_the_length_limit

**Setup:** custom bridge whose `to_wire` returns `"x" * _MAX_WIRE_ID_LEN`.
**Action:** construct Stage.
**Expected:** does not raise (boundary).
**Covers:** R16 boundary (positive sibling of A8).

### N2. test_stage_init_should_reject_a_bridge_returning_one_char_over_the_limit

**Setup:** as A8.
**Expected:** raises `BridgeTranslationError`. Pair this and N1 to bracket the limit.
**Covers:** R16.

---

## Group O — Connect-failure ordering and reentrancy

### O1. test_stage_connect_should_call_harnesses_in_registration_order

**Setup:** three harnesses A, B, C, each with a `RecordingMockTransport`.
**Action:** `await stage.connect()`.
**Expected:** the recorded order of `connect()` calls is `[A, B, C]`.
**Covers:** ADR §Notes deferred contract on ordering (the test makes the contract real).

### O2. test_stage_disconnect_should_call_harnesses_in_reverse_registration_order

**Setup:** as O1.
**Action:** `await stage.connect()` then `await stage.disconnect()`.
**Expected:** the recorded order of `disconnect()` calls is `[C, B, A]`.
**Covers:** ADR §Notes deferred contract.

### O3. test_stage_connect_rollback_should_disconnect_in_reverse_of_connect

**Setup:** three harnesses A, B, C; C raises on connect; A and B record disconnect calls.
**Action:** `await stage.connect()` (raises).
**Expected:** the rollback `disconnect()` calls happen in order `[C, B, A]` (C first because it's the failing transport, then siblings in reverse).
**Covers:** R4; consistency of rollback ordering with the documented contract.

---

## Group P — Canonical happy path (one positive scenario for sanity)

### P1. test_stage_canonical_round_trip_should_complete_in_under_fifteen_lines

**Setup:** two-transport Stage; `MappedBridge`; AUT-stand-in callback installed on the mock transports that translates inbound on transport A into outbound on transport B and vice versa.
**Action:** `s.publish` on A → AUT bridges to B → `s.on(B).publish(B, build=...)` reply terminator → assertion `s.expect` on A → `await await_all`.
**Expected:** result.passed is True. Counted lines of the scenario body (the contents of the `async with` block, excluding setup): ≤ 15.
**Covers:** ADR §Validation "Canonical bridge round-trip ≤ 15 lines".

This is the **one** positive test in the integration suite. It exists to anchor the negative scenarios — every other test in the suite is "what should fail and how". Unit tests of individual components (Bridge implementations, Stage state-machine logic, error type constructors) live separately and may have richer positive coverage.

---

## Out of scope for this plan

- **Unit tests of individual classes** (`IdentityBridge.fresh()` returning a hex string of expected length, `MappedBridge.configured_transports` as a frozenset, `BridgeTranslationError.__init__` constructing correct attributes, etc.). These belong in `tests/unit/test_stage_*.py`.
- **End-to-end tests against real brokers.** Gated by `pytest -m e2e` and live in `tests/e2e/test_stage_e2e.py`.
- **Performance / load testing.** The 100-scope parallel-isolation test (J1) is the upper bound for this layer; broader load testing belongs in a separate plan.
- **Property-based testing of bridge translation correctness.** A separate hypothesis-based plan can cover the "for any logical id, bridge.from_wire(bridge.to_wire(l)) == l" round-trip property for `MappedBridge` with inverse functions provided. Out of scope here.
- **Documentation tests of code samples in the ADR.** The ADR's code blocks are sketches, not runnable. Maintaining them as runnable would invert the artefact's purpose.

---

## Red-phase quality

Every group's tests must red on **the assertion** they exercise, not on
"method does not exist" or "module does not exist". A uniform red signal
across every test in a group (e.g. all hitting the same `AttributeError`)
is acceptable for **skeleton-introducing groups** — Group A introduced
`Stage`, Group E introduced `_StageScenarioScope`. The placeholder pattern
is: ship the symbol, raise `NotImplementedError` from the unimplemented
code path, then drive each test to red on its own assertion.

For groups that add methods to an existing class (Groups F-I), the
discipline is:

0. Public error types referenced by the new methods are introduced in
   the same scaffold pass as the methods themselves, before any test
   that asserts on them. Group F surfaced this rule the hard way:
   `MissingTransportError` and `UnknownTransportError` were referenced
   in the guard but defined only when the green pass tried to import
   them. The error types are part of the public surface and belong
   alongside the rest of the error hierarchy, not co-located with their
   call sites.
1. Before the red phase, scaffold the new method signatures with bodies
   that raise `NotImplementedError("Group X lands this")`.
2. Write each test; verify red.
3. The red signal should be: "expected MissingTransportError, got
   NotImplementedError" (assertion failure on error type), NOT
   "AttributeError: object has no attribute 'expect'" (no method
   defined). The first signal proves the test reaches its assertion;
   the second only proves the test loaded.
4. Implement the green path; each test goes green individually.

This matters because non-trivial behaviour (e.g. `MissingTransportError`
when `on=` is omitted vs `UnknownTransportError` when `on="typo"`) needs
each test to reach its specific assertion line so the green
implementation is genuinely driven by that test, not by an aggregate
"make all the red go green" pass.

## TDD walking order

Suggested order to implement these tests (red phase) and the corresponding production code (green phase):

1. **Group A (construction)** — establishes the Stage class, validation passes, basic error types. Smallest blast radius.
2. **Group B (lifecycle state machine)** — needs `connect()` / `disconnect()` to exist; introduces `_StageState`.
3. **Groups C + D (rollback + disconnect aggregation)** — exercises the harness-coordination paths; introduces `_rollback`, `StageDisconnectError(ExceptionGroup)`.
4. **Groups E + F (scope entry + DSL errors)** — needs `StageScenarioScope`, `StageScenario`. The `_register_expectation` extraction from `scenario.py` lands here.
5. **Group G (teardown isolation)** — small set; closes the most-flagged review item once the scope exists.
6. **Group H (broker drop)** — needs `DroppableMockTransport`; exercises the timeout path. Builds confidence the scope handles a transport disappearing.
7. **Group I (cross-transport reply)** — the largest set; needs `_register_reply` two-context refactor and `StageReplyChain`. Land last in the core implementation because it depends on every prior piece.
8. **Groups J, K, L, M, N, O** — coverage and observability tests. Land alongside or after the corresponding feature.
9. **Group P** — the happy-path canary lands last as a confidence test that the negative-only suite hasn't accidentally produced a Stage that can never succeed.

Each group is small enough to land as one PR.

---

## References
- `CLAUDE.md` §Test style — naming and assertion conventions

**Last Updated:** 2026-05-04
