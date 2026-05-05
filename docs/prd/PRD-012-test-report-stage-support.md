# Test Report Stage Support — Multi-Transport Scenarios in `results.json` and the HTML Report — Product Requirements Document

**Status:** Approved (v3 — sign-off 2026-05-04)
**Created:** 2026-05-04
**Last Updated:** 2026-05-04
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering, Chronicle package owners, `choreo-reporter` package owners
**Extends:** [PRD-007 — Test Report Output](PRD-007-test-report-output.md)
**Driven by:** [ADR-0027 — Stage Coordinator](../adr/0027-stage-multi-transport-coordinator.md), [PRD-011 — Multi-Transport Scenarios](PRD-011-multi-transport-stage.md)
**Coordinates with:** [PRD-009 — Chronicle Reporting Server](PRD-009-chronicle-reporting-server.md) (ingest migration is a Phase 1 dependency)

---

## Revision note

**v3 vs. v2** (current): v2's headline "fully additive, `schema_version` stays at `'1'`" claim was technically false for strict-validator consumers (relaxing `run.transport` from required to optional changes the schema document under a fixed version, and PRD-007's policy is to minor-bump on additive change). v3 also closes review findings from review cycle 2: `oneOf`→`anyOf`, no enum extension (map `FIRED`→`"replied"`, `FIRED_BUILDER_ERROR`→`"reply_failed"`), explicit `kind` field for Stage detection (replaces duck-typing), single-source redaction with hash-not-truncate at the report boundary, redaction of `handle.correlation_id` for Stage handles, Chronicle migration sequenced as Phase 1 blocker, HTML-escape mandate, transport-name regex tightening, and a tiered `data-*` contract (stable vs. advisory).

Net schema impact in v3:

- **`schema_version` bumps to `"1.1"`** (PRD-007 §US-3 policy: additive field is a minor bump).
- **No enum extension.** `state` keeps its existing four values; the framework's `StageReplyState` is mapped to those four at serialisation. Eliminates the dual-vocabulary leak.
- **All other fields remain optional and additive**, no breaking change to single-`Harness` consumers other than the schema-version string.

**v2 vs. v1** (historical): v2 expanded the lean v1 draft (~280 lines) to PRD-007's depth — added user stories with acceptance criteria, concrete schema diff, non-functional requirements, decisions-already-made section, risks and mitigations, a delivery-plan phasing, and two worked appendices.

---

## Executive Summary

The `choreo-reporter` package emits a versioned `results.json` and a self-contained `index.html` per pytest run, per [PRD-007](PRD-007-test-report-output.md). The schema and the renderer assume a single-`Harness` model: every scenario has one `correlation_id`, every handle reports a topic + outcome with no transport attribution, every reply report is single-transport. With Stage now in the framework ([PRD-011](PRD-011-multi-transport-stage.md), [ADR-0027](../adr/0027-stage-multi-transport-coordinator.md)), scenarios span multiple transports and the report has nothing to surface this with.

This PRD specifies an **additive extension** to `test-report-v1` that captures Stage scenarios faithfully:

- `Handle.transport` round-trips through the JSON as an optional field on each handle.
- `_StageReply.trigger_transport` and `response_transport` round-trip on each reply report. The framework's `response_topic` field is serialised under the existing `reply_topic` JSON key (mapping defined in §1.2.1).
- A new optional `scenario.stage` block carries the bridge class name, the touched transports, and the per-transport wire ids (hash-redacted before serialisation).
- A new optional `run.transports` array lists every transport seen across the run.
- The framework's `StageReplyState` is mapped to the existing `state` enum (`replied`, `reply_failed`, `armed_no_match`, `armed_matcher_mismatched`). **No enum extension.** Mapping defined in §1.7.
- The framework adds a public `kind: Literal["single_harness", "stage"]` discriminator on every result type. The reporter dispatches on `kind`, not on duck-typed `hasattr`. See §2.1 and D-5.

**`schema_version` bumps from `"1"` to `"1.1"`.** Per PRD-007 §US-3, additive fields warrant a minor bump. Consumers gating on `schema_version.startswith("1")` continue to work. Strict-validator consumers pinned to `test-report-v1.0` update to `test-report-v1.1` (the new schema document); the diff is purely additive.

The reporter package version bumps so consumers see the change in pipeline metadata.

The HTML renderer gains four small surfaces: per-handle transport badge, per-reply trigger / response transport badges, scope-level Stage breadcrumb, and an opt-in by-transport handle grouping toggle. Existing single-`Harness` rendering paths are byte-identical to before — verified by snapshot test.

The PRD ships as a phased delivery plan (schema + reporter + Chronicle migration, then renderer, then docs). **Chronicle's ingest migration is a Phase 1 dependency**, not a follow-up: today's normaliser does `run["transport"]` (no `.get()`) and `runs.transport` is `NOT NULL`, so the first Stage report would crash ingest. Chronicle and reporter PRs land together.

---

## Problem Statement

### Current State

`docs/schemas/test-report-v1.json` describes the JSON the reporter emits today. The relevant shape:

- **`run` carries `transport: string`** (required in `test-report-v1.0`) — set from the single `Harness` the run was wired against. A run that uses two harnesses (Stage's situation) cannot fit; the reporter would today either pick one (lying), pick none (loss), or crash.
- **Each `scenario` carries `correlation_id: string`** — single value, assumes one logical id per scope. Stage's per-scope logical id maps to per-transport wire ids; neither shape fits. (The current schema declares `correlation_id` as `type: "string"` non-nullable; Stage scenarios with `NoCorrelationPolicy` would inherit `None`. The schema must relax to `["string", "null"]` — see §1.4.1.)
- **Each `handle` is `{topic, outcome, latency_ms, ...}` with no `transport` field** — diagnostics in the HTML cannot show which side of a multi-transport scenario a failing handle belongs to.
- **Each `reply_report` is `{trigger_topic, reply_topic, ...}`** — no separate `trigger_transport`/`response_transport` fields. The cross-transport-coordination property the test author asserts on (trigger fires on Kafka, response emits on NATS) is invisible. (The framework's `_StageReply` exposes `response_topic`; the schema's existing JSON key is `reply_topic`. Mapping is in §1.2.1.)
- **Reply `state` enum is `{armed_no_match, armed_matcher_mismatched, replied, reply_failed}`** — the Stage `StageReplyState` enum is mapped onto these four values at serialisation. No new enum values are introduced (avoiding the dual-vocabulary leak that v2 contemplated). Mapping in §1.7.

The HTML renderer renders these fields as flat lists with no transport awareness. A cross-transport scenario emitted by today's reporter, even if the schema were extended, would render as a single flat handle list with no badges, no breadcrumb, no per-transport grouping.

### User Pain Points

- **A test author writes a Stage round-trip and runs the reporter — and gets nothing back about the bridge.** The structured fields cannot represent a handle that resolved on transport B from a publish on transport A. The cross-transport reply lifecycle (one `_StageReply` spanning two transports, single-writer per ADR-0016) cannot be expressed in the current `reply_report` shape.

- **A failing handle's diagnostic does not name its transport in the report.** CI logs surfacing `repr(handle)` carry the transport name (per ADR-0027 §Security Considerations; the `Handle.__repr__` change in Group K), but the reporter's structured fields do not. Triage from the HTML report is harder than triage from raw stderr — an inversion of the report's purpose.

- **A reply that fired on a different transport from its trigger looks identical to a same-transport reply.** The cross-transport-coordination property the author wants to assert on is invisible. A consumer reading `result.replies[0]` from `results.json` cannot tell whether the reply's `trigger_topic` and `reply_topic` were on the same harness or on different ones.

- **The HTML drill-down tree groups scenarios by name only.** Within a Stage scenario, there is no per-transport grouping; no "this side dropped" indicator; no scope-level breadcrumb showing the bridge class or the registered transports. A 100-scope parallel-isolation test (Group J) renders as 100 flat scenario nodes with no visual cue that they share a Stage.

- **A Stage scenario whose response transport dropped surfaces as a generic FAIL.** The `Handle.transport` field carries the breadcrumb in code, but the report does not. The author has to read the test source to learn which side dropped.

### Business Impact

- **Stage adoption stalls without a usable report.** The PRD-011 motivating use case is bridge-boundary tests — the highest-value tests in any protocol-translation service. A blank report for those tests removes the value that drove the consumer to choose Choreo.

- **CI triage time regresses for Stage tests.** The reporter is the team's diagnostic-time multiplier; without Stage support, Stage tests bypass the reporter and drop the team back to terminal output.

- **Chronicle ingest skews the picture, and breaks outright once the schema lands.** PRD-009's Chronicle reporting server ingests `results.json` for longitudinal analytics. Today's normaliser at `packages/chronicle/src/chronicle/services/normalise.py:159` does `transport=run["transport"]` (no `.get()`), and the `runs.transport` column is declared `TEXT NOT NULL` (PRD-009 §schema, line 380). Once the reporter starts emitting `run.transport: null` for Stage runs, ingest raises `KeyError` (Pydantic level 1) or a constraint violation (DB level), losing the run from archival. Chronicle's continuous aggregates (`topic_latency_hourly`, `topic_latency_daily`) `GROUP BY transport`; Stage handles would silently bucket as the run-level value rather than per-handle Stage values. The Chronicle migration is therefore a Phase 1 blocker, not OQ-2 follow-up.

---

## Goals and Objectives

### Primary Goals

1. **Faithful capture of Stage scenarios in `results.json`.** Handles carry their transport. Replies carry both transport sides. The scope's logical id, per-transport wire ids, and bridge class name are surfaced. The schema is precise enough that a downstream consumer of `results.json` can reconstruct the Stage scenario's structural shape.

2. **No breaking change to single-`Harness` consumers.** Single-`Harness` scenario reports look exactly as they do today. Every Stage-specific field is optional in the schema; consumers gating on `schema_version == "1"` continue to work. A snapshot test pins this.

3. **HTML renderer surfaces the cross-transport shape.** Per-handle transport badge, per-reply trigger / response transport badges, scope-level Stage breadcrumb, opt-in by-transport handle grouping toggle. Existing single-`Harness` rendering paths are byte-identical to before.

4. **Schema minor bump (`"1"` → `"1.1"`); reporter package version bumps in lockstep.** PRD-007 §US-3 policy: additive fields are minor bumps. Consumers gating on `schema_version.startswith("1")` continue to work. Strict-validator consumers update their pinned schema document to `test-report-v1.1`; the diff is purely additive (no required fields removed, no enum values removed).

5. **Tighter redaction posture at the report boundary than the framework's in-memory posture.** ADR-0027's `_redact()` (head=8, tail=4, length annotation) was designed for in-process error messages with bounded lifetime. Reports persist to disk and to Chronicle's archive (months of retention), so the report boundary uses **hash-based redaction** via a new `choreo.redaction.redact_wire_id()` API: SHA-256 truncated to 16 hex chars (~64 bits, sufficient to disambiguate within a run, insufficient to reverse the source). Single source of truth — the reporter imports this helper, does not re-implement it. See §1.5 and §2.4.

6. **Stage-side `handle.correlation_id` is also redacted.** v2 left handle correlation_ids un-redacted while redacting the `scenario.stage.correlation_ids` map, making the latter theatrical. v3 redacts both for Stage handles. Single-`Harness` handle correlation_ids continue un-redacted (the contract is unchanged for that path).

### Success Metrics

- **Schema fidelity.** The Group I cross-transport reply tests ([packages/core/tests/integration/test_stage_replies.py](../../packages/core/tests/integration/test_stage_replies.py)) and Group J parallel-isolation test ([test_stage_parallel_isolation.py](../../packages/core/tests/integration/test_stage_parallel_isolation.py)) run via the reporter; every transport name and every mapped `_StageReply.state` value is present in the emitted `results.json`. Verified by a reporter-level e2e test (Testing Strategy §End-to-end). "Group I/J/K" labels refer to the integration-test plan at [docs/test-plans/0027-stage-integration-tests.md](../test-plans/0027-stage-integration-tests.md).

- **Renderer fidelity.** A Stage scenario rendered through the HTML report passes the existing `data-*` contract (PRD-007 §4 *index.html structure*) plus the **stable-tier** contract: `data-handle-transport`, `data-reply-trigger-transport`, `data-reply-response-transport`, `data-stage-transports`, `data-grouping-mode`. Plus an **advisory tier** documented as may-change-without-bump: `data-stage-bridge-class`, `data-failing-reply-response-transport`. Tier split is in §3.6.

- **Backward compatibility.** A snapshot test asserts that a known single-`Harness` test run's `results.json` from the post-PRD-012 reporter is byte-identical to a baseline emitted by the **pre-PRD-012 reporter binary** (committed as `tests/snapshot/baseline_pre_prd012.json` to avoid the self-validation trap; see Testing Strategy §Snapshot tests). Same for the HTML output.

- **Reporter version bump.** Semver minor: e.g. `0.X` → `0.X+1`. Consumer pipelines see the new version in `run.reporter_version`.

- **Performance.** A Stage scenario at PRD-007's reference workload (10k handles, 1k scenarios, 200 transports across runs) renders in `<1 second` time-to-interactive on PRD-007's reference machine, with `<200ms` perceived response on by-transport toggle. JSON serialisation completes in `<500ms`. Memory stays under PRD-007's 5 MB report-directory cap. See §Non-Functional Requirements.

### Non-Goals

- **Schema major-version bump (`test-report-v2`).** This PRD is additive at minor-bump granularity (`v1.0` → `v1.1`); if a future need requires a structural change (e.g. dropping `correlation_id` in favour of `correlation_ids: array`), that warrants `test-report-v2` and a separate PRD.

- **Re-rendering the entire HTML report.** Existing single-`Harness` rendering paths stay; Stage adds per-transport surface within the same scenario node.

- **Forward compatibility with reporters older than PRD-012.** Reporters older than PRD-012 reading Stage results would crash on the unknown `stage` block when validating against `test-report-v1.0` (their pinned schema). Consumers who want to read PRD-012 reports update their reporter or pin `test-report-v1.1`. We do not back-port PRD-012 to older reporter versions.

- **Chronicle dashboard updates.** Chronicle's **ingest path** is a Phase 1 dependency (Problem Statement §Business Impact). Chronicle's **dashboard surface** (per-transport breakdowns, bridge-failure panels) is a separate piece of work scheduled after PRD-012 ships, owned by Chronicle package owners.

- **Stage-specific charts in the HTML report.** PRD-007 v1 explicitly excluded charts; PRD-012 inherits that exclusion. Per-transport fan-out diagrams, cross-transport-reply Sankey plots, and bridge-translation visualisations are deferred.

- **Run-over-run comparison of Stage reports.** Inherits PRD-007's "one run per report" constraint.

- **Live / streaming updates of Stage state during a run.** Inherits PRD-007's "report produced once at session teardown" constraint.

- **Renaming `replied` to `fired` in the schema.** Both terms denoted the same outcome in v2's extended-enum proposal. v3 maps `StageReplyState.FIRED` → `"replied"` directly (no enum extension), so the question dissolves.

---

## User Stories

### Primary User Stories

**US-1. As a** test author whose Stage scenario failed,
**I want to** open `test-report/index.html` and see which transport each failing handle belongs to,
**So that** I can localise the failure to a side of the bridge without re-reading the test source.

**Acceptance Criteria:**

- [ ] Every Stage handle element renders a transport badge: `<span data-handle-transport="nats">nats</span>` (or the relevant transport name).
- [ ] Single-`Harness` handle elements have no `data-handle-transport` attribute. Selector `[data-handle-transport]` matches only Stage handles in the handle list (the Stage breadcrumb's transport pills use `data-stage-transport` — see §3.3).
- [ ] When a Stage scenario has handles on both `nats` and `kafka`, the badge colour or icon distinguishes them visually (using a stable per-transport assignment, not random hue).
- [ ] The badge carries `aria-label="transport: <name>"`. Verified by an axe-core / Playwright assertion in the renderer integration suite (Testing Strategy §Renderer integration).

---

**US-2. As a** test author writing a cross-transport reply chain,
**I want to** see in the report which transport the trigger fired on and which transport the reply emitted on,
**So that** I can verify the bridge translation worked even when the test passed (forward-debug instinct).

**Acceptance Criteria:**

- [ ] Every reply report row renders two badges: trigger and response. Cross-transport replies show distinct badges (e.g. "trigger: orders.new [kafka] → reply: orders.processed [nats]"); same-transport replies show the same badge twice.
- [ ] `data-reply-trigger-transport` and `data-reply-response-transport` attributes carry the transport names.
- [ ] When the reply state is `replied`, the row carries `data-reply-publish-failed="false"` and both badges render at full opacity (the absence of any opacity-modifier class is the asserted observable). When the reply state is `reply_failed` (mapped from `StageReplyState.FIRED_BUILDER_ERROR`), the row carries `data-reply-publish-failed="true"` and the response-transport badge carries the class `reply-transport-badge--failed` (CSS rule reduces opacity to 0.4, but the asserted contract is the class name and the `data-*` flag, not the visual property).

---

**US-3. As a** consumer of `results.json` building a downstream tool (Chronicle, internal dashboard),
**I want to** distinguish Stage scenarios from single-`Harness` scenarios programmatically,
**So that** I can surface per-transport metrics without re-parsing scenario names or guessing.

**Acceptance Criteria:**

- [ ] Every Stage scenario in the JSON has a `stage` object: `{bridge_class, transports, correlation_ids}`. Single-`Harness` scenarios have no `stage` key.
- [ ] Every result emitted by the framework also carries an explicit `kind: "single_harness" | "stage"` discriminator field, mirroring the JSON `scenario.stage` presence one level up. Consumers prefer the JSON `scenario.stage` presence as the canonical signal; `kind` is the in-process equivalent for the reporter dispatch (D-5).
- [ ] `scenario.stage.transports` is the canonical list of transport names for the scope, sorted alphabetically (deterministic, snapshot-test friendly).
- [ ] `run.transports` lists every transport name across the run (union across all scenarios), sorted alphabetically. For a run with only single-`Harness` scenarios, `run.transports` is absent. For a run with any Stage scenario, `run.transports` is present and `run.transport` is `null`.
- [ ] The schema documents the `stage` block as optional and the `scenario.stage` presence as the canonical signal "this scenario was a Stage scenario".

---

**US-4. As a** test author with a 100-scope parallel-isolation test,
**I want to** see the report grouped by transport instead of by scenario name,
**So that** I can spot per-transport patterns (e.g. all NATS-side handles are slow) without scrolling through 100 scenario nodes.

**Acceptance Criteria:**

- [ ] A toggle button in the run header carries `data-grouping-mode="by-scenario"` initially. Activating the button flips the value to `"by-transport"` and re-applies the layout. The asserted observable is the attribute value flip (no DOM mutation expected; see §3.4).
- [ ] In "by transport" mode, all handles re-flow under group sections keyed by their `transport` value (CSS-only re-grouping via `display: contents` and `order:`; no DOM reparenting). Single-`Harness` handles (no `transport`) sit under an "Untagged" group with `data-grouping-section="transport:untagged"`.
- [ ] The toggle state is mirrored into `location.hash` (e.g. `#grouping=by-transport`) so a URL can reproduce a grouped view.
- [ ] Switching modes does not lose filter state (status pills, search, marker filter, duration filter all persist). Verified by a Playwright integration test in the renderer suite.

---

**US-5. As a** CI reviewer triaging a Stage scenario whose response transport dropped,
**I want to** see at a glance which side of the bridge failed,
**So that** I can hand the issue to the right team (NATS-side ops, Kafka-side ops) without reading code.

**Acceptance Criteria:**

- [ ] When a Stage scenario contains a handle resolved as `TIMEOUT` or `FAIL`, the scenario header shows the failing transport name as a sub-badge: "FAIL [nats]".
- [ ] When a Stage scenario contains a `_StageReply` resolved as `FIRED_BUILDER_ERROR`, the scenario header shows the failing response transport: "REPLY FAILED → [nats]".
- [ ] The per-handle / per-reply detail rows preserve the badges so the link from header summary to detail is direct.

---

**US-6. As a** tooling engineer running pre-PRD-012 and post-PRD-012 reporter versions side-by-side during a migration,
**I want** the JSON for a single-`Harness` test run to be byte-identical between the two versions (modulo `reporter_version`),
**So that** I can roll forward without regenerating golden snapshots.

**Acceptance Criteria:**

- [ ] A snapshot test in `tests/reporter/snapshot/` ingests a known single-`Harness` test run via the post-PRD-012 reporter, normalises masked fields (D-7), and compares to `tests/reporter/snapshot/baseline_pre_prd012.json` (committed; generated by the pre-PRD-012 reporter binary, not by the post-PRD-012 reporter on first run). The diff is empty modulo the `schema_version` field (which legitimately moves from `"1"` to `"1.1"`).
- [ ] The snapshot test runs in CI and fails the build if any byte (other than the masked set + `schema_version`) differs. Regeneration procedure documented in `tests/reporter/snapshot/README.md`: invoking with `CHOREO_REPORTER_UPDATE_SNAPSHOTS=1` regenerates the baseline using the currently-installed reporter binary; the operator pins the pre-PRD-012 reporter version before running.

---

**US-7. As a** test author running a Stage workload at PRD-007's reference scale,
**I want** the report's render time to stay under PRD-007's 1-second budget,
**So that** Stage scenarios are first-class in the report performance contract.

**Acceptance Criteria:**

- [ ] A reporter-level performance benchmark, located in `tests/reporter/performance/`, runs two workloads:
  - **Reference workload** (matches PRD-007 §Memory): 100 scopes × 5 handles × 2 replies. Time-to-interactive `<1s`, JSON write `<500ms`.
  - **Cap-saturated workload**: a synthetic run sized to within 200 KB of PRD-007's 10 MB JSON cap (10k handles, 1k scenarios, 200 transports across runs). Time-to-interactive `<3s` (acknowledged stretch budget); JSON write `<2s`. The benchmark's purpose is regression detection at the cap boundary, not a hard SLA.
- [ ] **"Time-to-interactive"** is defined here as Playwright's `page.waitForSelector('button[data-grouping-mode]', { state: 'attached' })` resolving — i.e. the toggle button is rendered and event-handler-attached. This is the smallest user-visible action surface; not Largest Contentful Paint, not DOMContentLoaded.
- [ ] Memory usage for the reference workload stays under PRD-007's worst-case bound (5 MB per report directory).
- [ ] Per-transport grouping (US-4) toggle response is `<200ms` on the reference workload, measured as the time between the click event and the next `data-grouping-mode` attribute mutation observed via MutationObserver.

---

## Functional Requirements

### 1. Schema additions: `test-report-v1.0.json` → `test-report-v1.1.json`

The schema is amended; `schema_version` bumps from `"1"` to `"1.1"`. Every addition (other than the bump) is an **optional** property. Existing `additionalProperties: false` constraints are retained on each definition; the new properties are explicitly added to each definition's `properties` block. The schema document itself is republished as `test-report-v1.1.json`; `test-report-v1.0.json` remains in tree as the prior version (for consumers pinned to it).

Concrete diff of the schema is in [Appendix A](#appendix-a--schema-diff-with-before--after-examples). Field-by-field:

#### 1.1 `handle.transport`

```json
"transport": {
  "description": "Transport name for handles produced by Stage scenarios; absent for single-Harness handles. Mirrors Handle.transport in code, which is exposed as a read-only @property; the underlying _transport dataclass field is mutable by leading-underscore convention only.",
  "type": ["string", "null"]
}
```

Required: optional. Single-`Harness` reports MUST OMIT the key entirely (not emit `null`) to keep the JSON byte-identical to v1.0 emission. Consumers MUST tolerate the omission.

#### 1.2 `reply_report.trigger_transport` and `reply_report.response_transport`

```json
"trigger_transport":  { "type": ["string", "null"] },
"response_transport": { "type": ["string", "null"] }
```

Required: optional. For Stage replies, both fields name the registered transports (set on `_StageReply.trigger_transport` / `response_transport` in the framework). For single-`Harness` replies, both keys are omitted.

#### 1.2.1 Topic field mapping (`response_topic` → `reply_topic`)

The framework's `StageReplyReport` exposes `response_topic`; the existing v1.0 schema's JSON key is `reply_topic`. The reporter serialises `response_topic` under the `reply_topic` JSON key for both single-`Harness` and Stage replies. Rationale: keeping the JSON key stable preserves backward compatibility for consumers who already query `reply_report.reply_topic`. The framework's internal name change (`reply_topic` was renamed `response_topic` for cross-transport clarity in ADR-0027) does not propagate to the JSON wire format.

#### 1.3 No `reply_report.state` enum extension

`StageReplyState` is mapped onto the existing four `state` enum values. **No new enum values are added.** Mapping in §1.7. Rationale: extending an enum is technically additive but creates a permanent dual-vocabulary in `results.json` (consumers must handle both `replied` and `fired` for the same outcome), which v3 closes.

#### 1.4 `scenario.stage` block

```json
"stage": {
  "type": ["object", "null"],
  "additionalProperties": false,
  "required": ["bridge_class", "transports", "correlation_ids"],
  "properties": {
    "bridge_class": {
      "description": "Class name of the CorrelationBridge in effect for the Stage. Advisory-tier field per §3.6: useful for debugging, NOT a stable contract; consumers naming a bridge class for clarity may rename it across releases. Pattern restricts to safe identifier characters.",
      "type": "string",
      "pattern": "^[A-Za-z][A-Za-z0-9_]{0,127}$"
    },
    "transports": {
      "description": "Transport names registered on the Stage, sorted alphabetically (deterministic; snapshot-test friendly).",
      "type": "array",
      "items": {
        "type": "string",
        "pattern": "^[a-zA-Z0-9_-]{1,64}$"
      }
    },
    "correlation_ids": {
      "description": "Map of transport name to the per-transport wire id minted from the scope's logical id. Values are HASH-REDACTED via choreo.redaction.redact_wire_id (SHA-256 truncated to 16 hex chars, prefixed with the redaction-version tag, see §1.5). Stage handles' correlation_id field is also hash-redacted (single redaction posture). Single-Harness handles' correlation_id remains un-redacted.",
      "type": "object",
      "additionalProperties": { "type": "string" }
    }
  }
}
```

Required: optional. Single-`Harness` scenarios MUST NOT have a `stage` key (presence is the canonical "this scenario was a Stage scenario" signal — referenced in US-3 acceptance criteria and §2.1's reporter dispatch logic).

#### 1.4.1 `scenario.correlation_id` schema relaxation

The existing v1.0 schema declares `scenario.correlation_id` as `{"type": "string"}` (non-nullable). v1.1 relaxes it to `{"type": ["string", "null"]}`. Rationale: Stage scenarios using `NoCorrelationPolicy` (the default) have no logical id; emitting `null` is more honest than synthesising a placeholder. This also matches reality for the existing single-`Harness` path — `packages/core/test-report/results.json` already contains `"correlation_id": null` in places, which would already fail strict validation against v1.0. v1.1 brings the schema into line with what the reporter has been emitting.

For Stage scenarios with a `DictFieldPolicy` (or any non-`None` correlation policy), the reporter populates `scenario.correlation_id` from the scope's logical id (read from `_StageScenarioScope._logical_id` — see §2.5 for the access path; this is the framework-internal source of truth). The logical id is a `secrets.token_hex` output by default and is **not** redacted in the report (it is the consumer-meaningful pivot for cross-run linkage). Bridges deriving `fresh()` from PII (request id, customer id) leak that PII via this field — consumer contract is to use a non-PII `fresh()`. Documented in ADR-0027 §Security Considerations.

#### 1.5 `run.transport` becomes optional alongside new `run.transports`

The existing `run.transport` field is relaxed from required to optional. A new `run.transports` array is added.

```json
"transport":  { "type": ["string", "null"] },
"transports": {
  "type": "array",
  "items": {
    "type": "string",
    "pattern": "^[a-zA-Z0-9_-]{1,64}$"
  }
}
```

Required: `transport` becomes optional (was required in v1.0); `transports` is also optional. At least one of the two MUST be present in every run report. The constraint uses `anyOf` (not `oneOf`) so reports MAY emit both fields (mixed-mode runs):

```json
"anyOf": [
  { "required": ["transport"] },
  { "required": ["transports"] }
]
```

Single-`Harness` runs emit `transport` only; Stage-only runs emit `transports` only; mixed runs emit both, with `transport` set to `null` and `transports` carrying the sorted union. See §1.6 for precedence.

##### 1.5.1 Wire-id redaction at the report boundary

Wire ids are sensitive: an adversary with read access to archived `results.json` could correlate runs via partial-id linkage if redaction is reversible. The framework's in-process `_redact()` (head=8, tail=4, length annotation) was designed for short-lived error messages and is not adversary-resistant against an archive-grep threat model.

The report boundary uses **`choreo.redaction.redact_wire_id(s)`** — a new public helper exported from `choreo.redaction` (single source of truth; the reporter imports, does not re-implement):

```python
import hashlib

REDACTION_VERSION = "v1"  # bumps if the algorithm changes; surfaces in run.redactions

def redact_wire_id(s: str) -> str:
    """Hash-based redaction for the report boundary. Not reversible.

    Returns a string of the form 'sha256:<16 hex chars>' so consumers
    can grep the prefix to identify redacted values, and the version
    tag is implicit in the schema's run.redactions field.
    """
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"
```

The reporter applies `redact_wire_id` to:

- `scenario.stage.correlation_ids[transport]` for every Stage scenario.
- `handle.correlation_id` for every Stage handle (per Goal 6).
- Any wire id appearing inside `handle.diagnostic`, `scenario.summary_text`, or reply diagnostic strings (the reporter walks these for substrings matching the per-scope wire ids and replaces them in-place; canary-tested per §Testing Strategy).

The reporter does NOT redact:

- `handle.correlation_id` for single-`Harness` handles (un-redacted, contract preserved from v1.0).
- `scenario.correlation_id` (this is the **logical** id, not a wire id; consumer-meaningful for run-linkage).

`run.redactions` (already a required field in v1.0) is extended to record `redaction_version: "v1"` so consumers know which algorithm produced the values.

#### 1.6 `run.transport` semantics in mixed-mode runs

Decision (D-1a, see Decisions Already Made): in a run that contains both single-`Harness` and Stage scenarios, the reporter sets `run.transport` to `null` and populates `run.transports` with the union of every transport name encountered. The reporter does NOT use `run.transport` as a "most-frequent hint"; the precedence rule is simply `transports` overrides `transport` when both are present.

Note on the vocabulary split: in v1.0, `run.transport` carries a Python class name (e.g. `"MockTransport"`, `"NatsTransport"`). In v1.1, `run.transports` carries Stage-registered transport names (the dict keys consumers chose, e.g. `"nats"`, `"kafka"`). These are different concepts (one is an implementation type, the other is a consumer-chosen logical name). Consumers gating on `run.transport == "MockTransport"` for routing logic continue to work for single-`Harness` runs but will not see Stage runs (where `run.transport` is `null`); migration path is to also check `run.transports` membership.

#### 1.7 `StageReplyState` → `state` mapping

The reporter normalises `StageReplyState` enum values to the schema's existing four enum strings.

| Source | Schema string |
|---|---|
| `StageReplyState.FIRED` | `"replied"` |
| `StageReplyState.FIRED_BUILDER_ERROR` | `"reply_failed"` |
| `StageReplyState.ARMED_NO_MATCH` | `"armed_no_match"` |
| `StageReplyState.ARMED_MATCHER_MISMATCHED` | `"armed_matcher_mismatched"` |
| `StageReplyState.ARMED` (runtime-only) | n/a — never serialised; `_resolve_pending_reply` flips it before the freeze step |
| `ReplyReportState.REPLIED` | `"replied"` |
| `ReplyReportState.REPLY_FAILED` | `"reply_failed"` |
| `ReplyReportState.ARMED_NO_MATCH` | `"armed_no_match"` |
| `ReplyReportState.ARMED_MATCHER_MISMATCHED` | `"armed_matcher_mismatched"` |

`ARMED` is documented as runtime-only because a `_StageReply` with `state == ARMED` has not yet resolved; the freeze step in `_StageScenarioScope.__aexit__` flips it to one of the terminal states. If an implementer encounters `ARMED` at serialisation time, that is a bug in the framework's freeze path (not the reporter); the reporter raises `AssertionError` so the bug is caught immediately rather than silently emitting a non-schema value.

The two-side derivation (Stage vs single-`Harness`) collapses to the same four schema values, so consumers comparing `reply.state == "replied"` work regardless of which path produced the reply. The internal distinction (which framework-side enum produced the value) is preserved in the in-process `_StageReply.state` / `ReplyReport.state` for in-tree code paths; it does not surface in JSON.

### 2. Reporter changes (`choreo-reporter`)

The reporter package is `packages/choreo-reporter/` (created as part of Phase 1 if not yet present; the in-tree session-end emitter producing `packages/core/test-report/results.json` is the precedent). The package depends on `choreo` with a loose lower-bound version constraint; the framework changes specified below are required for the reporter to import and dispatch correctly.

#### 2.1 Detect Stage results via the explicit `kind` discriminator

The framework adds a public `kind` field to both `ScenarioResult` (single-`Harness`) and `StageScenarioResult` (Stage; renamed from `_StageScenarioResult` and exposed publicly):

```python
# In packages/core/src/choreo/scenario.py
@dataclass
class ScenarioResult:
    kind: Literal["single_harness"] = field(default="single_harness", init=False)
    # ... existing fields ...

# In packages/core/src/choreo/stage.py; also re-exported as choreo.StageScenarioResult
@dataclass
class StageScenarioResult:
    kind: Literal["stage"] = field(default="stage", init=False)
    # ... existing fields, renamed from _StageScenarioResult ...
```

The reporter dispatches on `kind`:

```python
def _emit_scenario(result: ScenarioResult | StageScenarioResult) -> dict:
    if result.kind == "stage":
        return _emit_stage_scenario(result)
    return _emit_single_harness_scenario(result)
```

Rationale (replaces v2's `hasattr` duck-typing):

- Duck-typing on `hasattr(result, "replies")` is a false positive — `ScenarioResult` also carries `replies`. The collapse-to-`hasattr(by_transport)` makes detection one rename away from silently breaking.
- A hostile or malformed result object can spoof the duck-type and trigger reporter exceptions mid-emit, causing truncated `results.json` (denial-of-evidence in shared CI).
- An explicit `kind` field is unambiguous, isinstance-checkable, and survives refactors.

The reporter wraps the entire Stage-emission branch in `try/except` and, on unexpected failure, falls back to single-`Harness` serialisation while logging a structured `WARNING` event `stage_emission_failed` with the exception type. This protects the report's evidentiary value when a hostile or malformed result reaches the reporter; the structured warning surfaces the failure in CI logs.

#### 2.2 Reply report normalisation

The reporter normalises every reply's `state` enum value via the §1.7 mapping table. Implementation: a single `_serialise_reply_state(state)` helper that takes either `StageReplyState` or `ReplyReportState` and returns one of the four schema strings. Unit-tested for every case in the table, plus a negative case: `_serialise_reply_state(None)` and `_serialise_reply_state("nonsense")` raise `ValueError` (the reporter does not silently coerce unknown values; the freeze-path bug surfaces immediately).

The reporter also serialises the framework's `StageReplyReport.response_topic` under the JSON key `reply_topic` (per §1.2.1).

#### 2.3 Per-handle transport extraction

For every handle, the reporter reads `handle.transport` (a property on `Handle`; returns `None` for single-`Harness`, a string for Stage). The serialised JSON includes `transport: <string>` when non-`None`; the field is OMITTED (not emitted as `null`) when `None`. This keeps single-`Harness` JSON byte-identical to pre-PRD-012 output (modulo the `schema_version` bump).

#### 2.4 Wire-id redaction (single source)

The reporter imports redaction from the framework — does not re-implement:

```python
from choreo.redaction import redact_wire_id, REDACTION_VERSION
```

`redact_wire_id` is defined in §1.5.1. The reporter applies it to every site enumerated in §1.5.1's site list. A unit test in `tests/unit/test_redaction_sites.py` injects a synthetic payload containing the marker `SECRET-CANARY-XYZ` into each enumerated site (handle diagnostic, summary text, reply diagnostic, correlation_ids map, Stage handle correlation_id) and asserts the marker does NOT appear in the emitted JSON.

The framework retains its in-process `choreo.stage._redact()` (head=8, tail=4) for error-message construction, but the report boundary uses `redact_wire_id` exclusively. The two are deliberately decoupled — error messages have a bounded process lifetime and tolerate looser redaction; archived reports do not.

`run.redactions` records `redaction_version: "v1"` so consumers know which algorithm produced the values; future tightening of the algorithm bumps the version string.

#### 2.5 Run-level transport(s) emission

At session-finish, the reporter walks every test's scenarios and computes:

- The set of transport names from `scenario.stage.transports` for every Stage scenario.
- The single-`Harness` transport name from `harness.transport_name` (a new public read-only property on `Harness` returning `type(self._transport).__name__`; replaces the v2-proposed reach into the private `_transport` attribute).

If only single-`Harness` scenarios were observed, `run.transport` is set as today and `run.transports` is omitted.

If any Stage scenario was observed, `run.transport` is set to `null` and `run.transports` is set to the sorted (alphabetical) union of every transport name encountered across both modes.

#### 2.6 Logical-id surfacing for Stage scenarios

For Stage scenarios, the reporter populates `scenario.correlation_id` from the scope's logical id. The framework adds a public `StageScenarioResult.correlation_id` field (the value captured from the scope's `_logical_id` at scope teardown — the field is sealed at result-construction time). The reporter reads `result.correlation_id` directly; it does not reach into private scope attributes.

#### 2.7 Reporter version bump

`choreo_reporter.__version__` bumps semver-minor (e.g. `0.X` → `0.X+1`). The `run.reporter_version` field reflects this.

#### 2.8 Framework changes summary

The reporter changes above depend on the following additive changes to `choreo`:

- `Handle.transport` — already shipped (Group K F7 work). No change.
- `StageScenarioResult` — rename from `_StageScenarioResult`, expose publicly via `choreo.StageScenarioResult`. Existing tests using the underscored name continue to import successfully via a backward-compatibility alias for two minor versions.
- `kind: Literal["single_harness"] | Literal["stage"]` — new field on both result types.
- `StageScenarioResult.correlation_id` — new field carrying the scope's logical id.
- `Harness.transport_name` — new property returning `type(self._transport).__name__`.
- `choreo.redaction.redact_wire_id`, `choreo.redaction.REDACTION_VERSION` — new public module.

These changes are additive on the framework side too; existing consumers are unaffected.

### 3. HTML renderer additions

All renderer changes live in `packages/choreo-reporter/src/choreo_reporter/_renderer.py` and are applied at session-finish from `results.json`. No runtime fetches — the JSON is inlined per PRD-007 §7.

**Mandatory escaping.** Every `data-*` attribute and every text node carrying a runtime-derived value (transport name, bridge class name, topic, wire id, correlation id) is escaped via `html.escape(value, quote=True)` before insertion. Defence in depth against a malicious `results.json` ingested through Chronicle and re-rendered: schema-level regex validation (§1.4 transport names, §1.4 bridge class) is the first line; `html.escape` is the second. A renderer unit test fixtures a Stage report with `transport: "<script>alert(1)</script>"` and asserts the rendered HTML contains the escaped form, no script execution surface.

#### 3.1 Per-handle transport badge

Each handle row in the scenario tree renders a small transport badge when `handle.transport` is non-null:

```html
<span class="handle-transport-badge"
      data-handle-transport="nats"
      aria-label="transport: nats">nats</span>
```

Badge styling: a small pill with a per-transport stable colour assignment (computed by hashing the transport name to a colour palette of 8 — collision-tolerable; fallback to neutral grey). The badge's text content is the transport name verbatim; the colour is decorative. The badge is omitted when `handle.transport` is null or absent.

#### 3.2 Per-reply transport badges

Each reply report row renders trigger and response transport badges:

```html
<div class="reply-row"
     data-reply-state="replied"
     data-reply-publish-failed="false">
  trigger:
  <span class="topic">orders.new</span>
  <span class="reply-transport-badge"
        data-reply-trigger-transport="kafka">kafka</span>
  →
  <span class="topic">orders.processed</span>
  <span class="reply-transport-badge"
        data-reply-response-transport="nats">nats</span>
  state: <span class="reply-state">replied</span>
</div>
```

For replies that failed to publish (`state == "reply_failed"`, mapped from `StageReplyState.FIRED_BUILDER_ERROR`), the row carries `data-reply-publish-failed="true"` and the response-transport badge carries the additional class `reply-transport-badge--failed` (CSS reduces opacity to 0.4; the asserted contract is the class name, not the visual property — see US-2 AC).

The renderer omits the badges when both `trigger_transport` and `response_transport` are null/absent (single-`Harness` reply).

#### 3.3 Scope-level Stage breadcrumb

A Stage scenario's header carries an additional breadcrumb line:

```html
<div class="stage-breadcrumb"
     data-stage-bridge-class="MappedBridge"
     data-stage-transports="nats kafka">
  <span class="label">Stage scenario</span> ·
  bridge: <span class="stage-bridge-class">MappedBridge</span> ·
  transports:
  <span class="stage-transport-badge" data-stage-transport="nats">nats</span>
  <span class="stage-transport-badge" data-stage-transport="kafka">kafka</span> ·
  correlation_ids:
  <span class="wire-id" data-stage-transport="nats">sha256:3f2a91b8c4d50e1f</span>
  <span class="wire-id" data-stage-transport="kafka">sha256:7e8b50c1ad4912ff</span>
</div>
```

`data-stage-transports` is a **space-separated** token list (CSS `[~=]` selector compatible: `[data-stage-transports~="nats"]`). The breadcrumb's transport pills carry `data-stage-transport` (singular, distinct from handle-row badges' `data-handle-transport`); this avoids polluting the US-1 selector contract that "[data-handle-transport] matches only Stage handles".

The breadcrumb renders only when `scenario.stage` is present. Wire ids displayed in the breadcrumb are the redacted forms from the JSON (§1.5.1).

#### 3.4 By-transport handle grouping toggle (US-4)

A toggle button in the run header switches the test list between "by scenario" (default) and "by transport". The toggle button carries `data-grouping-mode="by-scenario"` or `data-grouping-mode="by-transport"` reflecting the active mode.

Implementation: **CSS-only re-grouping**, no DOM mutation. Each handle row's parent element carries a `--transport` CSS custom property derived from `data-handle-transport`. The handle list container has two layouts via CSS `:has()` and `[data-grouping-mode]` ancestor selectors: `by-scenario` (default; handles flow under their scenario containers) and `by-transport` (handles use `display: contents` + `order:` keyed off `--transport` to re-flow under transport-keyed group headings rendered as pseudo-elements). The toggle's effect on layout is a single attribute write on the run header; the browser handles re-layout. No `O(n)` JavaScript DOM operations, no node duplication.

Group sections in `by-transport` mode carry `data-grouping-section="transport:<name>"` (for `[data-handle-transport="nats"]`) or `data-grouping-section="transport:untagged"` (for handles without a transport).

Filter state (status, search, markers, duration) persists across mode switches because no DOM nodes are added or removed; filters operate on the same node set under either layout.

##### 3.4.1 Performance bound

The `<200ms` toggle response budget (§Non-Functional Performance) is met by the CSS-only design under any handle count. The cap-saturated benchmark (US-7) verifies this at 10k handles.

#### 3.5 Failing-side sub-badge in scenario header (US-5)

When a Stage scenario contains any handle resolved as `TIMEOUT` or `FAIL`, the scenario header surfaces a sub-badge:

```html
<span class="scenario-failing-side"
      data-failing-transports="nats">FAIL → [nats]</span>
```

`data-failing-transports` is a **space-separated** token list (CSS `[~=]` selector compatible). Single-failure case: one token; multi-transport failure: multiple tokens (e.g. `data-failing-transports="nats kafka"`).

When a `_StageReply` resolved as `FIRED_BUILDER_ERROR` (mapped to schema `state: "reply_failed"`), a separate sub-badge surfaces the response transport:

```html
<span class="scenario-failing-reply"
      data-failing-reply-response-transport="nats">REPLY FAILED → [nats]</span>
```

#### 3.6 Stable-tier vs advisory-tier `data-*` attributes

The renderer's `data-*` contract is split into two tiers. The split governs which selectors consumers can rely on for snapshot tests, CI selectors, and Chronicle dashboards.

**Stable tier** (snapshot-tested; changes require a PRD-012-equivalent process):

- `data-handle-transport` (on handle rows; matches only handle rows)
- `data-stage-transport` (on Stage breadcrumb pills)
- `data-stage-transports` (on Stage breadcrumb container; space-separated)
- `data-reply-trigger-transport`, `data-reply-response-transport` (on reply rows)
- `data-reply-publish-failed` (on reply rows; "true" / "false")
- `data-grouping-mode` (on the toggle button)
- `data-grouping-section` (on transport group headings in by-transport mode)
- `data-failing-transports` (on scenario header; space-separated)

**Advisory tier** (rendered for debugging convenience; may change without a schema bump):

- `data-stage-bridge-class` — class name in `__name__` form. Renames in consumer bridge code propagate here.
- `data-failing-reply-response-transport` — diagnostic shorthand; may evolve as the failing-reply UI matures.

Consumer reliance on advisory-tier attributes is at consumer risk; reliance on stable-tier attributes is supported. The reporter README reproduces this split.

### 4. Backward compatibility

#### 4.1 Pre-PRD-012 consumers reading PRD-012 reports

Strict-validator consumers pinned to `test-report-v1.0` will reject PRD-012 reports because (a) `schema_version` is now `"1.1"` and (b) `additionalProperties: false` rejects the new `stage` block. Consumers update to `test-report-v1.1.json`; the diff is purely additive.

Lenient consumers (no schema validation, just key access) continue to work — the new fields are ignorable additions, single-`Harness` scenario emission is byte-identical aside from `schema_version`.

The pre-PRD-012 HTML renderer is forward-compatible at the rendering layer: it ignores unknown JSON fields, so it renders Stage reports as flat handle lists with no transport badges. It does not crash.

#### 4.2 Post-PRD-012 reporters running pre-Stage scenarios

A pre-Stage scenario produces a `ScenarioResult` with `kind == "single_harness"`. The reporter's dispatch (§2.1) emits the existing fields only. The post-PRD-012 schema's `transport` field on `run` carries the same value as before; `transports` is absent. No diff visible to consumers other than the `schema_version` bump — verified by the snapshot test (US-6).

#### 4.3 Snapshot test (US-6)

Pinned in CI: a known single-`Harness` test run via the post-PRD-012 reporter is byte-identical to the **pre-PRD-012 baseline** committed at `tests/reporter/snapshot/baseline_pre_prd012.json`, modulo the masked field set (D-7) plus the `schema_version` field (which legitimately moves from `"1"` to `"1.1"`).

The baseline is generated using the pre-PRD-012 reporter binary and committed as a checked-in fixture. Regeneration procedure documented at `tests/reporter/snapshot/README.md`: invoking with `CHOREO_REPORTER_UPDATE_SNAPSHOTS=1` regenerates the baseline using the currently-installed reporter; the operator MUST first install the pre-PRD-012 reporter version. This makes the test self-validating only by deliberate operator action.

The HTML output undergoes the same byte-comparison after masking the version footer. CI fails on any byte diff outside the mask + `schema_version` set.

---

## Non-Functional Requirements

Inherits PRD-007 §Non-Functional Requirements; the Stage additions tighten the existing constraints rather than relaxing them.

### Performance

- **Time-to-interactive** for a Stage scenario (US-7): `<1 second` on PRD-007's reference machine for the reference workload (100 scopes × 5 handles × 2 replies). "Time-to-interactive" defined per US-7 AC: Playwright's toggle-button-attached signal.
- **Cap-saturated workload** (10k handles, 1k scenarios, 200 transports): `<3 second` time-to-interactive (acknowledged stretch budget; the benchmark exists for regression detection at the cap boundary).
- **JSON write time**: `<500ms` for the reference workload, `<2s` for the cap-saturated workload.
- **By-transport grouping toggle** (§3.4): `<200ms` perceived response time, measured as the interval between click event and the next `data-grouping-mode` mutation. CSS-only re-grouping makes this independent of handle count.
- **Wire-id redaction** runs at serialisation time (§1.5.1, §2.4), not at render time. The renderer reads pre-redacted strings from the JSON.

### Memory and file size

- A Stage scenario's contribution to the report directory size is bounded by the same per-payload caps PRD-007 specifies. The new `scenario.stage.correlation_ids` map is bounded by `(transport_count × redacted_wire_id_length)`; for a 2-transport scope this is ~50 bytes. Negligible per-scenario.
- **Aggregate file-size impact at PRD-007's reference scale.** The new fields add an estimated ~720 KB to a cap-saturated 10 MB report (per-handle `transport`, per-reply transport pair, per-Stage-scenario `stage` block, run-level `transports`). PRD-007's hard refuse threshold is bumped from 10 MB to **11 MB** (`run.truncated: true` semantics unchanged) to preserve PRD-007's worst-case headroom under the additive expansion. Reports between 10 MB and 11 MB carry an additional warning entry in `run.warnings`.
- The HTML renderer's `state` object grows by one extra string field (`groupingMode`); negligible. DOM node count grows by approximately 3× the handle count + 2× the reply count + 10 per Stage scenario; verified to stay within performance budgets at the cap-saturated workload via US-7 benchmark.

### Correctness of the JSON contract

- The schema's `additionalProperties: false` constraints stay in place. Every new field is explicitly listed in the schema; unknown fields are rejected at validation.
- The `anyOf` constraint on `run.transport` / `run.transports` enforces "at least one of the two MUST be present" — both-missing fails validation; both-present is valid (mixed-mode runs).
- The reply state enum is **unchanged** from v1.0: `replied`, `reply_failed`, `armed_no_match`, `armed_matcher_mismatched`. PRD-012 maps Stage-side enum values onto these four; no new enum values are added.
- Transport names and bridge class names use schema-level regex patterns (`^[a-zA-Z0-9_-]{1,64}$` and `^[A-Za-z][A-Za-z0-9_]{0,127}$` respectively) to constrain the character classes that can land in `data-*` attributes (defence-in-depth alongside the renderer's `html.escape`).
- Schema-level transport-name pattern is enforced both at the choreo-reporter level (Pydantic) and at the Chronicle ingest level (level-2 schema). Chronicle ingest must update its level-2 schema in the same release as the reporter.

### Security / data handling

- Wire ids are hash-redacted at the reporter boundary via `choreo.redaction.redact_wire_id` (§1.5.1). The framework's in-process `_redact()` (head=8, tail=4) for error messages is unchanged; the two are deliberately decoupled because their threat models differ (in-memory error message lifetime vs archived report retention).
- The redaction algorithm is versioned via `REDACTION_VERSION` and emitted in `run.redactions.redaction_version`. Consumers can detect algorithm changes via the version string; algorithm tightening bumps the version.
- Reply `builder_error` carries the exception class name only (already enforced by the framework; the reporter passes through verbatim).
- `summary_text` for a Stage scenario continues to be redacted by the framework (the `StageScenarioResult.assert_passed()` redaction shipped in Group K). The reporter additionally walks `summary_text` for any per-scope wire id substring and replaces it via `redact_wire_id` (closes the leakage path where the framework's redaction misses a payload-derived id).
- Transport names and bridge class names are validated against the schema regex (above) and HTML-escaped at the renderer (§3). A consumer naming a transport `<script>alert(1)</script>` sees the schema-level rejection at the choreo-reporter level (Pydantic validation) before HTML rendering is reached. Defence in depth: even if schema validation is bypassed, the renderer's `html.escape` neutralises the payload.
- Stage handles' `correlation_id` is hash-redacted at the report boundary (Goal 6 / §1.5.1). Single-`Harness` handles' `correlation_id` is unchanged. The asymmetry is documented in the schema description.

### Accessibility

- Transport badges (§3.1, §3.2) carry colour AND `aria-label` text; the colour is decorative. Screen readers announce "transport: nats" alongside the topic.
- Failing-side sub-badges (§3.5) carry icon + text + colour; not colour alone.
- The by-transport grouping toggle is keyboard-accessible (Tab / Enter / Space). Verified by axe-core assertions in the renderer integration suite.

### Browser compatibility

- Inherits PRD-007's evergreen-browser commitment.
- CSS-only re-grouping (§3.4) uses `display: contents`, `:has()`, and CSS custom properties. All three are baseline-2023 features in evergreen browsers (Chrome 105+, Firefox 121+, Safari 15.4+).
- The new `data-*` attributes are plain HTML5; no IE / legacy support concerns.

---

## Decisions Already Made

These resolve the open questions named in v1 plus those surfaced during v2 expansion and review cycle 2.

### D-1a. `run.transport` vs `run.transports` renderer precedence in mixed runs

**Resolution:** `transports` overrides `transport`. The reporter sets `run.transport` to `null` for any run containing at least one Stage scenario; `run.transports` carries the union of every transport name encountered (sorted alphabetically). The renderer reads `transports` if present, falls back to `transport` otherwise.

**Rejected alternative:** "most-frequent single-`Harness` transport name as a hint" — discarded because it masks heterogeneity and gives a false signal of homogeneity.

**Rationale:** `null` makes the "this is a multi-transport run" signal unambiguous; `transports` provides the data.

### D-1b. Schema validity for strict consumers (`schema_version` bump)

**Resolution:** `schema_version` bumps from `"1"` to `"1.1"`. The schema document `test-report-v1.json` is republished as `test-report-v1.1.json`; v1.0 stays in tree for consumers pinned to it. PRD-007 §US-3 policy: additive fields warrant a minor bump.

**Rejected alternatives:** (a) keep `schema_version` at `"1"` and silently mutate the schema (v2's approach — false additivity for strict validators); (b) keep `run.transport` required and emit `"<multiple>"` for Stage-only runs (loses fidelity); (c) major bump to `"2"` (overkill — no breaking changes to consumer access patterns).

**Rationale:** the v3 approach is the only one that is honest both to the schema-document evolution and to consumers using `schema_version.startswith("1")` for compatibility gating.

### D-2. Wire-id redaction algorithm at the report boundary

**Resolution:** hash-based redaction via `choreo.redaction.redact_wire_id()` (SHA-256 truncated to 16 hex chars, prefixed `sha256:`). Single source of truth in the framework's `choreo.redaction` module; the reporter imports, does not re-implement. Algorithm version surfaced via `run.redactions.redaction_version`.

**Rejected alternatives:** (a) reuse the framework's in-process `_redact()` (head=8, tail=4) — discarded because the in-memory threat model differs from the on-disk archive threat model (greppable archives leak prefix scheme + 12 chars + length); (b) re-implement in the reporter for "independence" (v2's approach) — discarded because duplicated implementations drift silently; (c) salted hash — discarded because cross-run linkage by hash equality is a deliberate consumer affordance.

**Rationale:** hash-based redaction is not reversible; truncating to 16 hex chars (~64 bits) is sufficient for within-run disambiguation but insufficient for archive-grep correlation.

### D-3. Reply state enum is NOT extended

**Resolution:** the `state` enum stays at its v1.0 four values. `StageReplyState.FIRED` maps to `"replied"`; `StageReplyState.FIRED_BUILDER_ERROR` maps to `"reply_failed"`. No new schema enum values.

**Rejected alternative:** v2's "extend the enum with `fired` and `fired_builder_error` for vocabulary parity with code" — discarded because it creates a permanent dual-vocabulary in `results.json` (consumers must handle both `replied` and `fired` for the same outcome) which is more harmful than the trivially-additive value.

**Rationale:** the framework's internal enum naming is an implementation choice; the wire format should expose stable user-facing semantics.

### D-4. `reporter_version` semver bump

**Resolution:** semver minor bump (`0.X` → `0.X+1`). The schema is additive (at the v1.x boundary); the reporter's API is additive.

**Rejected alternative:** semver patch — discarded because consumer pipelines treat minor as "new optional features added" which is exactly the message PRD-012 needs to convey.

**Rationale:** the snapshot test (US-6) ensures no behavioural change for single-`Harness` consumers, but the schema-version bump and the new fields warrant a clear pipeline-visible signal.

### D-5. Stage detection via explicit `kind` discriminator

**Resolution:** the framework adds `kind: Literal["single_harness", "stage"]` to both result types. The reporter dispatches on `result.kind`. Stage-emission branch wrapped in try/except, falling through to single-`Harness` emission with a structured `WARNING` event on unexpected failure.

**Rejected alternatives:** (a) duck-typing via `hasattr(result, "by_transport") and hasattr(result, "replies")` (v2's approach) — discarded because (i) `ScenarioResult` already has `replies`, collapsing the check to a single signal; (ii) duck-typing is spoofable by malicious or buggy result wrappers; (iii) refactors of attribute names silently break detection. (b) `isinstance` only — works fine but the import cost across version skew was the concern; v3 resolves this by promoting `_StageScenarioResult` to public `StageScenarioResult` and pinning a minimum framework version on the reporter package.

**Rationale:** explicit discriminators are unambiguous, refactor-safe, and don't depend on transitive attribute structure.

### D-6. Per-transport colour assignment in the renderer

**Resolution:** hash the transport name to a colour palette of 8 (deterministic). Collisions tolerable (two transports may share a colour); fallback to neutral grey for unrecognised names. Tracked as future work in OQ-3 (palette expansion).

**Rejected alternatives:** (a) fixed mapping per transport class (`nats` → green, `kafka` → orange) — discarded because it locks in default colours for hypothetical future transports the reporter has not seen. (b) random per-run colour assignment — discarded because consumer pattern recognition across CI runs depends on stable colour assignment. (c) larger palette — discarded for accessibility; 8 colours is the upper bound for pairwise-distinguishable palettes under common colour-vision deficiencies.

**Rationale:** stable hashing balances determinism with no consumer-facing bias; palette size 8 keeps accessibility tractable.

### D-7. Snapshot test masks

**Resolution:** mask `reporter_version` (always changing), `started_at` / `finished_at` / `duration_ms` / `git_sha` / `git_branch` / `hostname` (per-run variable, already masked in PRD-007's existing snapshot test). `schema_version` is masked from the diff (legitimately moves `"1"` → `"1.1"`).

**Rejected alternative:** structural snapshot via `beautifulsoup4` parse + selective serialise — viable for the HTML output and possibly a better long-term path, but byte-identity is the simpler v3 contract. Tracked as a follow-up if the mask grows.

**Rationale:** the snapshot test's purpose is to assert "PRD-012 is non-breaking for single-`Harness`" beyond the deliberate `schema_version` bump.

---

## Open Questions

These remain genuinely unresolved. Each names an owner and a decision deadline.

- **OQ-1. Renderer toggle persistence across page reloads.** Should the by-transport grouping toggle (§3.4) persist across reloads via `localStorage` (against PRD-007's "stateless between loads" constraint) or only via `location.hash` (per the existing state model)? Defer; pick the simpler `location.hash` for v1, revisit if consumer feedback demands it. **Owner:** Reporter package owners. **Decide by:** Phase 2 PR review.

- **OQ-2. Renderer telemetry on Stage adoption.** Should the renderer log a structured event when it first encounters a Stage scenario, so consumer pipelines can detect Stage adoption without parsing every JSON file? Probably not (the JSON itself is the signal); flagging for completeness. **Owner:** Platform. **Decide by:** Phase 2 PR review.

- **OQ-3. Future colour-palette expansion.** Whether D-6's 8-colour palette grows in a future PRD-007.x revision to accommodate runs with >8 distinct transport names. Defer until consumer feedback warrants. **Owner:** Reporter package owners. **Decide by:** PRD-007 v3 (if any).

- **OQ-4. Chronicle dashboard surfaces for Stage data.** PRD-012's Phase 1 ingested per-handle transport into Chronicle and surfaced run-level transports in the runs table. The dashboard layer (per-transport breakdowns, bridge-failure panels, cross-transport-coordination views) is a separate work-stream successor to PRD-010. **Owner:** Chronicle package owners. **Decide by:** Phase 3 of PRD-012 ships (track via a new PRD-013 or PRD-010-revision).

**Note:** v2 listed "Chronicle ingest schema migration" as OQ-2 (deferred). v3 reclassifies it as a Phase 1 dependency (Implementation Plan §Phase 1) — Chronicle's `runs.transport NOT NULL` constraint will reject the first Stage run unless the migration lands first. No longer an open question; the migration is scoped into Phase 1 with named owner.

---

## Out of Scope (explicit)

- **Schema major-version bump (`test-report-v2`).** Out of scope. PRD-012 is additive within v1.x (minor bump from v1.0 to v1.1).
- **Re-rendering of single-`Harness` scenarios.** Single-`Harness` scenario rendering is byte-identical to pre-PRD-012 (modulo `schema_version`); no changes here.
- **Charts.** No per-transport histograms, no fan-out diagrams, no cross-transport-reply Sankey plots.
- **Run-over-run comparison of Stage reports.** Inherits PRD-007's "one run per report" constraint.
- **Live updates during a run.** Inherits PRD-007's "report produced once at session teardown" constraint.
- **Chronicle dashboard surfaces specific to Stage.** Chronicle's *ingest* migration is a Phase 1 dependency; Chronicle's *dashboard* surfaces (per-transport breakdowns, bridge-failure panels) are scheduled separately, owned by Chronicle package owners.
- **Re-run / replay buttons for failed Stage scenarios.** Out of scope; reporters do not own re-execution.
- **PRD-007 unification.** PRD-007 stays the parent document; PRD-012 extends it additively. A future v3 of PRD-007 that folds Stage support inline is a possibility, not in scope here.

---

## Testing Strategy

The test pyramid: many unit tests, a focused property-based set for schema additivity, integration tests for the renderer state layer, three e2e tests via pytester, three snapshot tests, one Chronicle contract test, one performance benchmark, and a manual checklist used as Phase 2 PR gate.

**TDD discipline.** Within each phase scope, every code change is preceded by a failing test in the same PR. The phase exit criterion (Implementation Plan) makes this explicit.

### Unit tests

Located in `packages/choreo-reporter/tests/unit/`.

- **Schema validation.** Every emitted JSON validates against `docs/schemas/test-report-v1.1.json` via the `jsonschema` library. Cases: empty run, single-`Harness` only, Stage only, mixed run.
- **Reply state mapping.** `_serialise_reply_state(state)` is exercised against every value in §1.7. Cases: each `StageReplyState` value (including `ARMED` runtime-only — asserts `AssertionError`), each `ReplyReportState` value, `None` (asserts `ValueError`), `"nonsense"` (asserts `ValueError`).
- **Hash redaction.** `redact_wire_id()` is exercised against:
  - Empty string, 1 char, 32 chars, 1000 chars — all return `sha256:<16 hex>` form.
  - Same input twice → identical output (deterministic).
  - Different inputs → different outputs (no collision in test fixtures).
  - The `REDACTION_VERSION` constant is `"v1"`.
- **Redaction sites.** A canary test fixtures a Stage scenario with a wire id whose un-redacted form contains the marker `SECRET-CANARY-XYZ`. The test asserts the marker does NOT appear in:
  - `scenario.stage.correlation_ids[*]`
  - Any handle's `correlation_id` for Stage handles
  - `handle.diagnostic` strings
  - `scenario.summary_text`
  - Reply diagnostic strings
- **Stage detection.** `kind` discriminator dispatch. Cases: real `StageScenarioResult` (kind=="stage"), real `ScenarioResult` (kind=="single_harness"), result with corrupt kind (raises). Plus a hostile-fixture case: a result whose `by_transport` is a property that raises on access — the reporter's try/except wrapper falls through to single-`Harness` emission and emits a structured WARNING `stage_emission_failed`.
- **`run.transport` vs `run.transports` precedence.** Single-`Harness`-only run emits `transport`; Stage-only run emits `transports` and `null` for `transport`; mixed run emits both with `transport: null`.
- **Negative — malformed Stage result.** `StageScenarioResult` with `by_transport=None` → reporter emits an empty `stage.transports` array and an empty `stage.correlation_ids` map (does not crash). `StageScenarioResult` with `by_transport={}` → same behaviour.

### Property-based tests (Hypothesis)

Located in `packages/choreo-reporter/tests/property/`.

- **Schema additivity.** Hypothesis generates valid pre-PRD-012 reports (against `test-report-v1.0.json`); each generated report validates against `test-report-v1.1.json` (modulo `schema_version` substitution). Property: any v1.0-valid report is v1.1-valid after `schema_version` rewrite.
- **`run.transport` / `run.transports` invariant.** Hypothesis generates run dicts; the reporter's serialiser never produces a run with both fields absent. Property: at least one of the two is always present.
- **Redaction non-leakage.** Hypothesis generates wire ids of varying lengths; no generated id appears verbatim in the redacted output. Property: `wire_id_string not in redact_wire_id(wire_id_string)`.

### JSON Schema tests

Located in `packages/choreo-reporter/tests/schema/`.

- **Schema is valid JSON Schema draft 2020-12.** A meta-validation step.
- **Pre-PRD-012 valid JSON validates against post-PRD-012 schema** (after `schema_version` substitution `"1"` → `"1.1"`). Backward-compatibility check.
- **Post-PRD-012 valid JSON does NOT validate against `test-report-v1.0.json`** (the new fields' presence is rejected by the v1.0 `additionalProperties: false`). This is the schema's "we extended deliberately" assertion.
- **`anyOf` constraint negative case.** `test_a_run_missing_both_transport_and_transports_should_fail_schema_validation` — a run with neither key fails validation.
- **`anyOf` constraint positive cases.** A run with only `transport` validates; a run with only `transports` validates; a run with both validates.
- **Transport-name regex.** A run with a transport name violating `^[a-zA-Z0-9_-]{1,64}$` (e.g. `"<script>"`) fails validation. A run with a transport name passing the regex validates.
- **Wire-id-below-threshold.** `test_a_wire_id_below_the_redaction_input_length_should_still_emit_redacted_form` — even a 1-char wire id emits `sha256:<16 hex>` form (hash redaction has no length floor).

### Renderer integration tests

Located in `packages/choreo-reporter/tests/integration/`.

The renderer's state layer (grouping mode, badge rendering, escape handling) is tested in JSDOM via Playwright Component Test or equivalent. These tests do not run under a full browser — they exercise the renderer's pure-function output against a controlled DOM.

- **`data-grouping-mode` attribute flips on toggle click.** Asserts the attribute value changes from `"by-scenario"` to `"by-transport"` and back.
- **Filter state persists across grouping mode switches.** Apply a status filter; toggle grouping; assert the filter is still active.
- **HTML escape of transport names.** A Stage result with `transport: "<script>alert(1)</script>"` renders into HTML; assert the rendered string does NOT contain unescaped `<script>`. Combined with schema validation, this is defence in depth.
- **Failing-reply CSS class.** A reply with state `reply_failed` carries `data-reply-publish-failed="true"` AND its response-transport badge has class `reply-transport-badge--failed`.
- **Stage breadcrumb attributes.** A Stage scenario renders the breadcrumb with `data-stage-bridge-class`, `data-stage-transports` (space-separated), and `data-stage-transport` on individual pills.
- **axe-core a11y audit.** A rendered Stage report passes axe-core's WCAG 2.1 AA suite. `aria-label` on transport badges is asserted explicitly.
- **Keyboard accessibility.** The grouping toggle responds to Tab focus + Enter/Space keypress with the same observable as a click.

### End-to-end tests via pytester

Located in `packages/choreo-reporter/tests/e2e/`.

- **Stage scenario emits expected JSON shape.** A pytester-driven test that runs a known Stage test ([Group I from `test_stage_replies.py`](../../packages/core/tests/integration/test_stage_replies.py); see [docs/test-plans/0027-stage-integration-tests.md](../test-plans/0027-stage-integration-tests.md) for the group taxonomy), captures the emitted `results.json`, and asserts every Stage-specific field is present and correctly populated.
- **Mixed run emits both surfaces.** A pytester test with one single-`Harness` test and one Stage test in the same session; assert the JSON has scenarios with and without `stage` blocks, run-level `transport: null` plus populated `transports`.
- **HTML rendering of a Stage scenario** asserts the stable-tier `data-*` contract: every transport badge, every reply badge, every Stage breadcrumb attribute.

### Snapshot tests (US-6)

Located in `packages/choreo-reporter/tests/snapshot/`.

- **Single-`Harness` JSON byte-identity.** Diffs the post-PRD-012 reporter's output against `tests/reporter/snapshot/baseline_pre_prd012.json` (committed; generated using the **pre-PRD-012 reporter binary**, not by the post-PRD-012 reporter on first run). Masks per D-7 plus `schema_version`. Diff MUST be empty.
- **Single-`Harness` HTML byte-identity.** Same approach for the HTML output against `tests/reporter/snapshot/baseline_pre_prd012.html`.
- **Regeneration procedure** documented at `tests/reporter/snapshot/README.md`. Operator pins the pre-PRD-012 reporter version, runs with `CHOREO_REPORTER_UPDATE_SNAPSHOTS=1`. CI never sets the env var.

### Chronicle contract test

Located in `packages/choreo-reporter/tests/contract/`.

- **PRD-012 reports are accepted by Chronicle's ingest endpoint.** A contract test that POSTs Appendix A.3 (the full Stage scenario JSON) verbatim to a running Chronicle instance and asserts:
  - HTTP 200/201 (no 4xx, no 5xx).
  - The run is queryable via Chronicle's GET endpoints.
  - Per-handle `transport` is preserved in `handle_measurements.transport`.
  - `run.transports` is preserved in the new `runs.transports[]` column.
- This test runs in CI alongside the e2e suite using the existing `chronicle_db` marker. It is the load-bearing contract that proves PRD-012 + the Chronicle migration land together.

### Manual checklist (US-1, US-4, US-5)

Located in `packages/choreo-reporter/tests/MANUAL.md`. The file is a Markdown checkbox list. The Phase 2 PR exit criterion includes "named reviewer commits the completed checklist as part of the PR". The reviewer is identified by GitHub handle in the PR description, signs the boxes off, and commits the file.

Manual checks:

- Open a Stage report; verify per-handle transport badges visible.
- Toggle by-transport grouping; verify the tree re-organises and filters persist.
- Open a Stage report with a failing handle; verify the failing-side sub-badge is in the scenario header.
- Open a single-`Harness` report under the post-PRD-012 reporter; verify it looks identical to the pre-PRD-012 baseline (no transport badges, no Stage breadcrumb).
- Tab through a handle row; verify the transport badge is announced by VoiceOver / NVDA.

These are visual / qualitative checks beyond the automated axe-core / integration suite.

### Performance test (US-7)

A pytester benchmark with two workloads (per US-7 AC):

1. **Reference workload** (100 scopes × 5 handles × 2 replies):
   - JSON write `<500ms`.
   - HTML render time-to-interactive `<1s`.
   - Memory `<5 MB` per report directory.
   - Toggle response `<200ms`.
2. **Cap-saturated workload** (10k handles, 1k scenarios, 200 transports across runs, total JSON within 200 KB of PRD-007's 11 MB cap):
   - JSON write `<2s`.
   - HTML render time-to-interactive `<3s` (stretch budget; regression detector).
   - Toggle response `<200ms` (CSS-only design is independent of handle count).

The benchmark fails the build if any workload regresses by more than 10% from the prior baseline; the baseline is updated on every Phase 2 release.

---

## Risks and Mitigations

### Risk: Chronicle migration not landed before reporter ships (likelihood: medium / impact: high)

If the reporter starts emitting `run.transport: null` before Chronicle's `runs.transport NOT NULL` constraint is relaxed, every Stage run is lost from archival. Owner: Reporter and Chronicle maintainers; they MUST coordinate Phase 1.

**Mitigation:** Phase 1 exit criterion gates on the Chronicle contract test (§Testing Strategy). The contract test ingests a Stage report into a running Chronicle and asserts a 2xx response. Both PRs (reporter + Chronicle migration) ship in the same release; reverting one without the other is detected by the contract test.

### Risk: strict-validator consumers break on the `schema_version` bump (likelihood: low / impact: medium)

A consumer pinned to `test-report-v1.0.json` rejects PRD-012 reports because of the `additionalProperties: false` enforcement on the new fields, even though the bump from `"1"` to `"1.1"` is intentional.

**Mitigation:** Phase 3 release notes call out the bump explicitly. Both `test-report-v1.0.json` and `test-report-v1.1.json` are kept in tree so consumers can pin either. Consumers using `schema_version.startswith("1")` for compatibility gating are unaffected. The reporter README documents the migration path.

### Risk: renderer regression for single-`Harness` consumers (likelihood: low / impact: high)

The renderer changes touch shared state (the `state` object, the render pipeline). A bug in the by-transport grouping toggle could affect single-`Harness` rendering.

**Mitigation:** the snapshot test (US-6) catches HTML diffs. The state model's `groupingMode` field defaults to `"by-scenario"` (current behaviour); single-`Harness` runs never see the toggle's effect on their data. CSS-only re-grouping (§3.4) avoids DOM mutation that could affect single-`Harness` paths.

### Risk: redaction algorithm-version drift (likelihood: low / impact: high)

If the framework tightens `redact_wire_id` (e.g. response to a CVE) and the reporter package version is not bumped, archived reports may carry weaker redaction than expected.

**Mitigation:** `REDACTION_VERSION` is a public constant in `choreo.redaction`. Algorithm changes bump the version. The reporter emits `run.redactions.redaction_version`, surfacing the version to every consumer. A contract test (`test_a_redaction_version_change_should_emit_a_new_version_string`) asserts the surfacing path.

### Risk: snapshot test brittleness (likelihood: medium / impact: low)

If pytest-version or pytest-asyncio changes the byte layout of an existing single-`Harness` test's output (e.g. a new field is added to the `pytest` infrastructure), the snapshot test would fail spuriously.

**Mitigation:** the snapshot's masking ruleset is kept narrow (per D-7). When a new pytest-infrastructure field appears, the team updates the mask alongside the upgrade. The snapshot is meant to catch *PRD-012-introduced* changes, not pin against pytest evolution. The regeneration procedure (§Backward compatibility §4.3) gives operators an explicit path to update the baseline when legitimate non-PRD-012 changes occur.

### Risk: per-transport colour palette collision causes visual confusion (likelihood: medium / impact: low)

With 8 colours and an arbitrary number of transports, two transports may share a colour.

**Mitigation:** the badge text is the transport name verbatim (and `aria-label` carries it explicitly); the colour is decorative. Colour collision does not impair functionality. Tracked in OQ-3 if consumer feedback demands palette expansion.

---

## Implementation Plan

Three phases. Phase 1 ships the JSON shape AND the Chronicle migration as a single coordinated release (the contract test gates both). Phase 2 ships the renderer; Phase 3 ships the docs and reporter README.

**TDD discipline within each phase.** Every code change in each phase scope is preceded by a failing test in the same PR. The test order within Phase 1: schema test → schema change; redaction test → redaction helper; reply-state-mapping test → mapping helper. No "scaffold-then-green" skipping — each behavioural assertion lands red before its implementation.

### Phase 1 — Schema, reporter, and Chronicle migration

**Estimated effort:** medium (~2 person-weeks).

**Scope:**

- Framework changes (§2.8): `kind` discriminators, `StageScenarioResult` public rename, `StageScenarioResult.correlation_id`, `Harness.transport_name`, `choreo.redaction` module.
- Schema diff applied to `docs/schemas/test-report-v1.0.json` → `test-report-v1.1.json` (every change in §1, with v1.0 retained).
- Reporter changes (§2): `kind`-based dispatch, reply state mapping, hash-based wire-id redaction, run-level transports, version bump, response-topic→reply_topic mapping.
- Chronicle migration: relax `runs.transport NOT NULL`, add `runs.transports TEXT[]` column, update `bulk_insert_scenarios` and `copy_handle_measurements` to consume per-handle `transport`, refresh continuous aggregates to `GROUP BY` per-handle transport rather than run-level.
- Tests: unit (Schema validation, reply mapping, redaction sites canary, Stage detection, malformed-result negative), property-based (additivity, anyOf invariant, redaction non-leakage), schema validation (anyOf positive + negative, transport-name regex, wire-id-below-threshold), e2e via pytester (Stage JSON shape, mixed run, HTML data-* contract), Chronicle contract test.
- Snapshot test for single-`Harness` byte-identity (against pre-PRD-012 baseline).

**Owners:** Reporter package maintainer + Chronicle package maintainer (joint).

**Exit criterion:** PR(s) merged with all tests green, including:

- Chronicle contract test passes against a running TimescaleDB instance.
- Schema validation tests pass for both v1.0 and v1.1 schemas.
- Snapshot test diff is empty (modulo `schema_version` and D-7 mask set).
- Reporter version bumped, `run.reporter_version` reflects the new version.

**Deliverable:** consumers running the new reporter against Stage tests get a `results.json` with the full Stage shape; Chronicle ingests Stage reports without errors.

### Phase 2 — HTML renderer

**Estimated effort:** medium (~2 person-weeks).

**Scope:**

- Renderer additions (§3): per-handle transport badge, per-reply badges, scope-level Stage breadcrumb, by-transport grouping toggle (CSS-only design per §3.4), failing-side sub-badge, mandatory `html.escape` on all runtime-derived text.
- Per-transport colour palette + hashing (D-6).
- Renderer integration tests: grouping toggle, filter persistence, HTML escape, axe-core a11y, keyboard accessibility, breadcrumb attributes.
- Performance benchmark with reference + cap-saturated workloads (US-7).
- Manual checklist (`tests/MANUAL.md`) updated with Stage cases (US-1, US-4, US-5).
- Snapshot test for single-`Harness` HTML byte-identity.

**Owner:** Reporter package maintainer.

**Exit criterion:** PR merged with all tests green, including:

- Stable-tier `data-*` attributes asserted in renderer integration tests.
- Performance benchmark meets the per-workload SLAs in §Non-Functional Performance.
- Manual checklist file is committed in the same PR with all boxes ticked, named reviewer's GitHub handle in the commit author or co-author trailer.

**Deliverable:** consumers running the new reporter against Stage tests get the visual surface in `index.html`.

### Phase 3 — Documentation and downstream coordination

**Estimated effort:** small (~1 person-week).

**Scope:**

- [PRD-007's schema reference](PRD-007-test-report-output.md) §Schema is updated to point at the optional fields with a "see PRD-012" cross-link.
- [Stage user guide](../guides/stage.md) gains a "Reading the test report" section showing example HTML rendering and JSON consumption.
- Reporter README documents:
  - The new fields, with worked examples.
  - The schema-version bump (v1.0 → v1.1) and the migration path for strict-validator consumers.
  - The redaction policy at the report boundary, with a pointer to `REDACTION_VERSION`.
  - The stable-tier vs advisory-tier `data-*` contract (§3.6).
- Chronicle dashboard PRD (PRD-010 successor) opened with a tracked owner; this is scope for a separate work-stream.

**Owner:** Platform / Documentation.

**Exit criterion:** docs landed; Chronicle dashboard follow-up tracked in §Open Questions (OQ-4) with a named owner. The Chronicle *ingest* migration is a Phase 1 deliverable, not Phase 3.

**Deliverable:** PRD-012's surface is visible to documentation readers; the Chronicle dashboard work-stream is queued for a follow-up PRD.

---

## Related PRDs / ADRs

- [PRD-007 — Test Report Output](PRD-007-test-report-output.md) — the parent document this extends additively.
- [PRD-008 — Scenario Replies](PRD-008-scenario-replies.md) — the single-`Harness` reply primitive whose `ReplyReportState` PRD-012 maps alongside `StageReplyState`.
- [PRD-009 — Chronicle Reporting Server](PRD-009-chronicle-reporting-server.md) — downstream consumer of `results.json`. Ingest migration is a Phase 1 dependency.
- [PRD-010 — Chronicle Dashboard Views](PRD-010-chronicle-dashboard-views.md) — visual surface of Chronicle data; Stage dashboard support is a separate work-stream.
- [PRD-011 — Multi-Transport Scenarios](PRD-011-multi-transport-stage.md) — the framework feature being reported on.
- [ADR-0027 — Stage Coordinator](../adr/0027-stage-multi-transport-coordinator.md) — design for the framework surface; defines `Handle.transport`, `_StageReply.trigger_transport` / `response_transport`, the in-process `_redact()` posture.
- [ADR-0017 — Reply fire-and-forget results](../adr/0017-reply-fire-and-forget-results.md) — security posture for reply diagnostics; PRD-012's `builder_error` redaction inherits.
- [ADR-0019 — Pluggable correlation policy](../adr/0019-pluggable-correlation-policy.md) — the per-harness layer the bridge composes.
- [docs/schemas/test-report-v1.0.json](../schemas/test-report-v1.0.json) — the v1.0 schema (retained in tree for consumers pinned to it).
- [docs/schemas/test-report-v1.1.json](../schemas/test-report-v1.1.json) — the v1.1 schema (this PRD's output).
- [docs/test-plans/0027-stage-integration-tests.md](../test-plans/0027-stage-integration-tests.md) — Stage integration test plan; the source of "Group I/J/K" labels referenced throughout.
- [docs/guides/stage.md](../guides/stage.md) — user-facing guide for the framework feature.

---

## Appendix A — Schema diff with before / after examples

This appendix shows the concrete JSON before and after PRD-012 lands. The diff is additive (with the `schema_version` bump from `"1"` to `"1.1"`).

### A.1 Schema diff

Diff from `docs/schemas/test-report-v1.0.json` to `docs/schemas/test-report-v1.1.json`. The v1.0 file remains in tree for consumers pinned to it.

```diff
 {
-  "$id": "https://choreo.dev/schemas/test-report-v1.0.json",
+  "$id": "https://choreo.dev/schemas/test-report-v1.1.json",
   "$schema": "https://json-schema.org/draft/2020-12/schema",
   "$defs": {
     "run": {
       "required": [
         "started_at", "finished_at", "duration_ms", "totals",
         "project_name",
-        "transport",
         "allowlist_path", "python_version", "harness_version",
         "reporter_version", "git_sha", "git_branch", "environment",
         "hostname", "xdist", "truncated", "redactions"
       ],
       "properties": {
         ...
-        "transport": { "type": "string" },
+        "transport":  { "type": ["string", "null"] },
+        "transports": {
+          "type": "array",
+          "items": {
+            "type": "string",
+            "pattern": "^[a-zA-Z0-9_-]{1,64}$"
+          }
+        },
         ...
       },
+      "anyOf": [
+        { "required": ["transport"] },
+        { "required": ["transports"] }
+      ]
     },

     "scenario": {
       "properties": {
         ...
-        "correlation_id": { "type": "string" },
+        "correlation_id": { "type": ["string", "null"] },
         ...
         "summary_text": { "type": "string" },
+        "stage": {
+          "type": ["object", "null"],
+          "additionalProperties": false,
+          "required": ["bridge_class", "transports", "correlation_ids"],
+          "properties": {
+            "bridge_class": {
+              "type": "string",
+              "pattern": "^[A-Za-z][A-Za-z0-9_]{0,127}$"
+            },
+            "transports": {
+              "type": "array",
+              "items": {
+                "type": "string",
+                "pattern": "^[a-zA-Z0-9_-]{1,64}$"
+              }
+            },
+            "correlation_ids": {
+              "type": "object",
+              "additionalProperties": {
+                "type": "string",
+                "pattern": "^sha256:[0-9a-f]{16}$"
+              }
+            }
+          }
+        }
       }
     },

     "handle": {
       "properties": {
         ...
         "truncated": { "type": "boolean" },
+        "transport": { "type": ["string", "null"] }
       }
     },

     "reply_report": {
       "properties": {
         "trigger_topic":          { "type": "string" },
         "reply_topic":            { "type": "string" },
         "state": {
           "enum": ["armed_no_match", "armed_matcher_mismatched", "replied", "reply_failed"]
         },
         ...
         "correlation_overridden": { "type": "boolean" },
+        "trigger_transport":  { "type": ["string", "null"] },
+        "response_transport": { "type": ["string", "null"] }
       }
     },

     "redactions": {
       "properties": {
         ...
+        "redaction_version": {
+          "type": "string",
+          "description": "Algorithm version emitted by choreo.redaction.redact_wire_id. Bumps if the algorithm tightens.",
+          "pattern": "^v[0-9]+$"
+        }
       }
     }
   }
 }
```

Three things to note:

1. **No `state` enum extension.** `StageReplyState.FIRED` maps to `"replied"`; `StageReplyState.FIRED_BUILDER_ERROR` maps to `"reply_failed"` (per §1.7).
2. **`reply_topic` key unchanged.** The framework's `StageReplyReport.response_topic` is serialised under the existing `reply_topic` key (per §1.2.1). No JSON key change.
3. **`anyOf`, not `oneOf`.** Mixed-mode runs may emit both `transport` (null) and `transports` (per §1.5).

### A.2 Single-`Harness` scenario — JSON byte-identical aside from `schema_version`

A single-`Harness` test's emitted JSON differs from the v1.0 reporter's output only in the `schema_version` value. All other fields are byte-identical, including the omission of `transports`, `transport` on handles, and `stage` on scenarios.

```json
{
  "schema_version": "1.1",
  "run": {
    "transport": "MockTransport",
    "redactions": {
      "redaction_version": "v1",
      "...": "..."
    },
    "...": "..."
  },
  "tests": [
    {
      "nodeid": "tests/test_orders.py::test_order_settled",
      "scenarios": [
        {
          "name": "settle",
          "correlation_id": "TEST-3f2a91b8c4d50e1f",
          "outcome": "pass",
          "handles": [
            {
              "topic": "orders.settled",
              "outcome": "pass",
              "latency_ms": 12.4,
              "matcher_description": "field_equals('status', 'SETTLED')"
            }
          ],
          "replies": [],
          "...": "..."
        }
      ]
    }
  ]
}
```

No `stage` key on the scenario; no `transport` on the handle; no `transports` on the run. The single-`Harness` `correlation_id` is unchanged (un-redacted).

### A.3 Stage scenario — JSON after PRD-012

```json
{
  "schema_version": "1.1",
  "run": {
    "transport": null,
    "transports": ["kafka", "nats"],
    "redactions": {
      "redaction_version": "v1",
      "...": "..."
    },
    "...": "..."
  },
  "tests": [
    {
      "nodeid": "tests/test_bridge.py::test_orders_bridge_round_trip",
      "scenarios": [
        {
          "name": "bridge_round_trip",
          "correlation_id": "logical-3f2a91b8c4d50e1f",
          "outcome": "pass",
          "stage": {
            "bridge_class": "MappedBridge",
            "transports": ["kafka", "nats"],
            "correlation_ids": {
              "nats":  "sha256:3f2a91b8c4d50e1f",
              "kafka": "sha256:7e8b50c1ad4912ff"
            }
          },
          "handles": [
            {
              "topic": "results",
              "outcome": "pass",
              "latency_ms": 41.7,
              "transport": "nats",
              "correlation_id": "sha256:3f2a91b8c4d50e1f",
              "matcher_description": "field_equals('kind', 'result')",
              "...": "..."
            }
          ],
          "replies": [
            {
              "trigger_topic": "orders.new",
              "reply_topic": "orders.processed",
              "state": "replied",
              "trigger_transport": "kafka",
              "response_transport": "nats",
              "candidate_count": 1,
              "match_count": 1,
              "reply_published": true,
              "builder_error": null,
              "matcher_description": "(any)",
              "correlation_overridden": false
            }
          ],
          "...": "..."
        }
      ]
    }
  ]
}
```

Three things to note:

- The `correlation_ids` map values and Stage handle `correlation_id` use the new `sha256:<16 hex>` form (per §1.5.1).
- The single-`Harness` and Stage scenario `correlation_id` differ in semantics: single-`Harness` carries the un-redacted logical id; Stage carries the un-redacted *logical* id at scenario level (a consumer-meaningful pivot for run-linkage, per §1.4.1) and the redacted *wire* id at handle level.
- The reply's `state` is `"replied"` (mapped from `StageReplyState.FIRED`); same string as a single-`Harness` reply. The `trigger_transport` / `response_transport` discriminate the cross-transport coordination. `reply_topic` (JSON key, unchanged) carries the value of `StageReplyReport.response_topic` (framework attribute).
- `transports` is sorted alphabetically: `["kafka", "nats"]`.

### A.4 Stage scenario with `FIRED_BUILDER_ERROR` — JSON after PRD-012

```json
{
  "scenarios": [
    {
      "name": "bridge_with_dropped_response",
      "stage": {
        "bridge_class": "MappedBridge",
        "transports": ["nats", "kafka"],
        "correlation_ids": {
          "nats":  "sha256:ab12cd34ef5678aa",
          "kafka": "sha256:bc23de45fa6789bb"
        }
      },
      "handles": [],
      "replies": [
        {
          "trigger_topic": "orders.new",
          "reply_topic": "orders.processed",
          "state": "reply_failed",
          "trigger_transport": "kafka",
          "response_transport": "nats",
          "candidate_count": 1,
          "match_count": 1,
          "reply_published": false,
          "builder_error": "RuntimeError",
          "matcher_description": "(any)",
          "correlation_overridden": false
        }
      ]
    }
  ]
}
```

`builder_error` carries the exception class name only, never `str(exc)`. The renderer surfaces the response transport in the failing-side sub-badge per §3.5. The `state` value is `"reply_failed"` (mapped from `StageReplyState.FIRED_BUILDER_ERROR`); the same wire string single-`Harness` reports use for the analogous outcome.

---

## Appendix B — HTML rendering shape

Concrete HTML structure for a Stage scenario, demonstrating every `data-*` contract. All runtime-derived values are passed through `html.escape(value, quote=True)` at render time (§3); the snippets below show the post-escape form.

```html
<li class="test"
    data-nodeid="tests/test_bridge.py::test_orders_bridge_round_trip"
    data-outcome="passed">

  <div class="test-header">
    <span class="test-name">test_orders_bridge_round_trip</span>
    <span class="test-outcome" data-outcome="passed">PASS</span>
  </div>

  <ul class="scenarios" role="tree">
    <li class="scenario"
        role="treeitem"
        aria-expanded="false"
        data-scenario-name="bridge_round_trip"
        data-outcome="pass">

      <!-- New: scope-level Stage breadcrumb. data-stage-transports is space-separated for [~=] selector compat. -->
      <div class="stage-breadcrumb"
           data-stage-bridge-class="MappedBridge"
           data-stage-transports="kafka nats">
        <span class="label">Stage scenario</span> ·
        bridge: <span class="stage-bridge-class">MappedBridge</span> ·
        transports:
        <!-- Breadcrumb pills carry data-stage-transport (singular). Distinct from handle-row data-handle-transport. -->
        <span class="stage-transport-badge" data-stage-transport="kafka">kafka</span>
        <span class="stage-transport-badge" data-stage-transport="nats">nats</span> ·
        correlation_ids:
        <span class="wire-id" data-stage-transport="nats">sha256:3f2a91b8c4d50e1f</span>
        <span class="wire-id" data-stage-transport="kafka">sha256:7e8b50c1ad4912ff</span>
      </div>

      <ul class="handles">
        <li class="handle"
            data-topic="results"
            data-outcome="pass">
          <span class="handle-outcome" data-outcome="pass">PASS</span>
          <span class="handle-topic">results</span>
          <!-- New: per-handle transport badge with aria-label (US-1 AC4) -->
          <span class="handle-transport-badge"
                data-handle-transport="nats"
                aria-label="transport: nats">nats</span>
          <span class="handle-latency">41.7ms</span>
        </li>
      </ul>

      <ul class="replies">
        <li class="reply-row"
            data-reply-state="replied"
            data-reply-publish-failed="false">
          trigger:
          <span class="topic">orders.new</span>
          <!-- New: trigger transport badge -->
          <span class="reply-transport-badge"
                data-reply-trigger-transport="kafka">kafka</span>
          →
          <span class="topic">orders.processed</span>
          <!-- New: response transport badge -->
          <span class="reply-transport-badge"
                data-reply-response-transport="nats">nats</span>
          state: <span class="reply-state">replied</span>
        </li>
      </ul>

    </li>
  </ul>
</li>
```

For a failing Stage scenario, the failing-side sub-badges appear in the scenario header. Both a failing-handle case and a failing-reply case are illustrated:

```html
<li class="scenario"
    role="treeitem"
    aria-expanded="true"
    data-scenario-name="bridge_response_dropped"
    data-outcome="fail">

  <div class="scenario-header">
    <span class="scenario-name">bridge_response_dropped</span>
    <span class="scenario-outcome" data-outcome="fail">FAIL</span>
    <!-- Stable-tier: failing-handle sub-badge. Space-separated for multi-transport failure. -->
    <span class="scenario-failing-side"
          data-failing-transports="nats">FAIL → [nats]</span>
    <!-- Advisory-tier (§3.6): failing-reply sub-badge; may evolve. -->
    <span class="scenario-failing-reply"
          data-failing-reply-response-transport="nats">REPLY FAILED → [nats]</span>
  </div>

  <ul class="handles">
    <li class="handle" data-topic="results" data-outcome="fail">
      <span class="handle-outcome" data-outcome="fail">FAIL</span>
      <span class="handle-topic">results</span>
      <span class="handle-transport-badge"
            data-handle-transport="nats"
            aria-label="transport: nats">nats</span>
    </li>
  </ul>

  <ul class="replies">
    <!-- A reply-failed row carries the data-reply-publish-failed="true" flag and the badge --failed class. -->
    <li class="reply-row"
        data-reply-state="reply_failed"
        data-reply-publish-failed="true">
      trigger:
      <span class="topic">orders.new</span>
      <span class="reply-transport-badge"
            data-reply-trigger-transport="kafka">kafka</span>
      →
      <span class="topic">orders.processed</span>
      <span class="reply-transport-badge reply-transport-badge--failed"
            data-reply-response-transport="nats">nats</span>
      state: <span class="reply-state">reply_failed</span>
    </li>
  </ul>
</li>
```

The renderer's filter pills, search, and status toggles operate over these elements; the `data-*` contract makes structural tests addressable without text-matching. The stable-tier vs advisory-tier split is in §3.6.
