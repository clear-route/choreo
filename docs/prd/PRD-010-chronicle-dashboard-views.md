# Chronicle Dashboard Views — Data Consumption Patterns for Longitudinal Test Analytics

**Status:** Draft (v2 — post-review)
**Created:** 2026-04-19
**Last Updated:** 2026-04-19 (v2)
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering, Consumer Teams

---

## Revision note (v2 vs. v1)

v2 incorporates a three-actor parallel review (architecture, code quality,
performance). Material changes from v1:

- **Run Detail data source.** Corrected: the test file tree and all
  scenario/handle/timeline detail render entirely from the raw JSON
  (`GET /runs/{id}/raw`). The structured `RunDetail` provides hero card
  and metadata only — it does not carry file paths or handle data. The
  raw JSON is fetched once on view mount and cached client-side with
  `staleTime: Infinity`.
- **"Compare to previous" link.** Removed. Run comparison is explicitly
  out of scope. Replaced with a "Filter by branch" action that narrows
  the Run Summary list to show recent history on the same branch.
- **Regression endpoint response shape.** Changed from flat cells array
  to indexed matrix (topic index x bucket index → value) to reduce
  payload size by ~60%.
- **Missing schema fields.** Added `pass_rate`, `topic_count`, and
  `p50_ms`/`p95_ms`/`p99_ms` to `RunSummary` as required schema changes.
  Added `run` filter to `GET /anomalies`.
- **Anomaly Feed filters.** Added severity, detection method, and topic
  filters to match PRD-009 specification.
- **Sparkline window.** Clarified: over-fetch by 20 runs (request
  limit=70, display 50) to ensure sparklines at page boundaries have
  sufficient history.
- **View count.** Explicitly noted that PRD-010 extends PRD-009's
  original four views to six (adding Run Detail and Topic List).
- **Acceptance criteria and testing strategy.** Added per-view acceptance
  criteria and frontend test expectations.

---

## Executive Summary

PRD-009 established Chronicle as a reporting server that ingests
`test-report-v1` JSON and stores it in TimescaleDB. ADR-0021 defined the
backend structure. ADR-0022 defined the frontend architecture. The
dashboard shell is built — navigation, filter bars, design system, empty
states — but the views are hollow. They have no data visualisation because
the question "what should each view actually show, and to whom?" was
deferred.

This PRD answers that question. It defines three consumer personas, maps
their questions to specific views, specifies the data each view renders
and how, and establishes the contract between the API response shapes and
the frontend components.

Chronicle is not a general dashboarding tool. It answers a narrow set of
questions that no existing tool answers:

- Is this topic getting slower?
- Did that deploy break anything?
- Are we still within budget?
- Is the bus healthy right now?

---

## Consumer Personas

### Persona 1: The Developer (post-deploy)

**Context:** A developer on a consumer team has just merged a PR and
deployed to staging. Their CI pipeline ran Choreo tests and POSTed the
results to Chronicle. They want to know: *did my change break anything,
and if so, what?*

**Frequency:** Multiple times per day, triggered by deploy.

**Questions:**
1. Did this run pass? If not, which topics failed?
2. Is anything slower than before? Which handles took longer than usual?
3. Did I introduce any budget violations?
4. How does this run compare to the last few runs on this branch?

**Tolerance for complexity:** Low. The developer wants a traffic-light
answer in 5 seconds, then the option to drill down. They will not
configure dashboards, set thresholds, or read documentation to interpret
a chart. If the answer is not obvious on first glance, they will go back
to reading the HTML report.

**Key insight:** This persona consumes Chronicle as a *run viewer* — one
run at a time, with lightweight comparison to recent history.

### Persona 2: The QA / Release Engineer (cross-run analysis)

**Context:** A QA engineer is preparing a release from staging to
production. They need to assess the health of the message bus
infrastructure across the last sprint. They look at trends, not
individual runs.

**Frequency:** Weekly to fortnightly, before release milestones.

**Questions:**
1. Which topics have regressed over the last 2 weeks?
2. What is the p95 trend for our critical topics?
3. Are budget violations increasing or stable?
4. Which environments are healthy? Which are degraded?
5. Are there any open anomalies I should investigate before release?

**Tolerance for complexity:** Medium. This persona will use time range
pickers, environment filters, and read percentile charts. They will not
write queries or configure detection thresholds.

**Key insight:** This persona consumes Chronicle as a *trend analyser* —
comparing performance across time, looking for regressions and drift.

### Persona 3: The Platform Engineer (continuous monitoring)

