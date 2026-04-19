# 0026. Chronicle Topic Drilldown Frontend — Component Architecture and Stateless Data Flow

**Status:** Proposed
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-010 — Chronicle Dashboard Views](../prd/PRD-010-chronicle-dashboard-views.md), View 3; [ADR-0022 — Chronicle Frontend Architecture](0022-chronicle-frontend-architecture.md); [ADR-0025 — Topic Drilldown Backend](0025-chronicle-topic-drilldown-backend.md)

---

## Context

The Topic Drilldown route (`routes/TopicDrilldown.tsx`) is partially
built. It reads URL state, calls `useTopicLatency`, and renders
`LatencyLineChart` and `OutcomeBarChart` from the same `data.buckets`
array. What exists works correctly for two of the five content sections
specified by PRD-010 View 3.

What exists:

- Route component (70 lines) with URL state hooks, filter bar, time
  range picker, and loading/error/empty states.
- `useTopicLatency` hook — React Query wrapper around
  `GET /topics/{topic}/latency`. Returns `TopicLatencyResponse` with
  `buckets: LatencyBucket[]`.
- `LatencyLineChart` — Recharts `LineChart` rendering p50/p95/p99 from
  `LatencyBucket[]`.
- `OutcomeBarChart` — Recharts stacked `BarChart` rendering
  pass/slow/timeout/fail from `LatencyBucket[]`.

What is missing:

- **TopicSummaryCard** — aggregate stats (p50/p95/p99, outcome breakdown,
  budget violations) for the selected time range.
- **BudgetComplianceChart** — line chart of budget violation percentage
  over time. Only shown when the topic has handles with budgets.
- **TopicRunsTable** — recent runs that included this topic, with per-run
  stats. Requires a new hook (`useTopicRuns`).
- **Transform layer** — `transforms/topicDrilldown.ts` for pure
  functions converting API data to component props.

### Problem Statement

How should the three missing content sections be added to the Topic
Drilldown view so that the route remains a thin composition layer,
components are stateless, data transformation is testable without
rendering, and the conditional budget chart does not introduce business
logic into the route?

### Goals

- **Dumb components.** Every new component receives fully transformed
  data via props. No API calls, no URL state reads, no computation.
- **Testable transforms.** The logic that decides whether budget data
  exists, computes summary stats from buckets, and formats run rows is
  in pure functions in `transforms/topicDrilldown.ts`.
- **Thin route.** The route component stays under 60 lines after adding
  three new sections. It calls hooks, calls transforms, and passes
  props.
- **Single data source for charts.** The latency chart, outcome chart,
  and budget chart all render from the same `data.buckets` array
  returned by `useTopicLatency`. No additional API call.
- **Separate data source for runs table.** The runs table requires
  per-run stats that are not in the latency buckets. This is a separate
  API call (`GET /topics/{topic}/runs`) via a new `useTopicRuns` hook.

### Non-Goals

- Backend changes — ADR-0025 covers the query implementation.
- Topic List (View 6) — separate scope, though it shares some components.
- Run markers on the latency chart x-axis — deferred to a follow-up.
- SSE-driven chart refresh — already handled by `useSSE` cache
  invalidation (ADR-0022).

---

## Decision Drivers

- **Three charts from one API call.** `useTopicLatency` returns
  `LatencyBucket[]` containing all fields needed by the latency chart,
  outcome chart, and budget chart. The hook is called once; the route
  passes the same `data.buckets` to three components. React Query
  deduplicates the request if multiple components call the same hook
  with the same key — but in this architecture, only the route calls
  the hook. Components never call hooks.
- **Conditional rendering of the budget chart.** PRD-010 specifies: "Budget
  compliance chart is shown only when the topic has handles with
  `budget_ms`." The `LatencyBucket` includes `budget_violation_count`.
  If every bucket has `budget_violation_count === 0`, the chart adds no
  information. The question is where this "has budget data" check lives:
  in the route (inline), or in a transform function.
- **The TopicSummaryCard computes from buckets, not from the API.** The
  API returns time-series buckets, not pre-computed aggregate stats.
  The summary card's "p50: 9ms, p95: 84ms, p99: 201ms" comes from the
  latest bucket. The "outcome breakdown" and "budget violations" are
  computed across all buckets. These are derived values, not API fields.
