# Stage Scenario Timeline Capture - Per-Transport Event Recording in `results.json` and the HTML Report - Product Requirements Document

**Document ID:** PRD-013
**Status:** Approved (v2 - review cycle 2026-05-04; Phase 2 review cycle 2026-05-05)
**Author:** Choreo framework team
**Created:** 2026-05-04
**Last Updated:** 2026-05-05
**Extends:** [PRD-007 - Test Report Output](PRD-007-test-report-output.md), [PRD-012 - Test Report Stage Support](PRD-012-test-report-stage-support.md)
**Driven by:** [ADR-0027 - Stage Coordinator](../adr/0027-stage-multi-transport-coordinator.md)
**Coordinates with:** [PRD-009 - Chronicle Reporting Server](PRD-009-chronicle-reporting-server.md)

---

## Revision note

**v2 vs v1** (current): closes the multi-actor review cycle 2026-05-04. Material changes from v1 to v2:

- Dropped the `<scope>` regex sentinel for scope-level events; the `transport` field is OMITTED for `DEADLINE` events instead (resolves D-3 / D-3a internal inconsistency).
- Codebase-fact corrections: `_Timeline.record` (was incorrectly named `_Timeline.append`); `TimelineEntry` shape including the existing `wall_clock` `@property`; verified-linear Alembic chain (no multi-head); `_resolve_handle_on_match` records MATCHED/MISMATCHED directly via optional `timeline` + `transport` kwargs (cleaner than the v1 attempts-diff design).
- Per-run aggregate timeline cap (50,000 entries, §D-4) on top of the existing per-scope cap (256), addressing the cap-saturated-workload file-size analysis.
- Test-tool redaction posture explicit in §Security: payload-derived values stay un-redacted in `MISMATCHED` `detail` (diagnostic value beats redaction theatre); wire-id mismatches in `CORRELATION_SKIPPED` use `redact_correlation_id` (preserves PRD-012 §1.5.1).
- Hook-point table precision: exact code-path references; `on_sent` plumbing for POST-publish `PUBLISHED`/`REPLIED` semantics.
- Phase 1 visibility banner specified so consumers see "Stage timeline captured" in the HTML before Phase 2's swim-lane renderer ships.
- Reply-arrow pairing algorithm specified (single linear scan with per-(scope, trigger_topic) "last received" map).
- Renderer reuses existing `hr-waterfall-*` class taxonomy; no parallel `hr-timeline-*` introduced.
- Chronicle migration 005 specifies `chunk_time_interval` and three indexes; truncation list updated.
- Testing strategy: per-action unit tests (one per `TimelineAction`), negative-path coverage, synthetic clock for property-based tests, mutation testing as a Phase 1 deliverable.
- All cross-references corrected; OQ-3 has a decide-by deadline; Phase 3 dashboards has a tracking owner.

**v1 - historical context.** PRD-012 (signed off 2026-05-04) intentionally deferred Stage timeline capture to a follow-up PRD. PRD-012's reporter shipped `"timeline": []` for Stage scenarios as the explicit deferral marker; PRD-013 fills the gap with framework instrumentation (eight hook points), JSON shape (one new optional field plus per-run aggregate cap), Chronicle persistence (new `timeline_events` hypertable), and renderer additions (swim-lane mode + reply-arrow pairing).

**Net schema impact:** minor bump to `"1.3"` (additive - every change is an optional field). Phase 2 shipped v1.2 (timeline_entry.transport / logical_topic / topic-optional); Phase 2.5 ships v1.3 (timeline_entry.source for DSL-surface attribution, §1.6). Every v1.1-valid report validates against v1.2 / v1.3 after `schema_version` rewrite. v1.1 and v1.2 schemas stay in tree for pinned consumers; `test-report-v1.3.json` is the new active schema.

---

## Executive Summary

A test author writing a multi-transport (`Stage`) scenario can already see, in the report, what was registered (expectations, reply chains) and what was fulfilled (handle outcomes, reply lifecycle). What they cannot see is **the chronology of messages flowing across transports** - when did the test publish on transport A, when did the broker deliver to transport B, when did the matcher accept or reject, when did the deadline fire. Without that chronology, diagnosing a failing Stage test requires reading framework debug logs or running the test under a debugger to trace the message flow.

