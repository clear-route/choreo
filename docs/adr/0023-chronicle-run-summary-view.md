# 0023. Chronicle Run Summary View — Component Architecture and Data Flow

**Status:** Proposed
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-010 — Chronicle Dashboard Views](../prd/PRD-010-chronicle-dashboard-views.md), View 1

---

## Context

The Run Summary is Chronicle's landing page. It answers "what happened?"
for the developer persona (post-deploy) and "what's recent?" for the QA
persona (scanning history). PRD-010 specifies two content sections: a
Hero Card (latest run summary matching the OG HTML report header) and a
Run Table (paginated list with sparklines).

ADR-0022 established the frontend architecture: Recharts for charts,
URL-driven state via `nuqs`, React Query for server data, and a design
system in `styles.ts`. This ADR records how the Run Summary view is
decomposed into components, where data transformation lives, and the
interface contracts between them.

### Problem Statement

How should the Run Summary view be decomposed so that components are
stateless and reusable, data transformation is testable without rendering,
and the route component is a thin composition layer?

### Goals

- **Dumb components.** Every presentational component receives fully
  transformed data via props. No API calls, no URL state reads, no data
  computation inside components. A component renders what it is given.
- **Testable transforms.** The logic that converts API response shapes
  into component props is in pure functions, unit-testable without React.
- **Thin route.** The route component (`RunSummary.tsx`) wires hooks to
  components. It contains no business logic, no data transformation,
  and no conditional rendering beyond loading/error/empty states.
- **Reusable Hero Card.** The Hero Card appears in both Run Summary
  (latest run) and Run Detail (specific run). It must accept the same
  props in both contexts.

### Non-Goals

- Defining the Run Detail view (separate ADR).
- Implementing the backend schema changes (`pass_rate`, `topic_count`,
  `p50_ms`/`p95_ms`/`p99_ms` on `RunSummary`).
- SSE integration beyond cache invalidation (already handled by
  `useSSE` hook from ADR-0022).

---

## Decision Drivers

- **The OG report header is the visual contract.** The Hero Card must
  look and read like the OG HTML report's header section. The developer
  recognises the sentence structure, proportion bar, and sub-line format.
  Diverging from this visual wastes the recognition the team already has.
- **Components must be testable in isolation.** A `HeroCard` rendered
  with mock props must produce the correct output without an API, a
  router, or a query client.
- **Data transforms must be testable without React.** The function that
  converts `RunSummary` to `HeroCardProps` is a pure function. The
  function that computes sparkline data from a list of runs is a pure
  function. Both are unit-testable with `vitest` in under 1ms.
- **The route is a wiring diagram.** Reading `RunSummary.tsx` tells you
  what data is fetched, what components are rendered, and how they
  connect. It does not tell you how data is transformed or how components
  render — those are in `transforms/` and `components/` respectively.

---

## Decision

### Component Tree

```
RunSummary (route)
├── FilterBar (layout — existing)
├── HeroCard (new — presentational)
│   ├── ProportionBar (new — presentational)
│   └── SubLine (new — presentational)
└── RunTable (existing — refactored)
    └── Sparkline (existing — presentational)
```

### Layer Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Route: RunSummary.tsx                                    │
│  Reads URL state, calls hooks, passes props to components │
└────────────┬─────────────────────────────┬───────────────┘
             │                             │
     ┌───────▼────────┐           ┌────────▼────────┐
     │  Hooks          │           │  Transforms      │
     │  useRuns()      │           │  toHeroProps()    │
     │  useTenants()   │           │  toSparklines()   │
     │  useSSE()       │           │  toRunRows()      │
     └───────┬────────┘           └────────┬────────┘
             │                             │
             │        props                │ pure functions
             ▼                             ▼
     ┌────────────────────────────────────────────────┐
     │  Components (stateless, receive props only)     │
     │  HeroCard, ProportionBar, SubLine, RunTable     │
     └────────────────────────────────────────────────┘