- **The runs table has a different data shape.** `TopicRunSummary` has
  per-run percentiles, unlike `LatencyBucket` which has per-bucket
  percentiles. This data comes from a different endpoint and belongs in
  a separate hook.

---

## Considered Options

### Decision 1: TopicSummaryCard data source

#### Option A: Compute from `LatencyBucket[]` in a transform (chosen)

The latest bucket provides p50/p95/p99. All buckets provide the outcome
breakdown (sum of slow/timeout/fail counts vs total sample count) and
budget violation stats (sum of violations vs total samples, count of
buckets with violations).

```typescript
// transforms/topicDrilldown.ts

export function toSummaryCardProps(buckets: LatencyBucket[]): TopicSummaryCardProps {
  const latest = buckets[buckets.length - 1]; // newest bucket is last
  const totalSamples = sum(buckets, b => b.sample_count);
  const totalSlow = sum(buckets, b => b.slow_count);
  const totalTimeout = sum(buckets, b => b.timeout_count);
  const totalFail = sum(buckets, b => b.fail_count);
  const totalViolations = sum(buckets, b => b.budget_violation_count);

  return {
    p50Ms: latest?.p50_ms ?? null,
    p95Ms: latest?.p95_ms ?? null,
    p99Ms: latest?.p99_ms ?? null,
    runCount: buckets.length,
    outcomeBreakdown: {
      passRate: totalSamples > 0
        ? (totalSamples - totalSlow - totalTimeout - totalFail) / totalSamples
        : 0,
      slowRate: totalSamples > 0 ? totalSlow / totalSamples : 0,
      timeoutRate: totalSamples > 0 ? totalTimeout / totalSamples : 0,
      failRate: totalSamples > 0 ? totalFail / totalSamples : 0,
    },
    budgetViolations: totalViolations > 0
      ? { count: totalViolations, total: totalSamples, rate: totalViolations / totalSamples }
      : null,
  };
}
```

**Pros:**
- No new API endpoint. The data is already in `LatencyBucket[]`.
- Pure function, unit-testable. Pass in mock buckets, assert the output.
- Follows the ADR-0023 pattern — transforms compute derived data from
  API responses.

**Cons:**
- `runCount` here is actually "bucket count", not "number of runs".
  The true run count requires a separate query. For the summary card,
  bucket count is a reasonable proxy and is clearly labelled.
- The percentiles shown are from the latest time bucket, not from
  all handles across the range. This is correct for "current state"
  but could confuse users expecting a range-wide aggregate. Mitigated
  by: the card is positioned above the time-series charts, visually
  communicating "latest snapshot".

#### Option B: Add a `summary` field to `TopicLatencyResponse`

Compute the aggregate stats server-side and include them in the API
response alongside the buckets.

**Pros:**
- Single source of truth for summary stats.
- Can include accurate `run_count` from the database.

**Cons:**
- Changes the API response shape for a display concern. The summary
  stats are computed from the same buckets the API already returns.
  Computing them again server-side adds code without adding data.
- Every time range change would return fresh summary stats. The client
  can compute them instantly from the cached buckets.

### Decision: Option A — compute from buckets in a transform.

---

### Decision 2: BudgetComplianceChart — new component vs parameterised chart

#### Option A: Dedicated `BudgetComplianceChart` component (chosen)

A new component that receives pre-computed budget violation percentages
per bucket and renders a Recharts `LineChart` with a threshold line.

```typescript
interface BudgetComplianceChartProps {
  data: BudgetBucketPoint[];      // { bucket: string; violationPct: number }
  thresholdPct: number;           // default 20
}
```

The transform layer computes `violationPct` per bucket:

```typescript
export function toBudgetData(buckets: LatencyBucket[]): BudgetBucketPoint[] {
  return buckets.map(b => ({
    bucket: b.bucket,
    violationPct: b.sample_count > 0
      ? (b.budget_violation_count / b.sample_count) * 100
      : 0,
  }));
}
```

**Pros:**
- The component has a narrow, specific interface. It renders a
  percentage line chart with a threshold marker. No shared props with
  `LatencyLineChart`.
- The y-axis is percentage (0-100%), not milliseconds. Different scale,
  different semantics, different component.
- The threshold line (horizontal dashed at 20%) is specific to budget
  compliance and does not belong in a generic chart component.

