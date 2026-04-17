# 0007. Harness Failure Recovery Policy

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [ADR-0001 §Negative Consequences](0001-single-session-scoped-harness.md) — "If the Harness corrupts mid-suite, every remaining test fails"

---

## Context

[ADR-0001](0001-single-session-scoped-harness.md) concentrates every external connection into one session-scoped Harness. That is right for speed and cleanliness but carries a failure mode: if the Harness itself enters a corrupted state mid-suite — transport SDK thread hang, session death, callback queue exhaustion, socket leak — every remaining test in the session fails, often with confusing unrelated errors.

Today there is no recovery policy. The architect review flagged this as a HIGH gap. This ADR is one of two blockers ([ADR-0010](0010-secret-management-in-harness.md) is the other) that must land before [ADR-0001](0001-single-session-scoped-harness.md) can move from Proposed to Accepted.

### Background

- [ADR-0001 §Cons of Option 1](0001-single-session-scoped-harness.md) acknowledges the failure mode without mitigating it.
- [framework-design.md §3 Option A](../framework-design.md) flagged "one bad test can corrupt shared state".
- Long-lived transport SDKs typically have documented modes where the callback thread wedges (slow-consumer kickout, sequence-reset deadlocks). Assume they happen in production, and therefore in tests.

### Problem Statement

When the Harness enters a corrupted state mid-suite, what should the framework do — for the current test, for the remaining suite, and for diagnostic capture?

### Goals

- **Bounded blast radius.** One corrupted Harness does not silently fail every remaining test.
- **Explicit test classification.** The test during which corruption is detected gets marked **ERROR** (infrastructure failure), not **FAIL** (assertion failure). Distinguishable in CI reports.
- **Diagnostic preservation.** When corruption is detected, enough state is captured (surprise log, transport health, last N messages, Harness repr) to reproduce the issue.
- **No automatic retry.** Retry masks real bugs; the framework does not re-run the failing test.
- **Bounded recovery time.** Rebuild completes within 5 seconds, or gives up and marks all remaining tests ERROR.

### Non-Goals

- Self-healing invisible to the operator. Every rebuild logs a loud event.
- Detection of every possible failure mode. This ADR covers transport / LoopPoster health; deeper issues (memory leaks, slow CPU) are out of scope.
- Crash-recovery across pytest process boundaries. A segfault of the pytest process is separate — operators rerun the suite.

---

## Decision Drivers

- **Correctness over convenience.** A failed suite is better than a suite that silently hides bugs.
- **Debuggability.** A corrupted-Harness incident must leave enough evidence to reproduce.
- **Operator awareness.** Silent recovery is a bug, not a feature.
- **Reproducibility.** Rebuild must not carry state from the old Harness that could change test behaviour.

---

## Considered Options

### Option 1: Fail-fast, fail-all-remaining

**Description:** On detected corruption, abort the suite. Remaining tests are not run.

**Pros:**
- Simplest. No rebuild machinery.
- Operator gets one clear signal.

**Cons:**
- A single transient issue loses coverage of the entire remaining suite.
- Makes the Harness brittle: any flaky-but-recoverable state takes down everything.

### Option 2: Quarantine current test, rebuild Harness, continue (chosen)

**Description:** On detected corruption, mark the current test as ERROR, capture diagnostics, tear down the Harness, build a fresh one, continue with the next test.

**Pros:**
- Suite still completes; one bad test does not poison the rest.
- Explicit ERROR classification tells reviewers which tests are infrastructure-flaky.
- Rebuild is deterministic: fresh Harness has no state from the old one.

**Cons:**
- Adds rebuild complexity to the session fixture lifecycle.
- Rebuild cost (seconds) eats into the suite runtime budget.
- Rebuild itself could fail (cascading). Must have a ceiling.

### Option 3: Retry the current test after rebuild

**Description:** After rebuild, re-run the failing test. If it passes the second time, mark green.

**Pros:**
- Papers over genuine flakes.

**Cons:**
- Masks real bugs. If the test fails because the Harness wedged on a specific inbound, retrying makes the failure invisible.
- A test that passes second time is a test we do not trust.
- Ruled out on correctness grounds.

### Option 4: Best-effort tolerance, no rebuild

**Description:** Log the corruption, keep the broken Harness, let subsequent tests fail as they will.

**Pros:**
- Minimal framework changes.

**Cons:**
- Remaining tests all fail with opaque errors.
- Operator cannot distinguish corruption-induced failures from real bugs.
- Ruled out: violates the bounded-blast-radius goal.

---

## Decision

**Chosen Option:** Option 2 — Quarantine current test, rebuild Harness, continue.

### Rationale

- Option 1 is too pessimistic; a single transient transport hiccup should not lose a 100-test suite's results.
- Option 3 is too optimistic; retrying masks the exact bugs the harness is supposed to catch.
- Option 4 is neither — it loses the suite's signal-to-noise ratio without saving anything.
- Option 2 gives the operator explicit ERROR classifications, a rebuilt Harness with clean state, and a bounded recovery time. It is the only option that preserves diagnostic signal without paying the fail-all-remaining cost.

---

## Consequences

### Positive

- A corrupted-Harness test marks ERROR; the rest of the suite continues.
- Each rebuild is a loud structured event; operators know corruption happened.
- Fresh Harness has no state carry-over; no hidden dependencies between tests across a rebuild.
- Policy is explicit — no reviewer has to guess what the framework does on failure.

