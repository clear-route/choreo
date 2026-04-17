# Latency Observability — Per-Expectation SLAs and Failure Timelines — Product Requirements Document

**Status:** Draft
**Created:** 2026-04-17
**Last Updated:** 2026-04-17
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering, Performance SMEs

---

## Executive Summary

The Scenario DSL (PRD-002) already captures per-handle latency from expectation registration to first match. This PRD promotes that recorded number into a first-class test assertion: tests can declare an SLA per expectation, breaching it fails the scenario, and failing scenarios surface a chronological timeline of every named event the scope saw. Report output (HTML, dashboards, aggregate percentiles across a run) is explicitly out of scope and deferred to a later PRD.

---

## Problem Statement

### Current State

`Handle._latency_ms` is populated on every match ([packages/core/src/choreo/scenario.py:335](../../packages/core/src/choreo/scenario.py#L335)) and is surfaced in `ScenarioResult.failure_summary()` and `summary()`. It is purely informational. A test that matches content correctly cannot fail on timing: a handle whose matcher passes has `Outcome.PASS` regardless of how long the match took.

For diagnosis, `ScenarioResult` distinguishes silent timeouts (`Outcome.TIMEOUT` — no correlated message arrived) from near-miss timeouts (`Outcome.FAIL` — messages arrived but the matcher rejected them), tracking `_attempts` and `_last_rejection_reason`. It does not record *when* rejected messages arrived, so a flake that reports `3 messages rejected` gives no signal on whether those arrived 2ms after the publish or 450ms after.

### User Pain Points

- **Latency regressions silently pass CI.** A scenario that used to match in 5ms and now matches in 500ms is a correctness-of-system regression, but the test is green. Authors who care about timing today write ad-hoc `assert handle.latency_ms < 50`, which is boilerplate and inconsistent across tests.
- **Near-miss timeouts give no temporal signal.** When a scenario times out with `3 messages rejected`, the author cannot tell whether the service is slow (rejections arrived late) or wrong (rejections arrived promptly but with wrong fields).
- **No way to see the order events actually arrived in.** Three expectations against the same topic, matched in a non-deterministic order, cannot be debugged without re-running with print statements.

### Business Impact

- Latency-sensitive behaviour (state updates, adapter ACKs, downstream projections) is tested for correctness but not speed. Services that pass functional tests can still breach SLAs that only get caught in production.
- Debugging a timeout flake today means re-running the test locally with extra instrumentation. Timelines captured at failure time would cut diagnosis time materially.

---

## Goals and Objectives

### Primary Goals

1. **Declarative per-expectation SLA.** A test author writes `s.expect(topic, matcher).within_ms(50)`; if the match arrives after 50ms the scenario fails.
2. **A new terminal outcome `SLOW`** so `failure_summary()` tells the author "matched, but 72ms > 50ms budget" instead of conflating it with matcher rejections.
3. **Failure timeline on the scope.** On any non-passing scenario, `ScenarioResult` exposes the ordered list of every named-topic message the scope observed (matched or rejected), with offsets from the scope's t₀.
4. **Zero cost when unused.** Tests that do not call `.within_ms()` pay no extra runtime or memory cost beyond today's behaviour.

### Success Metrics

- A scenario that matches correctly but slowly (above its declared budget) reports `Outcome.SLOW` and fails `assert_passed()`.
- On failure, the timeline prints one line per named message seen, with monotonic offset in ms and topic, in arrival order.
- No change to handles in tests that do not call `.within_ms()` — same latency number, same outcome set they already had.
- Test authors can replace an existing ad-hoc `assert handle.latency_ms < 50` with `.within_ms(50)` in a single PR without functional change.

### Non-Goals

- **Aggregate reporting across a suite.** Percentiles across all scenarios in a run, HTML reports, dashboards, pytest-html integration, CI gating on aggregate metrics. Separate PRD (`PRD-007 — test report output`, to be written).
- **Run-over-run history.** Trend detection, regression alerting, archived comparisons. Deferred.
- **Transport wire-level latency.** Measuring time between a message leaving an external mock and the service's response on the transport. The anchor in this PRD is the `expect()` registration inside the scenario scope; transport wire latency is a distinct problem. If it turns out the transport adds meaningful unexplained latency, a follow-up PRD can layer on top.
- **Anchor at `publish()` instead of at `expect()`.** The existing anchor (expect registration) is kept. Multi-publish scenarios measure reaction time from "we are listening" to "the match arrived", not from each publish. This trades ambiguity for simplicity and matches current behaviour.
- **Wall-clock accuracy.** Latency uses `loop.time()` (monotonic). A wall-clock ISO8601 timestamp accompanies each timeline entry for correlation with external logs, but is not used for budget arithmetic.

---

## User Stories

### Primary User Stories

**As a** test author,
**I want to** assert that a `state.changed` arrives within 50ms of my `expect()`,
**So that** a latency regression in the downstream service breaks the build instead of being absorbed as flakiness.

**Acceptance Criteria:**
- [ ] `s.expect("state.changed", matcher).within_ms(50)` compiles and returns the same `Handle`.
- [ ] A match at 30ms produces `Outcome.PASS` and `handle.latency_ms == ~30.0`.
- [ ] A match at 80ms produces `Outcome.SLOW`, `handle.latency_ms == ~80.0`, and `ScenarioResult.passed == False`.
- [ ] `ScenarioResult.assert_passed()` raises with a line for the SLOW handle saying "matched in 80.2ms, budget 50ms".
- [ ] A handle with no `.within_ms()` call behaves identically to today: the latency is recorded and surfaced in `summary()` but never causes failure.

---

**As a** test author debugging a timeout,
**I want to** see the exact order and offsets of every message my scope observed,
**So that** I can tell whether the service was slow or whether it sent the wrong content.

**Acceptance Criteria:**
- [ ] On any non-passing `ScenarioResult`, `result.timeline` returns a tuple of `TimelineEntry` in arrival order.
- [ ] Each entry reports: `offset_ms` (monotonic, from scope t₀), `wall_clock` (ISO8601), `topic`, `action` (`"matched"` / `"rejected"` / `"correlation_skipped"` if we decide to include them — see Open Questions), and a short payload descriptor (not the full payload; see Non-Functional Requirements for bounding).
- [ ] `failure_summary()` appends a compact timeline block below the per-handle breakdown, one line per entry, truncated to the last N entries (see Open Questions for N).
- [ ] A passing scenario does not populate `result.timeline` — zero-cost when no failure.

---

**As a** test author,
**I want to** know whether messages that failed my matcher arrived promptly or late,
**So that** I can distinguish "wrong content, right speed" (matcher bug or service behaviour change) from "right shape, arrived slowly" (service slowness).

**Acceptance Criteria:**
- [ ] A near-miss rejection in the timeline shows its offset; a glance at `failure_summary()` tells the author when the rejection happened, not just that N happened.

---

## Functional Requirements

### 1. SLA declaration on Handle

- `Handle.within_ms(budget_ms: float) -> Handle` — fluent, returns self for chaining. Sets `handle._budget_ms`. Calling twice replaces the earlier budget with a warning (no-op silently *not* acceptable — it masks typos).
- Callable in the scenario builder after `expect()` returns, before `publish()`. Calling it after `await_all()` is a `RuntimeError` (state is frozen).
- `budget_ms` must be a positive finite number; otherwise `ValueError`.

### 2. New outcome: `SLOW`

- `Outcome.SLOW = "slow"` added to the enum.
- Produced by `_register_expectation`'s matcher callback when the matcher returns a match *and* `_budget_ms` is set *and* the measured `_latency_ms > _budget_ms`. The handle's `_message` is still populated (the match did occur); the outcome reflects the budget breach.
- `Handle.was_fulfilled()` continues to return `True` only for `Outcome.PASS`. `SLOW` is not a fulfilment.
- `ScenarioResult.passed` is `False` if any handle is `SLOW`.

### 3. Timeline capture

- A new `_Timeline` lives on `_ScenarioContext`. It records every named-topic message the scope observes on any subscriber registered by the scope's `expect()` calls, regardless of whether that message matched, was rejected, or was filtered by correlation ID.
- Each entry: `offset_ms` (from the scope's first `expect()` registration — same anchor as latency), `wall_clock`, `topic`, `action`, `detail` (a short description; see NFRs).
- `ScenarioResult.timeline: tuple[TimelineEntry, ...]` is populated only when `passed is False`. When `passed is True`, `timeline` is `()`.
- `failure_summary()` appends a `Timeline:` block listing the last N entries (see Open Questions).

### 4. Diagnosis text for SLOW

- `_diagnose(handle)` handles the new outcome: `"matched in 80.2ms, budget 50ms (exceeded by 30.2ms)"`.
- The matched payload's `_reason` from the matcher is still preserved in `handle._reason`; the diagnosis line is built from `_budget_ms` + `_latency_ms`.

### 5. Instrumentation points (no new clocks)

- Latency uses `loop.time()` consistent with PRD-002. No new clock source. `loop.time()` on the default asyncio loop is monotonic.
- Timeline entries additionally record `datetime.datetime.now(timezone.utc).isoformat()` at capture time, for external log correlation only. The offset and budget arithmetic never look at wall clock.

### 6. API surface summary

```python
handle = (
    s.expect("state.changed", contains_fields({"count": 1000}))
     .within_ms(50)
)
s = s.publish("events.created", event_fixture)
result = await s.await_all(timeout_ms=500)
result.assert_passed()   # raises on SLOW, FAIL, or TIMEOUT

# After await_all:
handle.latency_ms              # 72.4 (float, ms)
handle.outcome                 # Outcome.SLOW
result.passed                  # False
result.timeline                # populated because passed is False
for e in result.timeline:
    print(f"{e.offset_ms:6.1f}ms  {e.topic}  {e.action}")
```

---

## Non-Functional Requirements

### Performance

- `within_ms()` registration is O(1): a single field assignment on the handle.
- Timeline capture is O(1) per message and bounded per scope: a ring buffer of size `TIMELINE_MAX_ENTRIES` (see Open Questions for default) prevents a runaway test from eating memory. Overflow drops the oldest entries with a `dropped_count` surfaced on `ScenarioResult`.
- Zero allocations on the hot path for scopes that never produce a failure — the timeline buffer is reused; entries are only exposed when `passed is False`.

### Memory bound on timeline entries

- The `detail` field is a short string, capped at ~120 characters. It is derived by the codec-aware payload descriptor, not the raw payload. No full payload is retained on the timeline — callers who need payload inspection use `handle.message`.
- This PRD does *not* let a misbehaving test pin arbitrary scope-internal payloads in memory via the timeline.

### Accuracy

- `loop.time()` is monotonic; deltas are stable to well within a millisecond on Linux in CI. This is the same clock used by the existing deadline logic, so latency and deadlines agree by construction.
- Wall-clock timestamps are best-effort correlation only and are not guaranteed to align with any external clock source beyond system NTP configuration.

### Backwards compatibility

- Adding `Outcome.SLOW` is additive. Existing tests that only check `Outcome.PASS` / `TIMEOUT` / `FAIL` keep compiling. A test using `match handle.outcome: case Outcome.PASS: ... case Outcome.TIMEOUT: ...` without a default clause would silently miss `SLOW`, so:
  - `ScenarioResult.assert_passed()` is the recommended gate. It is already in use.
  - The migration note in the PRD announcement should flag exhaustive match statements as a follow-up.

---

## Decisions Already Made

| # | Decision | Why |
|---|----------|-----|
| 1 | Anchor = expect-registration, unchanged | Keeps existing code path; multi-publish ambiguity not worth the API complexity here |
| 2 | Budget API = fluent chain (`.within_ms()`) on the Handle | Clean separation: `expect` defines content, `within_ms` defines timing. Type-state unaffected |
| 3 | Hard failure on breach via new `Outcome.SLOW` | User explicitly wanted latency breaches to fail the test. A new outcome preserves diagnostic clarity vs overloading `FAIL` |
| 4 | Timeline populated only on failure | Zero-cost happy path; the data is only valuable when the test is already going to fail |
| 5 | Only named topics in the timeline | Matches the interview: ones named in `expect()` / `publish()`. Framework chatter stays out |
| 6 | Transport-agnostic | The instrumentation is at the scenario layer. Every transport flows through the same expect/match code path |
| 7 | Output surface = scenario-level only in this PRD | Suite / run / CI reporting is a distinct concern; a separate PRD will handle it |

---

## Open Questions

1. **Timeline max size.** Reasonable defaults: 256 or 1024 entries per scope. Leaning 256 — a scope that sees more than that has bigger problems than this PRD solves.
2. **Timeline block length in `failure_summary()`.** Print last 20, 50, all? Leaning 20, with a "(N earlier entries elided)" marker when truncated.
3. **Does the timeline include `correlation_skipped` messages** (messages on a subscribed topic whose `correlation_id` belongs to another scope)? Useful for diagnosing correlation-ID plumbing bugs, potentially noisy otherwise. Recommend: yes, but behind a feature flag on the scope (off by default).
4. **`within_ms(0)` or negative values.** Current plan: `ValueError` for zero and negative. Is there a legitimate "must have already arrived" use case? Probably not, but worth flagging.
5. **Multiple budgets on one handle.** `within_ms(50)` then `within_ms(100)`. Plan: last-write-wins with a `UserWarning`. Alternative: `RuntimeError`. Leaning warning; a test author iterating on tolerances shouldn't be punished.

---

## Out of Scope (explicit)

- Per-publish latency ("how long from `publish()` to the service's first reaction") — same measurement, different anchor, likely a second API later.
- Transport wire-level latency.
- Suite-level p50/p99.
- HTML / JSON / Grafana output.
- Flake-detection heuristics across runs.

---

## Related PRDs / ADRs

- [PRD-001 — Framework foundations](PRD-001-framework-foundations.md) — Harness, Dispatcher; the transport layer this sits on top of.
- [PRD-002 — Scenario DSL](PRD-002-scenario-dsl.md) — defines `Handle`, `expect`, `await_all`, `ScenarioResult`. This PRD extends them.
- `PRD-007 — Test report output` (to be written) — consumes the per-scenario data this PRD produces.
- [ADR-0014 — Handle pattern](../adr/0014-handle-pattern.md) — Handle is the per-expectation return object; `.within_ms()` is an additive method on it.

---

## Appendix A — Example failure output

A scenario where `state.changed` matched correctly but late, and `validation.approved` never arrived.

```
scenario 'process_event' failed — 2 of 2 expectations did not pass
correlation: corr-91f3b2

  [SLOW] state.changed
      matcher : fields contain {'count': 1000}
      why     : matched in 72.4ms, budget 50ms (exceeded by 22.4ms)
      latency : 72.4ms

  [TIMEOUT] validation.approved
      matcher : fields contain {'status': 'ACCEPTED'}
      why     : no matching message arrived on topic 'validation.approved' within 500ms
      latency : 500.0ms

Timeline (last 20 of 23):
    2.1ms  events.created          published
   14.8ms  state.changed           rejected  (count=500, want 1000)
   38.0ms  state.changed           rejected  (count=750, want 1000)
   72.4ms  state.changed           matched   (count=1000)
  500.0ms  validation.approved     deadline  (no message)
```