**Cons:**
- A new component file. Mitigated by: ~40 lines, single responsibility.

#### Option B: Parameterised `LatencyLineChart`

Add a `mode` or `lines` prop to `LatencyLineChart` to support rendering
arbitrary metrics from `LatencyBucket`.

**Cons:**
- The latency chart renders p50/p95/p99 in milliseconds. The budget
  chart renders a single percentage line. Forcing both through the same
  component creates a "mode" parameter that makes neither case clean.
  The y-axis label, scale, tooltip format, and threshold line are all
  different.

### Decision: Option A — dedicated component.

---

### Decision 3: Where does the budget visibility check live?

The budget chart should only render when at least one bucket has
`budget_violation_count > 0` (PRD-010: "shown only when the topic has
handles with `budget_ms`").

#### Option A: Transform function (chosen)

```typescript
// transforms/topicDrilldown.ts
export function hasBudgetData(buckets: LatencyBucket[]): boolean {
  return buckets.some(b => b.budget_violation_count > 0);
}
```

The route calls this function and conditionally renders:

```tsx
{hasBudgetData(data.buckets) && (
  <BudgetComplianceChart data={toBudgetData(data.buckets)} thresholdPct={20} />
)}
```

**Pros:**
- The route does not contain data-inspection logic. It calls a named
  function whose intent is clear from the name.
- The function is unit-testable: `hasBudgetData([bucket with 0 violations]) === false`.
- Consistent with ADR-0023's rule: "no business logic, no data
  transformation, and no conditional rendering beyond loading/error/empty
  states" in the route. This check is a transform, not business logic —
  it decides whether a visual section is relevant, the same way the
  route already decides whether the empty state is shown.

#### Option B: Inline in the route

```tsx
{data.buckets.some(b => b.budget_violation_count > 0) && <BudgetComplianceChart ... />}
```

**Cons:**
- The route contains a data traversal. If the condition becomes more
  complex (e.g. also checking `budget_ms` field), the inline grows.
- Not independently testable.

### Decision: Option A — transform function.

---

### Decision 4: TopicRunsTable — new component vs `RunTable` reuse

#### Option A: Dedicated `TopicRunsTable` component (chosen)

A new component accepting `TopicRunRow[]` props:

```typescript
interface TopicRunRow {
  runId: string;
  startedAt: string;          // pre-formatted
  environment: string | null;
  branch: string | null;
  handleCount: number;
  p50Ms: string | null;       // pre-formatted: "9ms"
  p95Ms: string | null;
  p99Ms: string | null;
  slowCount: number;
  timeoutCount: number;
}

interface TopicRunsTableProps {
  rows: TopicRunRow[];
  onRowClick: (runId: string) => void;
}
```

**Pros:**
- Different columns from `RunTable`. `TopicRunsTable` shows per-topic
  per-run percentiles, handle count, and slow/timeout counts. `RunTable`
  shows pass/fail/slow totals, topic count, and sparklines. Different
  data, different columns, different component.
- The props are pre-formatted by a transform. The component renders
  strings, not numbers.

#### Option B: Make `RunTable` polymorphic

Add a `columns` prop to `RunTable` that configures which columns render.

**Cons:**
- The two tables share only the column header + row layout pattern.
  The cell content, data types, and click behaviour are different.
  A polymorphic table component quickly becomes a configuration-driven
  mini-framework. For two tables, two simple components are clearer.

### Decision: Option A — dedicated component.

---

## Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Summary card data | Compute from `LatencyBucket[]` in transform | No new endpoint; pure function; follows ADR-0023 pattern |
| Budget chart | Dedicated `BudgetComplianceChart` | Different y-axis (% vs ms), threshold line, narrow interface |
| Budget visibility | Transform function `hasBudgetData()` | Keeps route thin; testable; named intent |
| Runs table | Dedicated `TopicRunsTable` | Different columns and data shape from `RunTable` |

---

## Consequences

### Positive

- **The route stays under 60 lines.** Adding three sections (summary
  card, budget chart, runs table) adds ~15 lines of JSX to the route
  (3 component tags + a conditional + a `useTopicRuns` hook call).
  Total: ~85 lines including the existing 70, minus the existing
  resolution caption which moves into the summary card.
- **All new logic is testable without React.** `toSummaryCardProps`,
  `toBudgetData`, `hasBudgetData`, and `toTopicRunRows` are pure
  functions tested with mock data. Sub-millisecond tests.
- **Three charts share one API call.** `useTopicLatency` is called once.
  The route passes `data.buckets` to `LatencyLineChart`,
  `OutcomeBarChart`, and (conditionally) `BudgetComplianceChart`. React
  Query caches the response. Changing the time range triggers one
  refetch; all three charts update.
- **The runs table is independent.** `useTopicRuns` fetches different
  data from a different endpoint. A slow latency query does not block
  the runs table, and vice versa.
- **Budget chart is zero-cost when not needed.** If `hasBudgetData`
  returns false, the component is not rendered. No empty chart, no
  "no data" message for a feature the topic does not use.

### Negative

- **Four new files.** `BudgetComplianceChart.tsx`, `TopicRunsTable.tsx`,
  `TopicSummaryCard.tsx`, and `transforms/topicDrilldown.ts`. Mitigated
  by: each file is small (30-60 lines), single-purpose, and
  independently testable. The file count follows the established
  pattern from ADR-0023 (HeroCard + ProportionBar + transforms).
- **Summary card percentiles are from the latest bucket, not a
  range-wide aggregate.** The card shows "p50: 9ms" which is the p50
  from the most recent time bucket, not the p50 across all handles in
  the range. This is correct for "current state" but could be
  misinterpreted. Mitigated by: the card's position above the charts
  visually communicates "latest", and the label can include a timestamp.

### Neutral

- A new `useTopicRuns` hook is needed. This follows the exact pattern
  of `useTopicLatency` — a React Query wrapper around an API call. ~20
  lines.
- The `transforms/topicDrilldown.ts` file imports `formatLatency` and
  `formatTimestamp` from `transforms/runSummary.ts`. These are reused,
  not duplicated.

### Security Considerations

- **No `dangerouslySetInnerHTML`.** All new components render data as
  React text nodes. Topic names and matcher descriptions are rendered
  as `{props.topic}`, not interpolated into HTML.
- **URL-encoded topic names.** The `useTopicLatency` hook already calls
  `encodeURIComponent(params.topic)`. The new `useTopicRuns` hook must
  do the same. Topic names may contain characters that require encoding
  (e.g. `orders.created`, `user/signup`).

---

## Implementation

### Component Tree (after changes)

```
TopicDrilldown (route)
├── FilterBar (existing)
│   └── TimeRangePicker (existing)
├── TopicSummaryCard (new — presentational)
├── LatencyLineChart (existing — no changes)
├── OutcomeBarChart (existing — no changes)
├── BudgetComplianceChart (new — presentational, conditional)
└── TopicRunsTable (new — presentational)
```

### Layer Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Route: TopicDrilldown.tsx                                │
│  Reads URL state, calls hooks, calls transforms,          │
│  passes props to components                               │
└────────────┬─────────────────────────────┬───────────────┘
             │                             │
     ┌───────▼────────┐           ┌────────▼────────┐
     │  Hooks           │           │  Transforms      │
     │  useTopicLatency  │           │  toSummaryCard   │
     │  useTopicRuns     │           │  toBudgetData    │
     │  useTenants       │           │  hasBudgetData   │
     └───────┬──────────┘           │  toTopicRunRows  │
             │                      └────────┬────────┘
             │        props                  │ pure functions
             ▼                               ▼
     ┌────────────────────────────────────────────────────┐
     │  Components (stateless, receive props only)         │
     │  TopicSummaryCard, BudgetComplianceChart,            │
     │  TopicRunsTable, LatencyLineChart, OutcomeBarChart   │
     └────────────────────────────────────────────────────┘
```

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `components/data/TopicSummaryCard.tsx` | Aggregate stats: percentiles, outcome breakdown, budget violations | ~50 |
| `components/charts/BudgetComplianceChart.tsx` | Recharts `LineChart` with violation % line and threshold | ~40 |
| `components/data/TopicRunsTable.tsx` | Table of recent runs for a topic, with per-run stats | ~45 |
| `transforms/topicDrilldown.ts` | `toSummaryCardProps`, `toBudgetData`, `hasBudgetData`, `toTopicRunRows` | ~90 |
| `transforms/topicDrilldown.test.ts` | Unit tests for all transform functions | ~120 |
| `hooks/useTopicRuns.ts` | React Query wrapper for `GET /topics/{topic}/runs` | ~25 |

### Modified Files

| File | Change |
|------|--------|
| `routes/TopicDrilldown.tsx` | Add `useTopicRuns` hook call, summary card, budget chart (conditional), runs table. Import transforms. |

### Interface Contracts

#### TopicSummaryCard

```typescript
interface OutcomeBreakdown {
  passRate: number;    // 0-1
  slowRate: number;
  timeoutRate: number;
  failRate: number;
}

interface BudgetSummary {
  count: number;       // total violations across all buckets
  total: number;       // total samples across all buckets
  rate: number;        // count / total
}

interface TopicSummaryCardProps {
  p50Ms: number | null;
  p95Ms: number | null;
  p99Ms: number | null;
  runCount: number;           // bucket count as proxy
  outcomeBreakdown: OutcomeBreakdown;
  budgetViolations: BudgetSummary | null;  // null = no budget data
}
```

The card is fully described by its props. It formats percentiles using
`formatLatency()` from the shared transforms, renders outcome percentages
as coloured text, and shows budget violation count only when
`budgetViolations` is non-null.

#### BudgetComplianceChart

```typescript
interface BudgetBucketPoint {
  bucket: string;         // ISO timestamp
  violationPct: number;   // 0-100
}

interface BudgetComplianceChartProps {
  data: BudgetBucketPoint[];
  thresholdPct: number;   // default 20 — renders as a horizontal dashed line
}
```

Recharts `LineChart` with a single `Line` (violation %) and a
`ReferenceLine` at the threshold. Y-axis is 0-100%. X-axis matches the
other charts' time axis (same bucket timestamps).

#### TopicRunsTable

```typescript
interface TopicRunRow {
  runId: string;
  startedAt: string;          // pre-formatted: "19 Apr 09:44"
  environment: string | null;
  branch: string | null;
  handleCount: number;
  p50Ms: string | null;       // pre-formatted: "9ms"
  p95Ms: string | null;
  p99Ms: string | null;
  slowCount: number;
  timeoutCount: number;
}

interface TopicRunsTableProps {
  rows: TopicRunRow[];
  onRowClick: (runId: string) => void;
}
```

### Route Component (after changes)

```typescript
// routes/TopicDrilldown.tsx (pseudocode — shows final wiring)

function TopicDrilldown() {
  // 1. URL state
  const { topic } = useParams<{ topic: string }>();
  const [tenant, setTenant] = useTenantParam();
  const [env, setEnv] = useEnvParam();
  const [from, setFrom] = useFromParam();
  const [to, setTo] = useToParam();

  // 2. Data fetching — two independent hooks
  const latency = useTopicLatency({ topic, tenant, environment: env, from, to });
  const runs = useTopicRuns({ topic, tenant, limit: 20 });

  // 3. Transforms (only run when data is available)
  const summaryProps = latency.data
    ? toSummaryCardProps(latency.data.buckets)
    : null;
  const showBudget = latency.data
    ? hasBudgetData(latency.data.buckets)
    : false;
  const budgetData = showBudget
    ? toBudgetData(latency.data!.buckets)
    : [];
  const runRows = runs.data
    ? toTopicRunRows(runs.data.items)
    : [];

  // 4. Render — pure composition
  return (
    <PageStack>
      <HeaderGroup>
        <TopicHeader topic={topic} />
        <FilterBar ...>
          <TimeRangePicker ... />
        </FilterBar>
      </HeaderGroup>

      {/* Loading / error / empty states */}
      ...

      {/* Data sections */}
      {summaryProps && <TopicSummaryCard {...summaryProps} />}
      <LatencyLineChart data={latency.data.buckets} />
      <OutcomeBarChart data={latency.data.buckets} />
      {showBudget && (
        <BudgetComplianceChart data={budgetData} thresholdPct={20} />
      )}
      <TopicRunsTable
        rows={runRows}
        onRowClick={(id) => navigate(`/runs/${id}`)}
      />
    </PageStack>
  );
}
```

The route is ~60 lines. It reads state, fetches data from two hooks,
transforms it via four pure functions, and passes props to five
components. No string formatting. No percentile computation. No budget
violation checking logic — those are in the transform layer.

### Migration Path

Not applicable — the existing route is extended, not replaced. The two
existing chart components (`LatencyLineChart`, `OutcomeBarChart`) are
unchanged.

### Timeline

1. Create `transforms/topicDrilldown.ts` with unit tests.
2. Create `hooks/useTopicRuns.ts`.
3. Build `TopicSummaryCard` component.
4. Build `BudgetComplianceChart` component.
5. Build `TopicRunsTable` component.
6. Wire everything in `routes/TopicDrilldown.tsx`.

---

## Validation

### Success Metrics

- **Route component is under 70 lines.** Measured by: line count.
- **Transform functions execute in < 1ms for 720 buckets** (30-day
  hourly). Measured by: Vitest benchmark.
- **Components have zero React Query or router imports.** Measured by:
  CI grep check — `grep -r "useQuery\|useNavigate\|useParams"
  components/data/TopicSummaryCard.tsx components/charts/BudgetComplianceChart.tsx
  components/data/TopicRunsTable.tsx` returns zero matches.
- **Transform functions have zero React imports.** Measured by:
  `grep "from 'react'" transforms/topicDrilldown.ts` returns zero matches.
- **`hasBudgetData` returns false when all violations are zero.**
  Measured by: unit test with buckets where every
  `budget_violation_count === 0`.
- **`toSummaryCardProps` extracts latest bucket percentiles.** Measured
  by: unit test with known bucket data.
- **Budget chart is not rendered when no budget data exists.** Measured
  by: component test rendering the route with mock data that has zero
  violations, asserting the chart component is not in the DOM.

### Monitoring

- Bundle size impact: three new components + transforms < 8 KB gzipped.
  Checked via `size-limit` in CI.
- React Query cache hit rate for `useTopicLatency` and `useTopicRuns` —
  both should show high hit rates during filter changes within the same
  topic (only time range changes trigger refetch).

---

## Related Decisions

- [ADR-0022](0022-chronicle-frontend-architecture.md) — Frontend
  architecture. Defines Recharts, URL state, React Query, and the
  design system. This ADR follows the same patterns.
- [ADR-0023](0023-chronicle-run-summary-view.md) — Run Summary view
  component architecture. Establishes the route/transform/component
  layer pattern that this ADR follows.
- [ADR-0025](0025-chronicle-topic-drilldown-backend.md) — Topic
  Drilldown backend. Defines the API endpoints and response shapes
  consumed by the hooks and transforms specified here.
- [PRD-010](../prd/PRD-010-chronicle-dashboard-views.md) — View 3
  specification. Defines the content sections, acceptance criteria,
  and interaction patterns.

---

## References

- [Recharts `ReferenceLine`](https://recharts.org/en-US/api/ReferenceLine) —
  used for the budget threshold marker in `BudgetComplianceChart`
- [React Query `staleTime`](https://tanstack.com/query/latest/docs/react/guides/important-defaults) —
  `useTopicRuns` uses `staleTime: 30_000` to match `useTopicLatency`

---

## Notes

- **Open follow-up — run markers on the latency chart.** PRD-010
  specifies "run markers on the x-axis" for the latency line chart.
  This requires passing run `started_at` timestamps to
  `LatencyLineChart` as a `markers: string[]` prop and rendering
  Recharts `ReferenceLine` components at each timestamp. Deferred
  because it requires either: (a) a new field on `TopicLatencyResponse`
  with run timestamps per bucket, or (b) using `useTopicRuns` data
  to extract timestamps. Option (b) is simpler but couples two data
  sources in the chart. **Owner:** Chronicle frontend.

- **Open follow-up — summary card percentile labelling.** The summary
  card shows "p50: 9ms" from the latest bucket. If the user selects a
  30-day range and the latest bucket is stale (e.g. the topic was not
  seen recently), the percentiles may not reflect the selected range.
  Consider adding a "from {bucket_timestamp}" qualifier or computing
  range-wide percentiles from all buckets (which would require
  re-aggregating on the client — computationally cheap but semantically
  different from database `percentile_cont`). **Owner:** Chronicle
  frontend.

- **Open follow-up — `TopicRunsTable` pagination.** The initial
  implementation fetches 20 runs. If pagination is needed, add `limit`
  and `offset` URL params to `useTopicRuns` and a `Pagination` component
  below the table. The API already supports pagination. **Owner:**
  Chronicle frontend.

**Last Updated:** 2026-04-19