**Context:** A platform engineer has configured Choreo to run synthetic
probes every 5 minutes against a standing environment (staging, pre-prod).
Chronicle is their synthetic monitoring dashboard. They check it
throughout the day and investigate when something looks off.

**Frequency:** Continuous — the dashboard is open in a browser tab,
receiving SSE updates.

**Questions:**
1. Is the bus healthy right now?
2. Has anything changed in the last hour?
3. When did latency for topic X start increasing?
4. Which topics are consistently slow vs intermittently slow?
5. Is the anomaly I saw earlier still active, or has it resolved?

**Tolerance for complexity:** High. This persona will use all four views,
cross-reference anomalies with topic drilldowns, and use the regression
heatmap as their primary health signal. They are the power user.

**Key insight:** This persona consumes Chronicle as a *live health
monitor* — they want the most recent data, pushed to them via SSE, with
the ability to investigate historical context when something looks wrong.

---

## View Specifications

### View 1: Run Summary (Landing Page)

**Primary persona:** Developer (post-deploy)
**Secondary persona:** QA engineer (scanning recent history)

**Purpose:** Answer "what happened?" at the run level. Show recent runs
with enough context to spot problems without clicking into detail.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  CHRONICLE   Runs   Topics   Regression   Anomalies             │
├─────────────────────────────────────────────────────────────────┤
│  Runs                                                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ [Select tenant v]  [Filter by environment...]             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Hero Card ────────────────────────────┐  │
│  │  37 of 37 tests passed                        2.18s       │  │
│  │  ████████████████████████████████████████████  (green bar) │  │
│  │  37 tests  ✓ 37 passed  ·  p50 9ms  p95 201ms  ·  today  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Run Table ────────────────────────────┐  │
│  │  STARTED         BRANCH   PASS  FAIL  SLOW  TOPICS  TREND │  │
│  │  19 Apr 09:44    main     110   0     0     68      ~~~~  │  │
│  │  19 Apr 06:00    main     37    0     0     0       ~~~~  │  │
│  │  18 Apr 15:22    feat/x   108   2     1     65      ~~~~  │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Hero Card (latest run summary)

The top of the page shows the most recent run for the selected tenant
and environment, styled identically to the OG HTML report header:

| Element | Data source | Format |
|---------|-------------|--------|
| Headline | `{passed} of {total} tests passed` | Same sentence structure as the OG report. Highlight count in green (all pass), amber (some slow), or red (any fail). |
| Proportion bar | `totals.passed`, `totals.slow`, `totals.failed`, `totals.errored`, `totals.skipped` | 6px horizontal bar, same colour segments as OG report |
| Sub-line | `total`, `passed`, `p50_ms`, `p95_ms`, `p99_ms`, `started_at` | Monospace, muted, tabular-nums. Percentile fields are new additions to `RunSummary` (see §Schema Changes). Omitted if null (run has no handle measurements). |
| Duration | `duration_ms` | Right-aligned, monospace |

This card is **not** a new component — it is the OG report header,
transplanted into Chronicle. The developer sees the same visual they
already recognise from the HTML report, now in the context of a run list.

**API source:** `GET /api/v1/runs?tenant={slug}&environment={env}&limit=1`
returns the latest run. The hero card renders `items[0]`.

#### Run Table

Below the hero card, a paginated table of runs, newest first:

| Column | Data source | Purpose |
|--------|-------------|---------|
| **Started** | `started_at` | Primary sort key. Formatted as `DD Mon HH:MM`. Links to Run Detail. |
| **Branch** | `branch` | Identifies which code produced this run. Monospace. |
| **Pass / Fail / Slow** | `total_passed`, `total_failed`, `total_slow` | Traffic-light numbers. Green for pass, red for fail, amber for slow. Zero values are muted. |
| **Topics** | `topic_count` | Shows test breadth. A run with 2 topics vs 68 topics tells you how comprehensive it was. New field on `RunSummary` (see §Schema Changes). |
| **Trend** | Sparkline of pass rate (last 20 runs on this branch+env) | Shows whether this run's pass rate is normal or anomalous. Uses `pass_rate` field (see §Schema Changes). |
| **Anomalies** | `anomaly_count` | Badge if > 0. Links to anomaly feed filtered for this run (`/anomalies?run={runId}`). |

**Sparkline data:** The sparkline shows the pass rate of the last 20 runs
on the same tenant + environment + branch. The client over-fetches by 20
(requests `limit=70`, displays 50) so that runs near the page boundary
have sufficient history for their sparkline. The `pass_rate` field on
`RunSummary` avoids recomputation.