```

**Three layers, strict boundaries:**

1. **Route** (`routes/RunSummary.tsx`) — reads URL params, calls hooks,
   calls transforms, passes props to components. No JSX logic beyond
   loading/error/empty branching.
2. **Transforms** (`transforms/runSummary.ts`) — pure functions that
   convert API response types to component prop types. No React imports.
   No side effects.
3. **Components** (`components/`) — stateless. Receive fully formed
   props. No hooks, no API calls, no URL state. A component's only job
   is to render markup from props.

### File Structure

```
src/
  routes/
    RunSummary.tsx              # Route — wires hooks to components

  transforms/
    runSummary.ts               # Pure functions: API → component props
    runSummary.test.ts          # Unit tests for transforms

  components/
    data/
      HeroCard.tsx              # OG report header — presentational
      ProportionBar.tsx         # Coloured segment bar — presentational
      RunTable.tsx              # Paginated run list (existing, refactored)

  hooks/
    useRuns.ts                  # React Query hook (existing)
```

### Interface Contracts

#### HeroCard

```typescript
interface HeroCardProps {
  /** "37 of 37 tests passed" */
  headline: string;
  /** Colour for the highlighted number: "pass" | "fail" | "slow" */
  headlineOutcome: "pass" | "fail" | "slow";
  /** Duration formatted as "2.18s" or "43.34s" */
  duration: string;
  /** Segment widths as fractions (0-1) for the proportion bar */
  segments: ProportionSegment[];
  /** Sub-line items: "37 tests", "✓ 37 passed", "p50 9ms", etc. */
  stats: SubLineStat[];
}

interface ProportionSegment {
  key: "passed" | "slow" | "failed" | "errored" | "skipped";
  fraction: number;   // 0-1, how wide this segment is
}

interface SubLineStat {
  label: string;       // "37 tests", "p50 9ms"
  colour?: string;     // design token colour, or undefined for default
  isMono?: boolean;    // true for percentile values
}
```

The HeroCard is fully described by its props. It has no knowledge of
`RunSummary`, `RunDetail`, or any API type. The same component renders
in both Run Summary (latest run) and Run Detail (specific run) — the
route passes different data, the component renders identically.

#### ProportionBar

```typescript
interface ProportionBarProps {
  segments: ProportionSegment[];
}
```

A 6px horizontal bar with coloured segments. Each segment's width is
`fraction * 100%`. Segments with `fraction === 0` are not rendered.
Colours are mapped from the design system tokens by segment key.

#### RunTable (refactored)

```typescript
interface RunRow {
  id: string;
  startedAt: string;      // pre-formatted: "19 Apr 09:44"
  branch: string | null;
  passed: number;
  failed: number;
  slow: number;
  topicCount: number;
  anomalyCount: number;
  sparklineData: [number[], number[]] | null;  // [timestamps, values]
}

interface RunTableProps {
  rows: RunRow[];
  onRowClick: (runId: string) => void;
  onBranchClick: (branch: string) => void;
}
```

The RunTable receives pre-formatted rows. It does not know about
`RunSummary`, `pass_rate`, or sparkline computation. The `sparklineData`
is already computed by the transform layer. `onRowClick` and
`onBranchClick` are callbacks provided by the route — the table does
not import `react-router-dom`.

### Transform Functions

```typescript
// transforms/runSummary.ts

/** Convert a RunSummary API response to HeroCard props. */
export function toHeroProps(run: RunSummary): HeroCardProps;

/** Convert a list of RunSummary responses to RunTable rows.
 *  Computes sparkline data using a rolling window of pass_rate values.
 *  @param runs - the full fetched list (including over-fetch buffer)
 *  @param displayCount - how many rows to display (e.g. 50)
 */
export function toRunRows(
  runs: RunSummary[],
  displayCount: number,
): RunRow[];