### Negative

- Rebuild adds seconds to any suite in which corruption happens.
- Rebuild itself can fail (e.g. transport SDK truly broken). Requires a ceiling: if N rebuilds in a row fail, abort the suite.
- Session-scoped fixture lifecycle becomes more complex; `harness` fixture may produce a different instance before and after the ERROR test.

### Neutral

- Surprise log from the old Harness is archived as a diagnostic artefact, not merged into the new Harness.
- Correlation map is reset on rebuild; any in-flight scenarios on the old Harness become orphan surprise-log entries on their completion (classified `orphaned_harness`).
- Test authors do not see the rebuild directly; the fixture handles it transparently.

### Security Considerations

- Rebuild **must not** carry credentials from the old Harness to the new one. Fresh Harness pulls secrets fresh at its own `connect()` (per [ADR-0010](0010-secret-management-in-harness.md)).
- Diagnostic snapshot on ERROR **must not** include payload content of inbound messages; only metadata (per [ADR-0009](0009-log-data-classification.md)).
- A hostile test that deliberately wedges the transport context to trigger rebuilds could potentially repeat this to observe timing side-channels between old/new Harnesses. Mitigation: cap rebuilds-per-session (N=3 default, configurable); after the cap, fall back to Option 1 behaviour.

---

## Implementation

1. **Health check method:** `Harness.is_healthy() -> bool` returns True when:
   - `self._transport` is connected,
   - `self._poster` is responsive (a fence-post call_soon completes within a small budget),
   - any stateful transport session is logged on and has not exceeded heartbeat-gap thresholds.
2. **Per-test health gate:** a session-scoped fixture runs `harness.is_healthy()` as the first line of every test. Failure raises `HarnessCorruptedError`, which the pytest plugin catches.
3. **Plugin hook:** `pytest_runtest_protocol` wraps each test — on `HarnessCorruptedError`, the test is marked ERROR, diagnostics captured, rebuild triggered.
4. **Rebuild sequence:**
   - Capture diagnostic snapshot: `{harness_id, surprise_log, active_subscriptions, uptime_seconds, last_error}` → artefact directory.
   - `await old_harness.disconnect()` best-effort (timeout 2s).
   - `new_harness = Harness(config=..., ...); await new_harness.connect()` (budget 3s).
   - Replace the fixture's yielded value with the new Harness.
   - Increment rebuild counter.
5. **Rebuild ceiling:** if `rebuild_counter >= 3`, stop rebuilding — all remaining tests ERROR, suite aborts after the current one.
6. **Diagnostic snapshot format:** JSON, one file per rebuild, named `rebuild-<session_id>-<N>.json`, placed in a configurable directory (default `.pytest_cache/rebuilds/`).

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with real LBM transport work): implement `Harness.is_healthy()` and the per-test health gate. Stub rebuild as Option 1 behaviour initially.
- Phase 2: full rebuild machinery, diagnostic snapshot, rebuild ceiling.
- Phase 3: operational tuning — adjust health-check thresholds, ceiling, and snapshot format based on observed CI behaviour.

---

## Validation

### Success Metrics

- **Corrupted-Harness recovery rate:** when detection fires, at least 95% of the time the rebuild succeeds and the suite completes. Measured monthly.
- **Rebuild time budget:** 95% of rebuilds complete in under 5 seconds.
- **False positive rate:** fewer than 1% of health-check failures turn out to be benign on retry. A false positive costs a rebuild; too many means the health check is too strict.
- **ERROR vs FAIL distinction:** every CI report clearly separates infrastructure ERRORs from assertion FAILs.
- **Rebuild ceiling hit rate:** fewer than 0.1% of suites. A higher rate means the Harness itself is the problem.

### Monitoring

- Counter: rebuilds per session (target: almost always zero).
- Structured log on every rebuild with `{reason, uptime, test_nodeid, snapshot_path}`.
- CI report pulls rebuild snapshots into the artefacts tab for every failing run.
- Alert threshold: >5 rebuilds per 100-test suite → investigate; the Harness is misbehaving.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single Harness (the decision this ADR unblocks).
- [ADR-0009](0009-log-data-classification.md) — Diagnostic snapshot must respect the log classification policy.
- [ADR-0010](0010-secret-management-in-harness.md) — Rebuild must not carry credentials.

---

## References

- [framework-design.md §10 Bulkhead](../framework-design.md) — the pattern deliberately not used; this ADR is the "bulkhead for the test harness" equivalent scoped to one session.
- Transport SDK documentation on slow-consumer kickout and context-thread recovery (environment-dependent).

---

## Notes

- **Open follow-up — detection for transport-specific corruption.** Some transport SDKs expose limited health signals. A watchdog thread that monitors context heartbeat would help but needs deeper integration with each SDK. Tracked as a stretch goal for Phase 3. **Owner:** Platform.
- **Open follow-up — session fixture swap.** pytest does not naturally support a session-scoped fixture whose value changes mid-session. The plugin hook has to override the fixture's cached value. Prototype needed. **Owner:** Platform / Test Infrastructure.
- **Failure modes not covered here:** segfaults of the pytest process (operator restart); OOM (systemd / cgroup handles); malicious infinite loops in test code (pytest-timeout). These remain the operator's problem.

**Last Updated:** 2026-04-17