**Decision:** Include `pass_rate` (float, 0-1) in the `RunSummary`
response. The sparkline is computed client-side from the fetched run list.
No new endpoint needed.

#### Interaction

- **Click a run row** → navigate to `/runs/{runId}` (Run Detail)
- **Branch column click** → filter the run list by that branch
- **Anomaly badge click** → navigate to `/anomalies?run={runId}`

---

### View 2: Run Detail

**Primary persona:** Developer (debugging a specific run)
**Secondary persona:** QA engineer (investigating a flagged regression)

**Purpose:** Answer "what exactly happened in this run?" Show the same
information as the OG HTML report, but within Chronicle's navigation and
with links to longitudinal context.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back to Runs                                                 │
│  Run Detail                                                     │
│                                                                 │
│  ┌──────────────────── Hero Card ────────────────────────────┐  │
│  │  110 of 110 tests passed                      43.34s      │  │
│  │  ████████████████████████████████████████████             │  │
│  │  110 tests  ✓ 110 passed  ·  p50 9ms  p95 1002ms         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Metadata Grid ────────────────────────┐  │
│  │  STARTED       19 Apr 09:44     BRANCH     main           │  │
│  │  DURATION      43.34s           GIT SHA    cda9c43d       │  │
│  │  ENVIRONMENT   staging          TRANSPORT  NatsTransport  │  │
│  │  PROJECT       choreo           HOSTNAME   runner-vm      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Test List ────────────────────────────┐  │
│  │  ▶ test_transport_contract.py              44 tests       │  │
│  │  ▶ test_e2e_nats_edge_cases.py             22 tests       │  │
│  │  ▼ test_e2e_kafka_edge_cases.py            12 tests       │  │
│  │    ✓ test_silent_topic_should_timeout       751ms TIMEOUT │  │
│  │    ✓ test_publish_and_receive_roundtrip     12ms  PASS    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Scenario Detail ──────────────────────┐  │
│  │  scenario silent_topic                                    │  │
│  │  ┌─ Handle: e2e.kafka.silent.abc123 ─────────────────┐   │  │
│  │  │  ● TIMEOUT   751ms   matcher: any_message()       │   │  │
│  │  │  diagnosis: silent_timeout — no message arrived    │   │  │
│  │  └───────────────────────────────────────────────────┘   │  │
│  │                                                           │  │
│  │  TIMELINE (2 events)                                      │  │
│  │  ┌ 251ms  published  e2e.kafka.silent.abc123            │  │
│  │  └ 751ms  deadline   e2e.kafka.silent.abc123  no msg    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Content Sections

**1. Hero Card** — identical to the Run Summary hero card, but for this
specific run.

**2. Metadata Grid** — the same stat grid we have now, matching the OG
report's "about" popover content. Two columns on mobile, four on desktop.

**3. Test File Tree** — collapsible file-grouped test list, matching the
OG report's left panel tree structure:

| Element | Data source | Notes |
|---------|-------------|-------|
| File header | Test `nodeid`, split into directory + filename | Monospace, bold. Shows test count per file. Collapsed by default. |
| Test row | Test `name`, `outcome`, `duration_ms` | Outcome badge (pass/fail/slow/timeout). Duration in monospace. Click to expand scenario detail. |
| Skip indicator | `skip_reason` | Shown inline if the test was skipped. |

**4. Scenario Detail** — expands below a test when clicked. Shows the
full Choreo scenario data:

| Element | Data source | Notes |
|---------|-------------|-------|
| Scenario header | `scenario.name`, `scenario.outcome`, `scenario.duration_ms` | Badge + duration. Collapsible. |
| Handle rows | Per-handle `topic`, `outcome`, `latency_ms`, `budget_ms`, `diagnosis.kind`, `matcher_description` | One row per handle. Topic as a chip (matching OG report's `.hr-topic` style). Outcome badge. Latency in monospace. |
| Budget bar | `latency_ms` vs `budget_ms` | Shown only for handles with a budget. Green fill up to budget, amber fill for overshoot. Matches OG report's `.hr-budget-bar`. |
| Diagnosis note | `diagnosis.kind`, `reason` | Coloured note: green for `matched`, red for `silent_timeout` or `near_miss`, amber for `over_budget`. |
| Expected / Actual | `expected`, `actual` | Side-by-side panels for pass case. Structural diff highlighting for fail case. Matches OG report's `.hr-diff` layout. |
| Timeline | `scenario.timeline[]` | Waterfall chart showing event sequence: published → matched/deadline, with offset_ms timestamps. Matches OG report's waterfall. |

**Data source:** The Run Detail view uses **two API calls**:

1. `GET /api/v1/runs/{run_id}` — returns `RunDetail` with hero card
   fields (totals, duration, metadata) and a flat list of
   `ScenarioSummary` objects. Used for the hero card and metadata grid.
2. `GET /api/v1/runs/{run_id}/raw` — returns the original
   `test-report-v1` JSON. Used for the test file tree, scenario detail,
   handle rows, expected/actual diffs, and timeline. Fetched once on
   view mount and cached client-side with `staleTime: Infinity` (the
   raw report is immutable once ingested).

The structured `RunDetail` does **not** carry file paths, handle data,
or timeline entries — these are only in the raw JSON. The test file
tree groups tests by `test.file` from the raw report, not from the
structured response. This split is correct because the structured
response is optimised for list views (small payload, no nested data),
while the raw JSON is the full record for drill-down.

**Key design decision:** The Run Detail view replicates the OG HTML
report's interaction model — collapsed test tree, expand to see scenario
detail, expand further to see handles and timeline. This is deliberate:
the developer already knows how to read the HTML report. Chronicle adds
the surrounding context (nav to trends, anomaly links) without changing
the core reading experience.

#### Contextual Links (Chronicle adds these; the HTML report cannot)

- **Topic chip → Topic Drilldown:** clicking a topic name in a handle
  row navigates to `/topics/{topic}` with the current time range. The
  developer can see whether this topic's latency is normal or anomalous.
- **Anomaly badge → Anomaly Feed:** if the run triggered anomalies,
  a link at the top navigates to the anomaly feed filtered for this run
  (`/anomalies?tenant={slug}&run={runId}`).
- **"Filter by branch" link:** navigates to the Run Summary view
  pre-filtered by this run's branch (`/runs?tenant={slug}&branch={branch}`),
  showing recent history on the same branch for lightweight comparison.

---

### View 3: Topic Drilldown

**Primary persona:** QA / Release Engineer (trend analysis)
**Secondary persona:** Platform Engineer (investigating a specific topic)

**Purpose:** Answer "is this topic getting worse?" Show per-topic latency
trends, outcome distribution, and budget compliance over time.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back to Topics                                               │
│  Topic Drilldown                                                │
│  orders.created                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ [tenant v] [env] | [24h] [7d] [30d] [90d] | [from] [to] │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────── Topic Summary Card ───────────────────┐  │
│  │  p50: 9ms     p95: 84ms     p99: 201ms     12 runs       │  │
│  │  Outcome: 98% pass  1% slow  1% timeout                  │  │
│  │  Budget: 3 violations in 12 runs (25%)                    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  LATENCY TREND                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  [p50/p95/p99 line chart with run markers on x-axis]      │  │
│  │  Click a marker to view that run's detail                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  OUTCOME DISTRIBUTION                                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  [stacked bar chart: pass/slow/timeout/fail per bucket]   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  BUDGET COMPLIANCE (if any handles on this topic use budgets)   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  [line chart: budget violation % over time]               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  RECENT RUNS WITH THIS TOPIC                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  STARTED     ENV      p50    p95    p99    SLOW  TIMEOUT  │  │
│  │  19 Apr      staging  9ms    84ms   201ms  0     0        │  │
│  │  18 Apr      staging  8ms    79ms   190ms  0     1        │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Content Sections

**1. Topic Summary Card** — aggregate stats for the selected time range:

| Metric | Source | Format |
|--------|--------|--------|
| p50 / p95 / p99 | Computed from latency buckets (latest bucket or aggregate) | Monospace, coloured by threshold: green < 100ms, amber < 500ms, red > 500ms (configurable per topic in future) |
| Run count | Count of runs containing this topic in the range | Provides sample size context |
| Outcome breakdown | Percentage of each outcome across all handles | "98% pass, 1% slow, 1% timeout" |
| Budget violations | Count and percentage of `over_budget` handles | Only shown if any handle on this topic has a budget |

**2. Latency Trend Chart** (Recharts `LineChart`) — three lines
(p50/p95/p99) over time:

| Property | Value |
|----------|-------|
| X-axis | Time buckets (auto-resolution: raw for 24h, hourly for 7-30d, daily for 30-90d) |
| Y-axis | Latency in ms |
| Lines | p50 (blue), p95 (amber), p99 (red) — matching design tokens |
| Run markers | Vertical dotted lines or dots on the x-axis for each run's `started_at`. Click to navigate to Run Detail. |
| Tooltip | Shows bucket time, p50/p95/p99 values, sample count |

**API source:** `GET /api/v1/topics/{topic}/latency?tenant=...&from=...&to=...`
returns `TopicLatencyResponse` with `buckets: LatencyBucket[]`.

**3. Outcome Distribution Chart** (Recharts stacked `BarChart`) —
pass/slow/timeout/fail counts per time bucket:

| Property | Value |
|----------|-------|
| X-axis | Same time buckets as latency chart |
| Y-axis | Count |
| Stacks | pass (green), slow (amber), timeout (purple), fail (red) |
| Tooltip | Shows counts and percentages per outcome |

**API source:** Same `LatencyBucket` response — it already includes
`slow_count`, `timeout_count`, `fail_count`.

**4. Budget Compliance Chart** (Recharts `LineChart`) — shown only when
the topic has handles with `budget_ms`:

| Property | Value |
|----------|-------|
| X-axis | Time buckets |
| Y-axis | Budget violation percentage (0-100%) |
| Line | Single line showing `budget_violation_count / sample_count * 100` |
| Threshold | Horizontal dashed line at the configured threshold (default 20%) |

**API source:** Same `LatencyBucket` — `budget_violation_count` and
`sample_count` are already present.

**5. Recent Runs Table** — the last N runs that included handles on this
topic:

**API source:** `GET /api/v1/topics/{topic}/runs?tenant=...&limit=20`
returns `TopicRunSummary[]`.

---

### View 4: Regression Timeline

**Primary persona:** Platform Engineer (continuous monitoring)
**Secondary persona:** QA / Release Engineer (pre-release health check)

**Purpose:** Answer "which topics are getting worse?" Show a cross-topic
comparison of regression magnitude, with a heatmap for temporal context.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Regression Timeline                                            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ [tenant v] [env] | [24h] [7d] [30d] [90d] | [from] [to] │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  HEATMAP                                                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  topic-a      ■■■■■□□■■■■■  (green to yellow to red)     │  │
│  │  topic-b      ■■□□□□■■■■□□                               │  │
│  │  topic-c      ■■■■■■■■■□□□                               │  │
│  │               |  |  |  |  |                               │  │
│  │              Mon Tue Wed Thu Fri                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  TOP REGRESSIONS                                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  TOPIC           CURRENT p95  BASELINE p95  DELTA    Δ%   │  │
│  │  orders.created  84ms         52ms          +32ms   +62%  │  │
│  │  fills.matched   201ms        180ms         +21ms   +12%  │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Content Sections

**1. Regression Heatmap** (custom Canvas component) — topics on y-axis,
time buckets on x-axis, colour by p95 latency:

| Property | Value |
|----------|-------|
| Y-axis | Topics, sorted by highest current p95 (worst first). Limited to top 100. |
| X-axis | Time buckets (daily for 30-90d, hourly for 7d, per-run for 24h) |
| Cell colour | Green → yellow → red gradient based on p95 value normalised across the visible data range |
| Null cells | Grey — no data for this topic in this bucket |
| Hover | Tooltip showing topic, bucket, p95 value |
| Click | Navigate to Topic Drilldown for that topic |

**Data source:** This requires a **new API endpoint**:

```
GET /api/v1/regression?tenant={slug}&environment={env}&from={iso}&to={iso}
```

Returns an indexed matrix (not a flat cell array) to minimise payload:

```json
{
  "topics": ["orders.created", "fills.matched"],
  "buckets": ["2026-04-01T00:00:00Z", "2026-04-02T00:00:00Z"],
  "matrix": [[52.3, 84.1], [180.0, 201.0]],
  "regressions": [
    {
      "topic": "orders.created",
      "current_p95": 84.1,
      "baseline_p95": 52.3,
      "delta_ms": 31.8,
      "change_pct": 60.8,
      "sample_count": 24
    }
  ]
}
```

`matrix[i][j]` is the p95 latency for `topics[i]` at `buckets[j]`.
`null` values indicate no data for that cell. This indexed
representation reduces payload by ~60% compared to a flat cell array
(no repeated topic/bucket strings).

**NFR target:** `GET /regression` must respond in < 500ms (p99) for
100 topics x 90 daily buckets. The query runs against the
`topic_latency_daily` continuous aggregate, not raw handle measurements.
Server-side caching with 30-second TTL, invalidated on SSE
`run.completed` events.

This is the only new endpoint required. It pre-computes the heatmap data
and regression rankings server-side to avoid transferring the full
per-topic latency data for 100 topics to the client.

**2. Regression Table** — topics ranked by largest p95 regression:

| Column | Source | Format |
|--------|--------|--------|
| Topic | `regression.topic` | Monospace, links to Topic Drilldown |
| Current p95 | `regression.current_p95` | Monospace. Last bucket's p95. |
| Baseline p95 | `regression.baseline_p95` | Monospace. Mean of preceding N buckets. |
| Delta | `regression.delta_ms` | Signed, coloured: red if positive (regression), green if negative (improvement) |
| Change % | `regression.change_pct` | Percentage change from baseline |
| Sample count | `regression.sample_count` | Shows data confidence |

---

### View 5: Anomaly Feed

**Primary persona:** Platform Engineer (continuous monitoring)
**Secondary persona:** QA / Release Engineer (pre-release checklist)

**Purpose:** Answer "is anything wrong right now?" Show detected anomalies
in chronological order, with enough context to decide whether to
investigate.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Anomalies                                                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ [tenant v] | [severity v] [method v] [topic] [☑ resolved]│  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────── Anomaly Card ─────────────────────────────┐  │
│  │  orders.created  ● CRITICAL  Baseline                     │  │
│  │  p95_ms: 84.1 vs baseline 52.3 (+60.8%)                  │  │
│  │                                         19 Apr 09:44      │  │
│  │  [View run]  [Topic drilldown]                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────── Anomaly Card (resolved) ──────────────────┐  │
│  │  fills.matched  ○ WARNING  Budget            (greyed out) │  │
│  │  budget_violation_pct: 25.0 vs baseline 8.2 (+205%)       │  │
│  │  Resolved 18 Apr 16:00                   18 Apr 14:30     │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Anomaly Card Content

Each anomaly card shows:

| Element | Source | Purpose |
|---------|--------|---------|
| Topic | `anomaly.topic` | Monospace chip. Links to Topic Drilldown. |
| Severity badge | `anomaly.severity` | `warning` (amber) or `critical` (red). Solid fill, white text. |
| Detection method | `anomaly.detection_method` | "Baseline", "Budget", "Outcome" — human label for the method. |
| Metric and values | `anomaly.metric`, `current_value`, `baseline_value`, `change_pct` | The core signal: "p95_ms: 84.1 vs baseline 52.3 (+60.8%)". |
| Timestamp | `anomaly.detected_at` | When the anomaly was detected. |
| Resolution | `anomaly.resolved`, `anomaly.resolved_at` | If resolved: "Resolved {date}". Card is greyed out. |
| Actions | Links | "View run" → Run Detail. "Topic drilldown" → Topic Drilldown. |

**SSE integration:** When an `anomaly.detected` event arrives, the new
anomaly card appears at the top of the feed with a brief highlight
animation (CSS transition on `background-color`). The user does not need
to refresh.

---

### View 6: Topic List (index for Topics nav)

**Primary persona:** All three personas (navigation hub)

**Purpose:** Answer "what topics exist and how are they performing?" This
is the gateway to Topic Drilldown — a searchable, sortable table of all
topics with their current stats.

#### Layout

A flat table of all topics seen for the selected tenant:

| Column | Source | Purpose |
|--------|--------|---------|
| Topic | `topic` | Monospace. Links to Topic Drilldown. |
| p50 / p95 / p99 | Latest bucket values | Quick health check |
| Runs | `run_count` | How many runs included this topic |
| Slow / Timeouts | `slow_count`, `timeout_count` | Non-pass outcome counts |
| Last seen | `latest_run_at` | Staleness indicator |

**Searchable:** A text input filters the topic list client-side. With 68
topics in the real data, client-side search is sufficient.

---

## Data Contract: API → Frontend Component Mapping

| Frontend Component | API Endpoint | Response Type | Notes |
|-------------------|-------------|---------------|-------|
| Hero Card | `GET /runs?limit=1` | `RunSummary` | Latest run for hero; needs `pass_rate` field added |
| Run Table | `GET /runs?limit=50&offset=N` | `PagedResponse<RunSummary>` | Sparkline computed from `pass_rate` across visible runs |
| Run Detail Hero | `GET /runs/{id}` | `RunDetail` | Same hero card layout |
| Run Detail Metadata | `GET /runs/{id}` | `RunDetail` | Stat grid |
| Test File Tree | `GET /runs/{id}/raw` | Raw JSON | Grouped by `test.file` |
| Scenario Detail | `GET /runs/{id}/raw` | Raw JSON | Handles, timeline, expected/actual from raw report |
| Topic Summary Card | `GET /topics/{topic}/latency` | `TopicLatencyResponse` | Computed from latest bucket |
| Latency Line Chart | `GET /topics/{topic}/latency` | `TopicLatencyResponse` | `buckets[]` → Recharts `LineChart` |
| Outcome Bar Chart | `GET /topics/{topic}/latency` | `TopicLatencyResponse` | `buckets[]` → Recharts `BarChart` |
| Budget Compliance | `GET /topics/{topic}/latency` | `TopicLatencyResponse` | `budget_violation_count / sample_count` |
| Topic Runs Table | `GET /topics/{topic}/runs` | `PagedResponse<TopicRunSummary>` | |
| Regression Heatmap | `GET /regression` | **New endpoint** | Pre-computed heatmap + regression data |
| Regression Table | `GET /regression` | **New endpoint** | Ranked by regression magnitude |
| Topic List Table | `GET /topics` | `PagedResponse<TopicSummary>` | |
| Anomaly Card | `GET /anomalies` | `PagedResponse<AnomalyCard>` | |

### Schema Changes Required

These are additive changes to existing Pydantic schemas. No breaking
changes; no database migrations.

**`RunSummary`** (in `schemas/runs.py`) — add:

| Field | Type | Source | Purpose |
|-------|------|--------|---------|
| `pass_rate` | `float` | `total_passed / total_tests` | Sparkline data for Run Table trend column |
| `topic_count` | `int` | Count of distinct topics from handle measurements | Run Table breadth indicator |
| `p50_ms` | `float \| None` | Aggregate p50 of handle latencies for this run | Hero Card sub-line |
| `p95_ms` | `float \| None` | Aggregate p95 | Hero Card sub-line |
| `p99_ms` | `float \| None` | Aggregate p99 | Hero Card sub-line |

**`GET /api/v1/anomalies`** — add query parameter:

| Parameter | Type | Purpose |
|-----------|------|---------|
| `run` | `UUID \| None` | Filter anomalies by triggering run. Used by Run Detail → Anomaly Feed link. |
| `severity` | `str \| None` | Filter by `warning` or `critical` |
| `method` | `str \| None` | Filter by detection method |
| `topic` | `str \| None` | Filter by topic |

### New API Endpoints Required

**`GET /api/v1/regression`** — returns indexed heatmap matrix and
regression rankings. The only new endpoint in PRD-010. See View 4 for
response shape and NFR target.

### Existing Endpoints Sufficient For

- All Topic Drilldown charts (latency, outcome, budget)
- Run Detail (structured + raw)
- Anomaly Feed (with the filter additions above)
- Topic List
- Run Summary table

---

## Scope Note

PRD-009 specifies "four core views: run summary, topic drilldown,
regression timeline, and anomaly feed." PRD-010 extends this to **six
views** by adding:

- **Run Detail** — the drill-down from Run Summary into a specific run.
  PRD-009 implied this via "Click a run to see full detail" (§UI Views,
  Run Summary) but did not enumerate it as a separate view.
- **Topic List** — the index page for the Topics nav tab. PRD-009
  implied this via "List all topics seen" (`GET /topics` endpoint) but
  did not enumerate it as a view.

These additions are consistent with PRD-009's intent. No scope creep.

---

## Decisions Already Made

| # | Decision | Why |
|---|----------|-----|
| 1 | **Hero card mirrors the OG report header** | The developer already knows how to read the HTML report. Chronicle adds context around the same visual, not a new visual to learn. |
| 2 | **Run Detail renders the test file tree entirely from raw JSON** | The structured `RunDetail` response provides hero card and metadata only. The raw JSON (`GET /runs/{id}/raw`) is fetched once on view mount, cached with `staleTime: Infinity`, and used for the file tree, scenario detail, handles, diffs, and timelines. This avoids duplicating the full report structure in the Pydantic schema. |
| 3 | **Sparkline data is computed client-side from the run list** | Adding a `pass_rate` field to `RunSummary` is simpler than a new sparkline endpoint. The client computes a rolling window from the visible page of runs. |
| 4 | **Regression heatmap requires a new server-side endpoint** | Sending per-topic latency data for 100 topics to the client and computing the heatmap matrix client-side would transfer ~100x more data than needed. The server pre-computes the matrix. |
| 5 | **Budget compliance is a conditional chart** | Only shown when the selected topic has handles with `budget_ms`. Many topics do not use budgets; showing an empty chart wastes space. |
| 6 | **Topic list is client-side searchable** | With < 200 topics in the target scale, client-side filtering is faster than server round-trips. If topic count exceeds 500, add server-side search. |

---

## Out of Scope (v1)

- Run comparison mode (side-by-side diff of two runs)
- Custom anomaly threshold configuration via UI
- Topic grouping or tagging
- Exportable charts (PNG, CSV)
- Shareable chart links (deep-link to a specific chart state)
- Time-synchronised charts (linked zoom/pan across multiple charts)
- Run-level latency distribution histograms
- Handle-level trend charts (per-handle over time, not per-topic)

---

## Acceptance Criteria

### View 1: Run Summary

- [ ] Hero card renders the latest run's headline, proportion bar, sub-line, and duration within 2 seconds of page load.
- [ ] Hero card headline uses the OG report sentence structure: `{N} of {total} tests passed`.
- [ ] Run Table shows columns: Started, Branch, Pass, Fail, Slow, Topics, Trend, Anomalies.
- [ ] Sparkline renders the pass rate of the preceding 20 runs in the list.
- [ ] Clicking a run row navigates to `/runs/{runId}`.
- [ ] SSE `run.completed` event adds the new run to the top of the table within 2 seconds.

### View 2: Run Detail

- [ ] Hero card and metadata grid render from the structured `RunDetail` response.
- [ ] Test file tree renders from the raw JSON, grouped by `test.file`, collapsed by default.
- [ ] Expanding a test shows scenario detail with handles, outcome badges, and latency.
- [ ] Handles with budgets show a budget bar (green fill + amber overshoot).
- [ ] Topic chips link to `/topics/{topic}`.
- [ ] Raw JSON is fetched once and cached — expanding multiple tests does not trigger additional API calls.

### View 3: Topic Drilldown

- [ ] Latency chart renders p50/p95/p99 lines for a 30-day range within 1 second.
- [ ] Outcome chart renders stacked bars per time bucket.
- [ ] Budget compliance chart is shown only when the topic has handles with `budget_ms`.
- [ ] Time range picker changes update all charts without full page reload.

### View 4: Regression Timeline

- [ ] Heatmap renders 100 topics x 90 buckets on Canvas within 500ms.
- [ ] Regression table shows topics sorted by largest p95 increase.
- [ ] Clicking a heatmap cell or table row navigates to the Topic Drilldown.

### View 5: Anomaly Feed

- [ ] Anomaly cards show topic, severity badge, metric values, and detection method.
- [ ] Resolved anomalies are visually distinguished (greyed out).
- [ ] SSE `anomaly.detected` event adds a new card at the top with a highlight animation.
- [ ] Filters for severity, detection method, topic, and resolved status work correctly.

### View 6: Topic List

- [ ] Table shows all topics with p50/p95/p99, run count, slow/timeout counts.
- [ ] Client-side search filters the topic list as the user types.
- [ ] Clicking a topic navigates to the Topic Drilldown.

---

## Testing Strategy

### Frontend tests (Vitest + React Testing Library)

- Each view component renders correctly with mock API data.
- Hero card formats the headline sentence correctly for all-pass, some-fail, and some-slow cases.
- Sparkline computes the correct rolling window from a list of runs.
- Budget bar renders proportional fill for within-budget and over-budget handles.
- Time range picker updates URL params and triggers React Query refetch.
- SSE hook invalidates the correct query keys on `run.completed` and `anomaly.detected` events.
- No component uses `dangerouslySetInnerHTML` (ESLint rule `react/no-danger`).

### Integration tests (Playwright)

- Full ingest-to-dashboard flow: POST a report, verify the run appears in the Run Summary table.
- Run Detail: ingest a report with scenarios and handles, navigate to the run, expand a test, verify handle data renders.
- Topic Drilldown: ingest multiple reports, navigate to a topic, verify the latency chart renders data points.
- Anomaly Feed: ingest reports that trigger anomaly detection, verify the anomaly card appears.

---

## Related PRDs / ADRs

- [PRD-007 — Test Report Output](PRD-007-test-report-output.md) — defines `test-report-v1` JSON schema and the OG HTML report
- [PRD-009 — Chronicle Reporting Server](PRD-009-chronicle-reporting-server.md) — data model, API surface, anomaly detection
- [ADR-0021 — Chronicle API Structure](../adr/0021-chronicle-api-structure.md) — backend architecture
- [ADR-0022 — Chronicle Frontend Architecture](../adr/0022-chronicle-frontend-architecture.md) — frontend architecture, charting, state management