/** Format milliseconds to a human-readable duration string.
 *  < 1000 → "42ms", < 60000 → "2.18s", >= 60000 → "1m 12s"
 */
export function formatDuration(ms: number): string;

/** Format a timestamp to "DD Mon HH:MM" in en-GB locale. */
export function formatTimestamp(iso: string): string;
```

Every function is pure. Every function is independently testable.
The route calls these functions to convert API data to component props.

### Route Component

```typescript
// routes/RunSummary.tsx (pseudocode — shows wiring, not full JSX)

function RunSummary() {
  // 1. URL state
  const [tenant, setTenant] = useTenantParam();
  const [env, setEnv] = useEnvParam();
  const [offset, setOffset] = useOffsetParam();

  // 2. Data fetching
  const { data, isLoading, isError, error, refetch } = useRuns({
    tenant,
    environment: env,
    limit: 70,    // over-fetch by 20 for sparkline buffer
    offset,
  });

  // 3. Transforms (only run when data is available)
  const heroProps = data?.items[0] ? toHeroProps(data.items[0]) : null;
  const rows = data?.items ? toRunRows(data.items, 50) : [];

  // 4. Render — pure composition, no logic
  return (
    <PageStack>
      <HeaderGroup>
        <PageTitle>Runs</PageTitle>
        <FilterBar ... />
      </HeaderGroup>

      {isLoading ? <LoadingSkeleton /> :
       isError ? <ErrorState ... /> :
       !data ? <OnboardingState ... /> :
       <>
         {heroProps && <HeroCard {...heroProps} />}
         <RunTable
           rows={rows}
           onRowClick={(id) => navigate(`/runs/${id}`)}
           onBranchClick={(b) => setBranch(b)}
         />
         <Pagination ... />
       </>
      }
    </PageStack>
  );
}
```

The route is ~40 lines. It reads state, fetches data, transforms it,
and passes it to components. No string formatting. No colour logic.
No sparkline computation. Those are in the transform layer.

---

## Consequences

### Positive

- **Components are testable with mock props.** Render `<HeroCard
  headline="37 of 37 tests passed" ... />` and assert the output.
  No API mock, no router mock, no query client mock.
- **Transforms are testable without React.** Call
  `toHeroProps(mockRunSummary)` and assert the returned object. Sub-
  millisecond tests, no DOM.
- **The HeroCard is reusable.** Run Summary passes `toHeroProps(latestRun)`.
  Run Detail passes `toHeroProps(specificRun)`. Same component, different
  data, zero duplication.
- **The route reads like a wiring diagram.** A new contributor reads
  `RunSummary.tsx` and sees: "it fetches runs, transforms them, and
  renders a hero card + table." The transform and component details are
  one click away, not inline.

### Negative

- **One extra layer (transforms).** The transform functions could live
  inline in the route or inside the components. Extracting them adds
  a file. Mitigated by: the file is small (~80 lines), the functions
  are pure, and the testability gain is significant.
- **Component props are verbose.** `HeroCardProps` has 5 fields;
  `RunRow` has 9 fields. The route must construct these explicitly.
  Mitigated by: the transform functions handle construction; the route
  calls `toHeroProps(run)`, not a 10-line object literal.

### Neutral

- The `transforms/` directory is new. It does not exist in the current
  codebase. It will also be used by Run Detail (`toRunDetailHeroProps`)
  and other views that need API-to-component mapping.

### Security Considerations

- **No `dangerouslySetInnerHTML`.** The HeroCard renders the headline
  as a React text node. The proportion bar uses `style.width` with a
  numeric fraction — no string interpolation of user data into CSS.
- **Branch names in the Run Table** are rendered as text nodes. A branch
  named `<script>alert(1)</script>` renders as literal text, not HTML.
- N/A — see ADR-0022 §Security Considerations for the full frontend
  security model.

---

## Implementation

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `components/data/HeroCard.tsx` | OG report header — headline, proportion bar, sub-line, duration | ~60 |
| `components/data/ProportionBar.tsx` | 6px coloured segment bar | ~25 |
| `transforms/runSummary.ts` | `toHeroProps`, `toRunRows`, `formatDuration`, `formatTimestamp` | ~80 |
| `transforms/runSummary.test.ts` | Unit tests for all transform functions | ~100 |

### Modified Files

| File | Change |
|------|--------|
| `routes/RunSummary.tsx` | Replace inline data handling with transform calls; add HeroCard; adjust `useRuns` limit to 70 |
| `components/data/RunTable.tsx` | Accept `RunRow[]` instead of `RunSummary[]`; remove internal formatting; accept `onRowClick`/`onBranchClick` callbacks |
| `api/types.ts` | Add `pass_rate`, `topic_count`, `p50_ms`, `p95_ms`, `p99_ms` to `RunSummary` |
| `hooks/useRuns.ts` | No change needed — already returns `PagedResponse<RunSummary>` |

### Migration Path

Not applicable — the existing RunSummary route is a shell with no data.
This ADR defines the first real implementation.

### Timeline

1. Add schema fields to `api/types.ts` (and backend Pydantic schema).
2. Implement `transforms/runSummary.ts` with unit tests.
3. Build `HeroCard` and `ProportionBar` components.
4. Refactor `RunTable` to accept `RunRow[]` props.
5. Wire everything in `routes/RunSummary.tsx`.

---

## Validation

### Success Metrics

- **Hero Card renders in < 100ms** after data is available. Measured by:
  Vitest render benchmark with mock data.
- **Transform functions execute in < 1ms** for 70 runs. Measured by:
  Vitest benchmark calling `toRunRows` with synthetic data.
- **Route component is under 50 lines.** Measured by: line count check.
- **Components have zero React Query or router imports.** Measured by:
  CI grep check: `grep -r "useQuery\|useNavigate\|useParams"
  components/data/HeroCard.tsx components/data/ProportionBar.tsx` returns
  zero matches.
- **Transform functions have zero React imports.** Measured by:
  `grep "from 'react'" transforms/runSummary.ts` returns zero matches.

### Monitoring

- Bundle size impact: HeroCard + ProportionBar + transforms < 5 KB
  gzipped. Checked via `size-limit` in CI.

---

## Related Decisions

- [ADR-0022](0022-chronicle-frontend-architecture.md) — Frontend
  architecture. Defines Recharts, URL state, React Query, design system.
- [PRD-010](../prd/PRD-010-chronicle-dashboard-views.md) — View
  specifications. Defines the Hero Card content and Run Table columns.
- [PRD-007](../prd/PRD-007-test-report-output.md) — The OG HTML report.
  The Hero Card mirrors its header section.

---

## References

- [OG Harness Report CSS](../../packages/chronicle/test-report/index.html) —
  `.hr-hero`, `.hr-proportion`, `.hr-hero-sub` classes that the Hero Card
  mirrors
- [Recharts documentation](https://recharts.org/) — not used in View 1
  (no charts), but the same Recharts integration pattern from ADR-0022
  applies to Views 3-4

---

## Notes

- **Open follow-up — over-fetch strategy.** The route fetches 70 runs
  but displays 50. The Pagination component must use `displayCount` (50),
  not the API `limit` (70), for page calculation. The 20-run buffer is
  only consumed by the sparkline transform. If the API's `total` is
  used for pagination, the over-fetch is invisible to the user.
  **Owner:** Chronicle frontend.
- **Open follow-up — SubLine component extraction.** The sub-line
  (stats row below the proportion bar) could be a separate component
  (`SubLine.tsx`) or inline JSX within `HeroCard`. At ~10 lines of
  rendering, inline is acceptable for now. Extract if it grows or if
  the sub-line is needed independently elsewhere. **Owner:** Chronicle
  frontend.

**Last Updated:** 2026-04-19