PRD-013 specifies **timeline capture for Stage scenarios** with these properties:

- Eight observable events (`PUBLISHED`, `RECEIVED`, `MATCHED`, `MISMATCHED`, `CORRELATION_SKIPPED`, `DEADLINE`, `REPLIED`, `REPLY_FAILED`) recorded at their observable boundaries on the Stage code path.
- Every Stage timeline entry produced by a per-transport child carries a `transport` attribution naming that child (e.g. `nats` / `kafka`). Single-`Harness` timelines remain unchanged (no `transport` field).
- Scope-level events (currently only `DEADLINE`) OMIT the `transport` and `topic` fields entirely - no sentinel string, no `null` overload (D-3).
- Test-tool redaction posture: payload values un-redacted (diagnostic value); wire-ids hash-redacted via `redact_correlation_id` (preserves PRD-012 §1.5.1's report-boundary redaction).
- Per-scope cap of 256 events (existing) plus a per-run aggregate cap of 50,000 events at the reporter boundary (D-4).
- Reporter populates `scenario.timeline[]` for Stage scenarios (replaces the v2-era `[]` deferral marker) and bumps `schema_version` to `"1.2"`.
- Renderer activates "swim-lane" mode for Stage scenarios with transport-attributed entries: per-transport lanes, dedicated scope-events lane, cross-transport reply-arrow overlay (pairing via single linear scan).
- Virtualisation under cap-saturated workloads: timelines below 500 entries mount eagerly; at/above the threshold the renderer mounts the first 500 entries and exposes an expansion control.
- Chronicle gains a `timeline_events` hypertable for per-run timeline ingest (Phase 3).

---

## Problem Statement

### Current State

PRD-012 shipped Stage scenario support in the test report: `scenario.stage` block, per-handle `transport`, hash-redacted per-transport correlation ids. PRD-012 explicitly deferred the timeline to a follow-up because the single-`Harness` timeline shape (one ring buffer per scope, per-topic causal tree in the renderer) does not naturally extend to multi-transport scenarios where causal flow crosses transport boundaries. The deferral marker today: every Stage scenario in `results.json` carries `"timeline": []` regardless of what the framework observed.

### User Pain Points

- **A failing Stage test is opaque.** A `TIMEOUT` on a Stage handle says "no message arrived on `results` for this scope within 50ms" - but the QA cannot see whether a message was published on the trigger transport, whether anything arrived on the response transport (correlation-rejected or otherwise), whether the bridge translation fired. The PRD-012 lifecycle redesign (Pass 2) gives the per-handle "did the matcher receive a candidate" answer; the timeline gives the chronological "what flowed across the wire" answer.
- **Cross-transport bridge diagnosis requires log archaeology.** A bridge AUT translating from Kafka to NATS leaves no first-class artefact in the report when it misroutes; the QA reads framework logs or rerun-with-DEBUG to see the wire id translation chain.
- **Replay / regression triage is manual.** A flaky reply test cannot be triaged from the report alone; the QA cannot see whether the trigger arrived but the build callback raised, or whether the response was published but the receiver-side correlation filter rejected it.

### Business Impact

- A timeline that surfaces the chronological wire activity makes Stage tests as triagable from the report as single-`Harness` tests today (the established baseline).
- Chronicle ingest of timeline data unlocks per-tenant cross-run comparison (Phase 3) - a regression dashboard that pairs an older healthy run against the current failing run by aligning their timeline events.

---

## Goals and Objectives

### Primary Goals

1. **Make Stage tests as triagable as single-`Harness` tests** - parity on what the report shows the QA per scope.
2. **Preserve the JSON byte-identity contract for single-`Harness` scenarios** - PRD-012's snapshot test (US-6) keeps holding under v1.2.
3. **Schema additivity** - every v1.1-valid report validates against v1.2 after `schema_version` rewrite.
4. **Test-tool diagnostic posture** - payload values stay un-redacted in matcher mismatch reasons; wire ids hash-redact at the report boundary.

### Success Metrics

- 100% of the eight TimelineAction values are recorded for Stage scopes that exercise the corresponding paths.
- Per-scope cap (256) + per-run aggregate cap (50k) keep cap-saturated workloads inside PRD-007 §file-cap headroom.
- Renderer time-to-interactive for ≤500-entry timelines: ≤60ms (eager-mount path).

### Non-Goals

- Cross-process / xdist-worker timeline merging at the framework layer (consumers reconstruct via Chronicle).
- Real-time streaming of timeline events during a test run (post-run snapshot only).
- Schema-level enforcement of per-transport event ordering (observation order, not wire-arrival order; §2.4).

---

## User Stories

### Primary User Stories

**US-1.** As a QA reading a failing Stage report, I can see the chronological event flow across all registered transports without leaving the report (no log archaeology).

Acceptance criteria:
- [ ] Each `scenario.timeline[]` entry produced by a per-transport child carries a `transport` field naming that child (e.g. `"nats"`, `"kafka"`). Scope-level events (e.g. the `DEADLINE` event when `await_all`'s `timeout_ms` fires) OMIT the field entirely - see §D-3.
- [ ] The renderer surfaces per-transport swim-lanes for Stage scopes that exercise multiple transports.
- [ ] Cross-transport reply linkage is visible in the report (PRD-013 §4.2).

**US-2.** As a Phase-1-only deployment consumer, I can see in the HTML report that Stage timelines are being captured even before the swim-lane renderer ships in Phase 2.

Acceptance criteria:
- [ ] A "Stage timeline captured: N events across M transports" banner appears for Stage scenarios with non-empty timeline data.

**US-3.** As a strict-validator consumer pinned to v1.1, I can adopt v1.2 by updating the pinned schema reference; my existing assertions on v1.1 fields continue to hold.

Acceptance criteria:
- [ ] v1.2 is purely additive on `timeline_entry` (no field removed, no enum value removed, no required field added).

---

## Functional Requirements

### 1. Schema additions: `test-report-v1.1.json` -> `test-report-v1.2.json`

#### 1.1 `timeline_entry.transport`

Required: optional. Single-`Harness` timeline entries MUST OMIT the key entirely (not emit `null`). Stage timeline entries produced by a specific per-transport child MUST emit a non-null string naming that child. Stage timeline entries that are **scope-level** (the only case today is `DEADLINE` when `await_all`'s `timeout_ms` fires) OMIT the field - they are the entire scope's event, not produced by any one child. See §D-3.

Schema fragment:
```json
"transport": {
  "type": "string",
  "pattern": "^[a-zA-Z0-9_-]{1,64}$",
  "description": "Per-transport attribution. OMITTED for single-Harness entries and scope-level Stage events."
}
```

#### 1.2 `timeline_entry.topic` relaxation

`topic` becomes optional. Scope-level events (DEADLINE) omit the field. Symmetric with the `transport` omission rule (D-3).

#### 1.3 `timeline_entry.logical_topic`

Forward-compatibility groundwork: future translating bridges may set a `logical_topic` distinct from the wire `topic`. Today's `MappedBridge` does not translate topics, so this field is always omitted.

#### 1.4 `scenario.timeline_dropped` semantics unchanged

The per-scope ring-buffer overflow counter from PRD-012; populated for Stage scopes from `_StageScenarioScope._timeline.dropped`.

#### 1.6 `timeline_entry.source` (schema v1.3)

Required: optional. Names the DSL surface that produced the event so consumers can disambiguate a test-side publish from a reply-chain's automatic response on the same topic.

Schema fragment:
```json
"source": {
  "enum": ["publish", "expect", "reply", "scope"],
  "description": "Which DSL surface produced this event (PRD-013 §1.6). `publish` = test-side `scope.publish` / `harness.publish`; `expect` = subscriber registered by `scope.expect` / `s.expect`; `reply` = reply-chain registered by `scope.on(...).publish(...)`; `scope` = scope-level framework event (DEADLINE)."
}
```

Per-action mapping:

| Action | Source | Site |
|---|---|---|
| `PUBLISHED` | `"publish"` | `_StageScenarioScope.publish` (test-initiated) |
| `RECEIVED` | `"expect"` or `"reply"` | `expect()`'s `_on_message` vs reply chain's `_on_trigger` |
| `MATCHED` | `"expect"` | matcher-accept branch of `_resolve_handle_on_match` |
| `MISMATCHED` | `"expect"` | matcher-reject branch of `_resolve_handle_on_match` |
| `CORRELATION_SKIPPED` | `"expect"` or `"reply"` | inherits from caller of `_decode_and_correlation_check` |
| `DEADLINE` | `"scope"` | `_StageScenarioScope.await_all` timeout branch |
| `REPLIED` | `"reply"` | `_register_stage_reply._on_trigger` post-publish `on_sent` |
| `REPLY_FAILED` | `"reply"` | reply-chain build/publish exception branch |

Single-`Harness` entries continue to omit the field entirely (byte-identity preserved).

### 2. Framework changes (`choreo` core)

#### 2.1 `TimelineEntry.transport`

`@dataclass(frozen=True, slots=True)` `TimelineEntry` (in `packages/core/src/choreo/scenario.py`) gains optional `transport: str | None = None` and `logical_topic: str | None = None` fields. `topic` is relaxed to `str | None`. The frozen contract is preserved (`__post_init__` enforces the detail-truncation cap as defence-in-depth).

`_Timeline.record(*, now, topic, action, detail="")` (the existing recording method) gains additional kwarg-only `transport: str | None = None` and `logical_topic: str | None = None`. Every existing single-`Harness` call site continues to omit the new kwargs. `_Timeline.record` swallows internal exceptions and exposes a `record_errors` counter - an observability seam must never break the AUT.

#### 2.2 `_StageScenarioScope` captures a timeline

`_StageScenarioScope` gains a `_Timeline` instance constructed at `__aenter__`. The timeline is exposed on `StageScenarioResult` via new `timeline: tuple[TimelineEntry, ...]` and `timeline_dropped: int` fields. After `await_all` snapshots the timeline, the underlying `_Timeline` is sealed (`_Timeline.sealed = True`) so late inbound callbacks (subscriptions stay live until `__aexit__`) cannot mutate the snapshot's counters.

#### 2.3 Hook points where Stage events are recorded

The framework instruments each event at its observable boundary in `packages/core/src/choreo/stage.py`. Recording goes through a small `_record_event(timeline, *, action, topic, transport, detail, logical_topic, now)` helper that handles the optional-timeline guard.

| TimelineAction | Recording site (in `stage.py`) | Transport value | `detail` content |
|---|---|---|---|
| `PUBLISHED` | `_StageScenarioScope.publish` via the `on_sent` callback (post-wire) | the `on=` argument (publish target) | `""` (empty) |
| `RECEIVED` | `_decode_and_correlation_check` on the accept path | the receiving child's transport name | `""` (empty) |
| `CORRELATION_SKIPPED` | `_decode_and_correlation_check` on the correlation-mismatch path | the receiving child's transport name | the wire-id mismatch, **hash-redacted via `choreo.redaction.redact_correlation_id`** (preserves PRD-012 §1.5.1 - see §Security) |
| `MATCHED` | `_resolve_handle_on_match` matcher-accept branch | the expectation's transport (`Handle.transport`) | `""` (empty) |
| `MISMATCHED` | `_resolve_handle_on_match` matcher-reject branch | the receiving child's transport name | `result.reason` from the matcher; **un-redacted** - payload values stay visible per the test-tool framing in §Security |
| `DEADLINE` | `_StageScenarioScope.await_all` `TimeoutError` branch | OMITTED - scope-level event (`topic` is also OMITTED for the same reason; see §D-3) | descriptor of the deadline that fired (e.g. `"timeout_ms=200"`) |
| `REPLIED` | `_register_stage_reply._on_trigger` via the `on_sent` callback wrapping `response_harness.publish` (post-wire) | the response transport (publish target) | trigger topic (e.g. `"trigger=orders.new"`) |
| `REPLY_FAILED` | `_register_stage_reply._on_trigger` build/publish exception branch | the response transport | response topic + exception class name only - `f"reply={topic} error={type(exc).__name__}"` (per ADR-0017 §Security Considerations; consistent with single-`Harness` scenario.py:1058) |

##### 2.3.1 `PUBLISHED` and `REPLIED` recording semantics - pre vs post-publish

Single-`Harness` scopes record `PUBLISHED` POST-publish via the transport's `on_sent` callback: the recording reflects "the bytes have left the wire". Stage's publish + reply-fire paths use the same `on_sent` pattern so PRD-013 timestamps match single-`Harness` semantics.

##### 2.3.2 `MISMATCHED` detection mechanism

`_resolve_handle_on_match` already discriminates `result.matched` vs the reject branch when applying the matcher; the implementation records `MATCHED` and `MISMATCHED` directly at those branches by accepting an optional `timeline` + `transport` kwarg pair. The matcher's `result.reason` is recorded verbatim as `MISMATCHED.detail`. Per §Security the detail is emitted un-redacted.

#### 2.4 Aggregation order

Timeline entries are recorded in **observation order**: the order events are seen by the recording sites on the asyncio event loop. `offset_ms` is computed at recording time from `loop.time()`. Two consequences worth surfacing:

- **Same-tick events:** if two events fire in the same microsecond they share an `offset_ms` and the timeline carries them in recording order.
- **Cross-thread inbound:** transports that dispatch via `loop.call_soon_threadsafe` from a background thread record in the loop's pickup order. That order is not necessarily wire-arrival order. Consumers must treat `offset_ms` as observation time, not wire-arrival time.

No post-hoc sort is performed; the recorded order IS the timeline.

### 3. Reporter changes (`choreo-reporter`)

#### 3.1 Stage timeline serialisation

`_serialise_stage_scenario()` populates `timeline` and `timeline_dropped` from `result.timeline` / `result.timeline_dropped` (replacing the v2-era `[]`/`0` deferral markers). Per-entry serialisation goes through `serialise_timeline_entry()` which omits optional JSON fields when their Python value is `None` - preserves PRD-012 byte-identity for single-`Harness` (no surprise `null` regression) and satisfies §1.1's omission rule for scope-level Stage events.

#### 3.2 Schema version bump

`schema_version` bumps from `"1.1"` to `"1.2"` in `_collect.py`. Additive minor bump; consumers gating on `schema_version.startswith("1")` continue to work.

### 4. Renderer changes (HTML)

#### 4.1 Per-transport swim lanes

The renderer activates swim-lane mode when the timeline contains at least one entry whose `transport` field is set AND that value appears in the scenario's `scenario.stage.transports` list. The `isSwimLaneMode(scenario, nodes)` helper makes the activation rule explicit.

In swim-lane mode, the existing `hr-waterfall-*` taxonomy is reused: a `hr-waterfall-lane` wrapper element per transport, ordered by `scenario.stage.transports` (registration order). A dedicated scope-events lane (`data-scope-lane="true"`) holds `DEADLINE` and other topic-less entries.

##### 4.1.1 Virtualisation under cap-saturated workloads

Single-`Harness` timelines and small Stage timelines (`<500` events) skip virtualisation - the existing eager-mount path renders them at lower latency. At/above the threshold (`VIRTUALISATION_THRESHOLD = 500`), the renderer mounts the first 500 entries and exposes an expansion control. The Phase-2 implementation is bounded-mount + click-to-expand; true scroll-windowed virtualisation is a follow-up if profiling demands it.

#### 4.2 Cross-transport reply linkage

A `pairReplyArrows(nodes)` helper does a single linear scan with a per-topic last-RECEIVED map. For each REPLIED entry, the trigger topic is parsed from `detail` (`trigger=<topic>`) and paired against the last RECEIVED on that topic. Pairs render as an SVG path overlay above the lanes.

##### 4.2.1 Reply-arrow pairing algorithm

```
last_received_by_topic = {}
pairs = []
for node in nodes (in observation order):
    if node.action == 'received' and node.topic:
        last_received_by_topic[node.topic] = node
    elif node.action == 'replied':
        m = match /trigger=(\S+)/ on node.detail
        if m and last_received_by_topic[m[1]]:
            pairs.append({from: last_received_by_topic[m[1]], to: node})
return pairs
```

O(events) cost. No cross-scope pairing (intentional - pairing across scopes is a Chronicle-level concern).

#### 4.3 Phase 1 / Phase 2 visibility gap

When a Phase 1-only framework deployment ships before the Phase 2 renderer's swim-lane support is ready, consumers see Stage timeline JSON but no Stage-aware visualisation. The renderer prepends a `data-stage-timeline-banner` div for Stage scenarios with non-empty timeline data so the QA sees "Stage timeline captured (N events across M transports)" before the swim-lane mode is wired. The banner persists as a semantic header above the swim-lane waterfall in Phase 2.

### 5. Chronicle ingest changes (Phase 3)

A new `timeline_events` hypertable in TimescaleDB; one row per `TimelineEntry`, indexed on (run_id, scope_id) + (transport) + (action). Migration `005` extends from `004` (linear chain).

---

## Non-Functional Requirements

### Performance

- **Per-message recording overhead:** ~600-900ns per inbound message (validated under cap-saturated workload).
- **JSON write time:** timeline entries grow the JSON payload by ~80-120 bytes per event. For a Stage workload at PRD-007's reference scale (100 scopes × 30 events = 3000 entries), payload grows ~300 KB. At the cap-saturated workload (1000 scenarios × 256-event per-scope cap = up to 256k events naïvely), the aggregate timeline cap of 50k entries (§Memory) bounds the JSON growth at ~5 MB.
- **`wall_clock` ISO formatting:** `TimelineEntry.wall_clock` `@property` formats from a stored epoch float (~3-5μs per call). For 50k entries at serialisation, that is 150-250 ms - within the PRD-012 §US-7 cap-saturated JSON-write budget (`<2s`).

### Memory

- **Per-scope cap:** 256 entries × ~80-120 bytes = 20-30 KB per scope.
- **Per-run aggregate cap:** 50,000 entries enforced at the reporter boundary (§D-4).
- **Aggregate report:** a Stage run with 50k timeline entries × ~80-120 bytes/entry serialised + 50k DOM nodes rendered fits inside PRD-012's 11 MB JSON cap and remains within the renderer time-to-interactive budget when virtualisation is on.

### Correctness of the JSON contract

- Optional fields (`topic`, `transport`, `logical_topic`) are OMITTED when None - never emitted as `null`. Preserves the byte-identity contract for single-`Harness` entries and the per-§D-3 omission rule for scope-level Stage events.

### Security / data handling

- **Test-tool framing.** A test report's diagnostic value is the actual rejected payload, the actual mismatch reason, the actual exception. A QA reading the report at 09:30 with a failing CI needs to see *what the matcher saw and why it rejected*. Stripping payload-derived content from the report removes the very evidence the report exists to surface. Test report archives should be handled like test-fixture data - they may carry payload-derived values; access controls and retention policy belong in CI infrastructure, not in framework redaction logic.
- **`MISMATCHED` and `MATCHED`-on-`SLOW` events keep payload-derived `detail` un-redacted.** The `detail` field for these events records the matcher's actual rejection reason, including the rejected payload value (`actual!r`). Consistent with single-`Harness` behaviour (`scenario.py:868` - `detail=result.reason`). Consumers / archives that cannot tolerate payload values in test reports must filter at ingest, not at recording.
- **`REPLY_FAILED` events carry the exception class name only** (per ADR-0017 §Security Considerations) - `str(exc)` is NOT included.
- **`CORRELATION_SKIPPED` events hash-redact the wire-id mismatch in `detail`** via `choreo.redaction.redact_correlation_id` - preserving PRD-012 §1.5.1's report-boundary hash redaction. Asymmetric vs MISMATCHED above: payload values stay un-redacted because they have diagnostic value; wire ids hash-redact because they don't.
- **Transport names** land in the `transport` field; the schema regex `^[a-zA-Z0-9_-]{1,64}$` constrains the character class. Stage validates at `__init__` so consumer-supplied names cannot propagate into the timeline / results.json / Phase 2 renderer where they would otherwise become an injection surface.
- **Topic names** carry whatever the test author named them. PRD-013 does not redact topic names - the test author chose the convention; the report emits it.

---

## Decisions Already Made

### D-1. Use `_Timeline` (not a new class) on `_StageScenarioScope`

**Rejected alternatives:** subclass with Stage-specific recording methods (rejected - `_Timeline.record` accepts an optional `transport` after PRD-013 §2.1, no additional behaviour needed).

### D-2. `transport` field is optional, omitted (not null) for single-`Harness`

**Rejected alternatives:** always emit `transport: null` for single-`Harness` (rejected - breaks PRD-012's snapshot byte-identity contract).

### D-3. `DEADLINE` event recording on Stage - omit `transport`, do not invent a sentinel

Scope-level events (today only `DEADLINE`) omit the `transport` field entirely. Symmetric with `topic` (also omitted; see §D-3a).

**Rejected alternatives:**
- **Sentinel string** `transport: "<scope>"` - rejected. Forces a schema-regex relaxation; overloads the `transport` string field with a non-transport value (in-band-signalling anti-pattern); creates a forgery surface.
- **Emit `null`** - rejected. The schema already permits `["string", "null"]` for some fields, but null is the representation of "absent / single-`Harness`" and overloading it with "scope-level deadline" erodes the type distinction at the consumer query layer.
- **Replicate one `DEADLINE` per registered transport** - rejected. Multiplies a single conceptual event into N entries with no information gain.

### D-3a. Renderer mode detection: real-transport requirement

The renderer activates swim-lane mode when the timeline contains at least one entry whose `transport` field is set AND that value appears in the scenario's `scenario.stage.transports` list. A Stage scenario whose only timeline event is a scope-level DEADLINE (no `transport` set on any entry) does NOT activate swim-lane mode - it falls through to the single-`Harness` per-topic layout because there is nothing transport-specific to lane.

### D-4. Aggregate timeline cap (per-run)

Per-run aggregate cap of 50,000 entries enforced at the reporter boundary, on top of the existing per-scope cap of 256.

**Rejected alternatives:**
- **Per-scope cap only** - rejected. At PRD-012's cap-saturated workload (1000 scenarios × 256-event cap = 256k events × ~120 bytes = ~30 MB), the timeline alone blows past PRD-007's 11 MB hard refuse and PRD-012's headroom by ~3×.
- **Bump the file cap** to e.g. 50 MB - rejected. The file cap is a memory-bound for the renderer's inlined JSON.
- **No cap, rely on consumer truncation** - rejected.

### D-5. Schema bump v1.1 -> v1.2 (additive minor)

Every change is an optional field. Every v1.1-valid report validates against v1.2 after `schema_version` rewrite. v1.1 stays in tree for consumers pinned to it; v1.2 is the new active schema.

### D-6. Reuse existing `hr-waterfall-*` class taxonomy

Renderer additions wrap existing rows in `hr-waterfall-lane` group elements rather than introducing a parallel `hr-timeline-*` taxonomy. Single-class hierarchy keeps CSS, tests, and documentation cohesive.

### D-7. Package version + JSON field lockstep

The `choreo-reporter` package version moves in lockstep with the emitted `schema_version`. v1.2 schema = same package release window. Documented in CHANGELOG.

---

## Open Questions

- **OQ-1.** True scroll-windowed virtualisation vs the Phase-2 bounded-mount + click-to-expand pattern. Decision deferred until profiling shows the click-to-expand pattern hurts at real cap-saturated workloads.
- **OQ-2.** Reply-arrow runtime layout pass: minimal straight-line layout vs Bezier curves between lane Y-coordinates. Decided in Phase 2 review cycle: minimal layout pass on boot, lay out arrows as straight diagonals between the row centres.
- **OQ-3.** Phase 3 Chronicle dashboards owner + tracking artefact. To be picked up under PRD-013.x.

---

## Phase break-down

- **Phase 1**: framework instrumentation. Six PRs (1.1-1.6) wiring eight hook points + foundation work.
- **Phase 2**: reporter + renderer. Four PRs (2.1-2.4): schema bump + JSON serialisation; Phase 1/2 visibility banner + DEADLINE-safe waterfall; swim-lane mode + reply-arrow pairing; virtualisation.
- **Phase 3**: Chronicle ingest. Tracked under PRD-013.x.

---

## Appendices

### Appendix A: TimelineAction enum values

```python
class TimelineAction(StrEnum):
    PUBLISHED = "published"
    RECEIVED = "received"
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    CORRELATION_SKIPPED = "correlation_skipped"
    DEADLINE = "deadline"
    REPLIED = "replied"
    REPLY_FAILED = "reply_failed"
```

### Appendix B: JSON shape example (v1.2)

```json
{
  "schema_version": "1.2",
  "run": { "...": "abridged - see PRD-007 §3 for the full envelope" },
  "tests": [
    {
      "...": "abridged - file/name/markers/etc. per PRD-007 §3",
      "scenarios": [
        {
          "name": "bridge_round_trip",
          "stage": {
            "bridge_class": "MappedBridge",
            "transports": ["kafka", "nats"],
            "correlation_ids": {
              "kafka": "sha256:7e8b50c1ad4912ff",
              "nats":  "sha256:3f2a91b8c4d50e1f"
            }
          },
          "timeline": [
            {"offset_ms": 0.0, "wall_clock": "...", "action": "published", "detail": "", "topic": "orders.new", "transport": "kafka"},
            {"offset_ms": 0.5, "wall_clock": "...", "action": "received",  "detail": "", "topic": "orders.new", "transport": "kafka"},
            {"offset_ms": 1.0, "wall_clock": "...", "action": "replied",   "detail": "trigger=orders.new", "topic": "orders.processed", "transport": "nats"},
            {"offset_ms": 1.5, "wall_clock": "...", "action": "received",  "detail": "", "topic": "orders.processed", "transport": "nats"},
            {"offset_ms": 2.0, "wall_clock": "...", "action": "matched",   "detail": "", "topic": "orders.processed", "transport": "nats"},
            {"offset_ms": 12.0, "wall_clock": "...", "action": "deadline", "detail": "timeout_ms=200"}
          ],
          "timeline_dropped": 0
        }
      ]
    }
  ]
}
```

Note the DEADLINE entry omits both `topic` and `transport` (scope-level, per §D-3); other entries carry per-transport attribution.

### Appendix C: Stable-tier renderer data attributes

| Attribute | Where | Purpose |
|---|---|---|
| `data-stage-timeline-banner` | `<div>` above the waterfall | Phase 1/2 visibility banner |
| `data-scope-event` | `<div class="hr-waterfall-row">` | Tags scope-level events (DEADLINE) |
| `data-transport` | `<div class="hr-waterfall-lane">` and per-row | Per-transport attribution |
| `data-scope-lane` | scope-events lane wrapper | Routes topic-less entries |
| `data-reply-link-from` / `-to` | SVG `<path>` per arrow | Reply-arrow pairing endpoints (node ids) |
| `data-virtualised` | timeline host | Bounded-mount marker |
| `data-virtualised-shown` / `-total` | timeline host | Mount progress counters |
| `data-virtualised-expand` | expansion `<button>` | Bounded-mount expansion control |

---

## Related docs

- [docs/context.md](../context.md) §15 - global writing style rules
- [docs/framework-design.md](../framework-design.md) - architecture overview
- [docs/adr/](../adr/) - architectural decisions
- [docs/adr/0017-reply-fire-and-forget-results.md](../adr/0017-reply-fire-and-forget-results.md) - reply-error redaction posture
- [docs/adr/0027-stage-multi-transport-coordinator.md](../adr/0027-stage-multi-transport-coordinator.md) - Stage architecture
- [docs/prd/PRD-007-test-report-output.md](PRD-007-test-report-output.md) - report output base spec
- [docs/prd/PRD-008-scenario-replies.md](PRD-008-scenario-replies.md) - reply lifecycle
- [docs/prd/PRD-009-chronicle-reporting-server.md](PRD-009-chronicle-reporting-server.md) - Chronicle PRD
- [docs/prd/PRD-011-multi-transport-stage.md](PRD-011-multi-transport-stage.md) - Stage product spec
- [docs/prd/PRD-012-test-report-stage-support.md](PRD-012-test-report-stage-support.md) - report support for Stage
- [docs/guides/stage.md](../guides/stage.md) - Stage user guide
