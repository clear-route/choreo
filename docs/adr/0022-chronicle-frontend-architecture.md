# 0022. Chronicle Frontend Architecture — Stateless React Dashboard with Recharts

**Status:** Accepted
**Date:** 2026-04-19
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-009 — Chronicle Reporting Server](../prd/PRD-009-chronicle-reporting-server.md)

---

## Context

Chronicle's backend (ADR-0021) exposes a REST API and SSE stream. The
frontend is a React dashboard that renders four views: Run Summary, Topic
Drilldown, Regression Timeline, and Anomaly Feed. This ADR records decisions
about the frontend's internal architecture: code structure, state management,
charting library, design system, interaction patterns, and how the UI stays
stateless against a server-owned data model.

### Background

- [PRD-009](../prd/PRD-009-chronicle-reporting-server.md) specifies the four
  views, SSE integration, and deployment model (single container serving both
  API and built frontend via FastAPI static file mount).
- [ADR-0021](0021-chronicle-api-structure.md) specifies the API surface,
  Pydantic response schemas, SSE event types (`run.completed`,
  `anomaly.detected`), and the paginated response envelope.
- No frontend code exists in the monorepo. This is a greenfield decision.
- The PRD mentions "React + Recharts (or Tremor)" as a starting point. This
  ADR evaluates that suggestion against the actual rendering requirements.

### Problem Statement

What frontend architecture, charting library, state management approach, and
code structure should Chronicle use to deliver a performant, maintainable
dashboard that treats the server as the single source of truth?

### Goals

- **Server is the source of truth.** The frontend holds no authoritative state.
  All data comes from the API or SSE stream. Refreshing the page restores
  the exact same view (URL-driven state).
- **Sub-second interactions.** View transitions, filter changes, and time range
  selections feel instant. Chart rendering for 30-day ranges with 100+ topics
  completes in under 500ms.
- **Canvas heatmap at scale.** The Regression Timeline heatmap must render
  100 topics x 90 time buckets (9,000+ cells) without DOM element overhead.
  SVG is not acceptable for this view.
- **Minimal bundle.** The frontend ships as pre-built static files — both
  inside the Python wheel (for `pip install`) and in the Docker image.
  Gzipped JS bundle target: under 200 KB.
- **Accessible.** Charts include ARIA labels and keyboard-navigable data
  tables as fallbacks. Colour is not the sole differentiator in any
  visualisation.

### Non-Goals

- Offline support or service workers.
- Server-side rendering (SSR). The dashboard is an internal tool, not a
  public-facing site. SEO is irrelevant.
- Mobile-responsive layout. PRD-009 explicitly defers this.
- Dark mode. PRD-009 explicitly defers this.
- Internationalisation. PRD-009 explicitly defers this.
- Component library publishing. The dashboard is a closed application.
- Node.js as a consumer dependency. Consumers install via `pip` or Docker.
  Node.js is required only for frontend development and CI builds.

---

## Decision Drivers

- **Rendering requirements are mixed.** Line charts and bar charts need SVG
  for interactive tooltips and click-through. The heatmap needs Canvas for
  performance at scale. The charting library must support both renderers or
  be composable enough to mix.
- **Time-series is the primary data shape.** Every chart plots metrics against
  time. The library must handle irregular intervals, time gaps (no runs over
  weekends), and timezone-aware axes.
- **Team familiarity.** The team knows React and TypeScript. Libraries that
  require deep D3 knowledge add ramp-up time.
- **Bundle size.** Single-container deployment means the frontend bundle is
  served by uvicorn, not a CDN. Every kilobyte matters.
- **Maintenance window.** Chronicle is an internal tool maintained by a small
  team. The frontend should use stable, well-documented libraries with low
  churn.

---

## Considered Options

### Decision 1: Charting Library

#### Option 1A: Recharts + custom Canvas heatmap (chosen)

**Description:** Recharts for SVG line and bar charts. A bespoke Canvas
heatmap component (~150 lines) for the Regression Timeline. Recharts is
the most widely adopted React charting library (~45M weekly npm downloads,
~24k GitHub stars, organisation-backed, monthly releases, stable v2.x).

**Pros:**
- Familiar React component API; shortest learning curve on the team.
- Strong TypeScript support with stable v2.x API.
- 45M weekly npm downloads — 25x the next largest React chart library.
  Organisation-backed with ~30 active contributors and monthly releases.
- Good time-series line chart and bar chart components with built-in
  tooltip, legend, and click handler support.
- ~50 KB gzipped — smallest bundle of the evaluated libraries.
- The heatmap is a self-contained Canvas component with no library
  dependency. A `fillRect` loop over pre-computed data renders 9,000+
  cells in under 20ms — faster than any library-based Canvas heatmap.

**Cons:**
- SVG-only rendering for line/bar charts. Not a concern — SVG handles
  the line/bar data volumes (720 points for 30-day hourly, 2,160 SVG path
  data points across 3 percentile lines) comfortably.
- No native heatmap component. The custom Canvas heatmap is ~150 lines
  of application code that must be maintained by the team. Mitigated by:
  the heatmap has a narrow interface (data grid, colour scale, cell hover
  callback) and no complex interaction beyond hover tooltips.
- Sparklines require a supplementary solution (see Decision 2).

#### Option 1B: Tremor

**Description:** Higher-level React dashboard component library built on
Recharts. Provides pre-styled cards, KPI widgets, and chart wrappers.

**Pros:**
- Pre-built dashboard components reduce UI development time.
- Consistent visual style out of the box.
- Good for KPI cards and summary widgets.

**Cons:**
- Inherits Recharts' SVG-only limitation — no Canvas heatmap.
- Higher-level abstraction limits customisation for analytics-specific
  interactions (run markers on time axes, click-through to run detail).
- Opinionated styling conflicts with a custom design system.
- Adds a dependency layer on top of Recharts without solving the core
  rendering limitation.

#### Option 1C: Nivo

**Description:** React charting library built on D3. Provides both SVG and
Canvas renderers for most chart types. Rich chart type catalogue including
native Canvas heatmaps.

**Pros:**
- Dual rendering: SVG for line/bar charts, Canvas for heatmaps — both
  rendering modes in a single library with a consistent API.
- Native heatmap component (`@nivo/heatmap`) with Canvas mode.
- Modular package structure (`@nivo/line`, `@nivo/bar`, `@nivo/heatmap`).
- Responsive containers and theme support built in.

**Cons:**
- **Single maintainer (plouc).** Bus-factor risk. No organisation backing.
  If the sole maintainer steps away, the library has no succession plan.
- **Never reached v1.0** (currently v0.99.0). Pre-1.0 libraries carry
  breaking-change risk with no semver stability guarantee.
- **1.8M weekly npm downloads** — meaningful adoption, but 25x smaller
  than Recharts (45M). Issue response times are weeks to months vs days.
- ~110-120 KB gzipped with selective imports (3 chart packages + D3
  transitive dependencies). The D3 dependency tree is heavy.
- Steeper learning curve than Recharts for custom interactions (tooltips,
  click handlers require understanding Nivo's layer system).
- Canvas heatmap (`HeatMapCanvas`) renders tooltips outside React's
  virtual DOM via `document.createElement`. Custom tooltip props that
  interpolate data into HTML strings could introduce XSS — the ESLint
  `react/no-danger` rule would not catch this.
- No sparkline primitive.

#### Option 1D: ECharts (via echarts-for-react)

**Description:** Apache ECharts with a React wrapper. Canvas + WebGL
rendering. Enterprise-grade charting with massive dataset support.

**Pros:**
- Canvas-native — handles 100K+ data points without performance issues.
- Excellent heatmap, line, bar, and sparkline support.
- Handles time gaps and irregular intervals natively.

**Cons:**
- Full bundle is ~1 MB. Tree-shaking reduces this to ~200 KB but requires
  careful configuration.
- Imperative API wrapped in a React component — not idiomatic React.
  Configuration objects are large and hard to type-check.
- Tooltip and interaction customisation requires learning ECharts' option
  system, which is verbose and poorly typed.
- Overkill for this dashboard's scale (thousands of points, not millions).

#### Option 1E: Visx (Airbnb)

**Description:** Low-level composable D3 primitives for React. Maximum
flexibility, minimum opinion.

**Pros:**
- Full control over rendering (SVG and Canvas).
- Composable primitives for any chart type.
- Small individual packages.

**Cons:**
- Requires building every chart from primitives. A line chart is 50-100
  lines of code vs 10 with Recharts or Nivo.
- Deep D3 knowledge required.
- Development time is 3-5x higher than with a higher-level library.
- No pre-built heatmap, tooltip, or legend components.

### Decision 2: Sparkline Library

#### Option 2A: uPlot (chosen)

**Description:** Lightweight Canvas-based time-series charting library.
48 KB total (smallest available). Purpose-built for sparklines and small
inline charts.

**Pros:**
- 48 KB total bundle — negligible addition.
- Canvas rendering — performs well in table cells with many rows.
- Time-series focused — handles the "last 20 runs" sparkline use case
  directly.
- No React wrapper needed for this use case — render to a `<canvas>` ref.

**Cons:**
- Not React-native; requires a thin wrapper component.
- Limited interaction (no tooltips in sparkline mode, which is acceptable).

#### Option 2B: Recharts Sparkline

**Description:** Use Recharts' `<LineChart>` with minimal configuration
for sparklines, since Recharts is already a dependency.

**Pros:**
- No additional dependency — reuses the primary chart library.

**Cons:**
- SVG rendering in table cells. With 50 rows x 1 sparkline each, this
  creates 50 SVG elements with internal `<path>` nodes. Heavier than
  Canvas for many simultaneous instances.
- Recharts' line component carries overhead (axes, legends, grid) that
  must be disabled for sparkline mode. The result is a full chart
  component configured to look minimal — not a purpose-built sparkline.
- Cannot be virtualised as effectively as Canvas instances (SVG teardown
  and recreation is more expensive).

### Decision 3: State Management

#### Option 3A: URL state + React Query (chosen)

**Description:** All view state lives in URL search parameters. React Query
(TanStack Query) manages server state (fetching, caching, background
refetching). No client-side state store (no Redux, no Zustand, no Jotai).

**Pros:**
- **Stateless frontend.** Refreshing the page restores the exact same view.
  Sharing a URL shares the exact same view. No client state to serialise
  or hydrate.
- **Server is the source of truth.** React Query's cache is a read-through
  cache of server state, not a separate store. Stale data is automatically
  refetched.
- **SSE invalidation is natural.** When an SSE event arrives, invalidate the
  relevant React Query cache key. The next render fetches fresh data. No
  manual state synchronisation.
- **No state management library.** One fewer dependency. No reducers, no
  actions, no selectors. View state is URL params; server state is React
  Query.
- **Deep linking for free.** A URL like
  `/topics/orders.created/latency?tenant=platform&env=staging&from=2026-04-01&to=2026-04-19`
  fully describes the view. Bookmarkable, shareable, reproducible.

**Cons:**
- URL parameters have length limits (~2,000 characters). Mitigated by:
  Chronicle's filter set is small (tenant, environment, branch, topic,
  time range, resolution). This fits comfortably.
- Complex filter combinations require careful URL serialisation. Mitigated
  by: `nuqs` library handles type-safe URL state with minimal boilerplate.
- React Query's `staleTime` and `gcTime` require tuning per endpoint to
  balance freshness against unnecessary refetches.

#### Option 3B: Zustand + React Query

**Description:** Zustand for client-side UI state (selected filters, panel
open/closed), React Query for server state.

**Pros:**
- Clean separation of UI state and server state.
- Zustand is lightweight (1 KB).

**Cons:**
- Client-side state diverges from URL. Refreshing the page loses Zustand
  state unless manually synced to URL params — which is exactly what
  Option 3A does without Zustand.
- Two state management approaches to understand and maintain.
- For Chronicle's four views, there is no UI state that is not representable
  as URL parameters. Filter selections, time ranges, and sort orders are all
  URL-serialisable.

#### Option 3C: Redux Toolkit + RTK Query

**Description:** Redux for global state, RTK Query for server state.

**Pros:**
- Mature ecosystem, strong DevTools.
- RTK Query handles caching and invalidation.

**Cons:**
- Disproportionate overhead for a 4-view dashboard with no client-side
  mutations. Chronicle never writes data from the frontend — it is
  read-only.
- ~30 KB added to bundle for Redux + RTK.
- Boilerplate (slices, reducers, selectors) for state that is better
  expressed as URL parameters.

### Decision 4: Design System Approach

#### Option 4A: Tailwind CSS + Headless UI (chosen)

**Description:** Utility-first CSS with Tailwind. Headless UI (or Radix UI)
for accessible, unstyled interactive primitives (dropdowns, modals, tabs).
No pre-built component library.

**Pros:**
- No design system lock-in. Chronicle's visual identity is defined by
  Tailwind configuration (colours, spacing, typography), not by a
  third-party component library's opinions.
- Headless primitives provide accessibility (ARIA, keyboard navigation)
  without imposing visual style.
- Tailwind's utility classes co-locate style with markup — no CSS file
  sprawl, no naming conventions to enforce.
- Tree-shaking removes unused utilities. Production CSS is typically
  < 10 KB gzipped.
- Consistent with the growing convention in internal tool dashboards.

**Cons:**
- No pre-built table, card, or form components. These must be built
  from primitives. Mitigated by: Chronicle has 4 views with a small
  component vocabulary (data table, chart card, filter bar, anomaly card).
  The component count is low enough to build from scratch.
- Tailwind class strings can be verbose. Mitigated by: extracting repeated
  patterns into component-level composition, not utility classes.

#### Option 4B: shadcn/ui

**Description:** Copy-paste React components built on Radix + Tailwind.
Not a dependency — source code is copied into the project.

**Pros:**
- Pre-built accessible components (Table, Card, Select, Badge, Tabs).
- Source-owned — no version pinning or breaking upgrades.
- Built on the same Tailwind + Radix stack as Option 4A.

**Cons:**
- Although individual components can be selectively copied (only the 8-10
  needed, not the full library), each carries styling opinions (border
  radius, shadow, colour) that require overriding to match Chronicle's
  identity. The "copy and modify" workflow means upstream accessibility
  bug fixes must be manually back-ported.
- Creates an illusion of a design system without the discipline of one.
  Copied components drift from their upstream source as local modifications
  accumulate.

#### Option 4C: Material UI (MUI)

**Description:** Full component library with Material Design styling.

**Pros:**
- Complete component set — data tables, cards, forms, icons.
- Strong accessibility defaults.

**Cons:**
- ~80 KB gzipped added to bundle (the heaviest option).
- Material Design visual identity is opinionated and hard to override
  for a custom dashboard aesthetic.
- Emotion/styled-components CSS-in-JS runtime adds overhead.
- Overkill for 4 views with a small component vocabulary.

### Decision 5: Distribution Strategy

The frontend is a React application that requires a Node.js build step.
Choreo ships on PyPI as a Python package. These two facts create a
distribution problem: how do consumers get a working dashboard?

#### Option 5A: Pre-built assets in the Python wheel (chosen)

**Description:** CI builds the frontend (`npm run build`), and the
resulting `dist/` directory is included as package data in the Python
wheel via Hatchling's `force-include`. `pip install choreo-chronicle`
ships a working dashboard — no Node.js required by consumers.

**Pros:**
- **`pip install` gives a complete product.** Consumers get the API
  server and the dashboard from a single `pip install`. No Node.js, no
  `npm`, no build step.
- **Docker and non-Docker deployments both work.** A consumer can run
  Chronicle in a venv, on a VM, or in an existing Python service without
  a multi-stage Docker build.
- **Well-established pattern.** Django ships admin static files in its
  wheel. Jupyter ships frontend assets in its wheel. The Python packaging
  ecosystem handles this reliably.
- **Node.js is a development dependency only.** Contributors who modify
  the frontend need Node.js. Consumers never do.

**Cons:**
- Wheel size increases by ~500 KB-1 MB (gzipped JS, CSS, HTML, and any
  static assets like fonts).
- CI must run `npm ci && npm run build` before building the wheel. The
  frontend build step adds ~30 seconds to CI.
- Version coordination: the frontend build must happen before `hatch build`.
  This requires a CI step ordering constraint (see §Build Pipeline).

#### Option 5B: Docker-only distribution

**Description:** The frontend is built in the Docker multi-stage build
(as described in PRD-009 §Deployment). No frontend assets are included
in the PyPI wheel. Consumers must use Docker.

**Pros:**
- No wheel size increase.
- No CI step ordering — Docker handles the build sequence.

**Cons:**
- **`pip install` gives a headless API server.** The dashboard is missing.
  Consumers who do not use Docker cannot access the UI.
- Breaks the monorepo's PyPI distribution model. Every other Choreo
  package is installable via `pip`. Chronicle would be the exception.
- Excludes consumers who run Python services directly (venvs, VMs,
  managed platforms like Cloud Run with custom images).

#### Option 5C: Separate frontend package (npm)

**Description:** Publish the frontend as a separate npm package or static
tarball. Consumers install the Python package and the frontend separately.

**Pros:**
- Clean separation of concerns.
- Frontend can be deployed to a CDN independently.

**Cons:**
- Two install steps. The PRD promises "one `docker compose up`" or a
  single `curl` — not "install Python package, then install npm package,
  then configure static serving."
- Version synchronisation between Python and npm packages adds release
  overhead.
- Consumers must configure FastAPI's static file mount path manually.

---

## Decision

### Charting: Recharts + custom Canvas heatmap + uPlot sparklines

**Recharts** for SVG line and bar charts. A **custom Canvas heatmap**
component for the Regression Timeline. **uPlot** for table-cell sparklines.

Recharts is chosen over Nivo for ecosystem health and production risk:

| Factor | Recharts | Nivo |
|--------|----------|------|
| Weekly npm downloads | 45M | 1.8M |
| GitHub stars | ~24k | ~14k |
| Active contributors (past year) | ~30 | ~10-15 |
| Backing | Organisation | Single maintainer (plouc) |
| Release cadence | Monthly | Irregular (months between) |
| Semver stability | Stable v2.x | Never reached v1.0 (v0.99.0) |
| Issue response time | Days | Weeks to months |

Nivo's advantage was a unified SVG + Canvas API. That advantage is
eliminated by building the heatmap as a self-contained Canvas component
(~150 lines of `fillRect` calls). The custom heatmap is also faster:
under 20ms for 9,000 cells vs 200-400ms through Nivo's D3 scale pipeline.

Recharts' SVG line/bar charts handle the dashboard's data volumes
comfortably (720 points for 30-day hourly data across 3 percentile lines).
Its React-native declarative API is the shortest learning curve on the team.

ECharts was rejected for its imperative API, bundle weight (~200 KB alone),
and verbose, poorly-typed configuration objects. Visx was rejected for
requiring 3-5x more development time to build charts from primitives.
Tremor was rejected for inheriting Recharts' limitations while adding an
abstraction layer that restricts customisation.

uPlot supplements Recharts for sparklines because Recharts' SVG line
component is too heavy for 50+ simultaneous table-cell instances. uPlot's
~15 KB gzipped Canvas renderer is purpose-built for this use case.

### State: URL parameters + React Query

All view state lives in URL search parameters. React Query manages the
server-state cache. No client-side state store. The frontend is a pure
function of `(URL params, server data, SSE events)`.

Zustand and Redux add client-side state that must be synchronised with the
URL — solving a problem that does not exist when the URL *is* the state.
Chronicle is a read-only dashboard; there are no client-side mutations
that require local state.

### Design: Tailwind CSS + Radix UI primitives

Utility-first CSS for styling. Radix UI for accessible interactive
primitives (Select, Tabs, Toggle). No full component library.

shadcn/ui allows selective component copying (only the 8-10 components
needed, not the full library), but the styling opinions still require
overriding to match Chronicle's identity, and upstream accessibility fixes
are not automatically inherited. For a 4-view dashboard with a small
component vocabulary, building from Radix primitives is lower total
overhead. MUI's bundle weight (~80 KB) and Material Design opinions are
disproportionate.

### Distribution: Pre-built assets in the Python wheel

The frontend is built in CI and the `dist/` output is included as package
data in the Python wheel. `pip install choreo-chronicle` ships a working
dashboard — consumers never need Node.js.

Docker-only distribution was rejected because it breaks the monorepo's
PyPI distribution model and excludes consumers who run Python services
directly. A separate npm package was rejected because it adds a second
install step and version synchronisation overhead.

Node.js is a **development dependency** (contributors who modify the
frontend) and a **CI dependency** (the build step), not a consumer
dependency. This matches established precedent: Django ships admin static
files in its wheel; Jupyter ships frontend assets in its wheel.

---

## Consequences

### Positive

- **URL-driven state enables reproducibility.** A platform engineer
  investigating a regression copies the URL and shares it with a colleague.
  The colleague sees the same tenant, environment, time range, and topic —
  no "which filters did you have selected?" conversations.
- **SSE integration is a cache invalidation, not a state update.** When
  `run.completed` arrives via SSE, the handler calls
  `queryClient.invalidateQueries({ queryKey: ['runs', tenant] })`. React
  Query refetches in the background. No manual state patching, no stale
  data.
- **Heatmap is faster than any library-based alternative.** The custom
  Canvas heatmap renders 9,000+ cells via `fillRect` in under 20ms — no
  D3 scale pipeline, no library overhead, no DOM elements. This exceeds
  the 500ms target by a wide margin.
- **Charting library has minimal production risk.** Recharts has 45M
  weekly npm downloads, organisation backing, stable v2.x semver, and
  monthly releases. Single-maintainer or pre-1.0 risk does not apply.
- **Bundle is measurably small.** Recharts (~50 KB) + uPlot (~15 KB) +
  React + ReactDOM (~45 KB) + React Router (~14 KB) + React Query (~13 KB)
  + nuqs + Radix + app code (~20 KB) = ~160 KB gzipped. Well within the
  200 KB target with headroom for growth.
- **`pip install` gives a complete product.** Consumers get the API server
  and the dashboard from a single `pip install choreo-chronicle`. No
  Node.js, no `npm`, no frontend build step. The wheel includes pre-built
  static assets (~500 KB-1 MB) — the same pattern Django uses for its
  admin interface.

### Negative

- **Two rendering paradigms.** Recharts (SVG) and the custom heatmap
  (Canvas) use different rendering approaches. Theming must be applied
  separately — Recharts via its component props, the heatmap via the
  shared design tokens passed to the Canvas rendering function. Mitigated
  by: the heatmap has its own colour scale (green → yellow → red) that
  does not overlap with line/bar chart styling; the theming seam is small.
- **Custom heatmap is owned code.** ~150 lines of Canvas rendering +
  tooltip positioning + colour scale interpolation that the team must
  maintain. Mitigated by: the component has a narrow interface (data grid
  in, colour-mapped cells out), comprehensive tests, and no complex
  interaction beyond hover tooltips. If maintenance becomes a burden,
  a library-based replacement (ECharts, or a future Recharts Canvas mode)
  can be swapped in — the component boundary is clean.
- **uPlot is not React-native.** A thin wrapper component (~30 lines) is
  needed to manage the Canvas lifecycle. Mitigated by: the wrapper is used
  in one place (sparkline cells in the Run Summary table).
- **No pre-built components.** Data tables, filter bars, and anomaly cards
  are built from scratch with Tailwind + Radix. Mitigated by: the
  component vocabulary is small (< 15 components) and the build cost is
  a one-time investment.
- **Wheel size increases.** Pre-built frontend assets add ~500 KB-1 MB to
  the wheel. Mitigated by: this is a one-time cost, well within PyPI's
  size limits, and smaller than many Python packages with bundled data
  (e.g. Jupyter's wheel is ~7 MB).
- **CI must build the frontend before the wheel.** The `npm ci && npm run
  build` step adds ~30 seconds. Mitigated by: CI already runs Node.js for
  other monorepo tasks; the step is cacheable.

### Neutral

- Recharts' `<Customized>` component allows embedding custom SVG elements
  (e.g. run markers on the time axis) without leaving the Recharts API.
- React Query's DevTools (opt-in, dev-only) provide cache inspection for
  debugging stale data issues during development.
- The custom heatmap component can be extracted to a shared package later
  if other projects need Canvas heatmaps. This is not planned — noted only
  because the component's interface is self-contained.

### Security Considerations

- **No `dangerouslySetInnerHTML`.** PRD-009 §Security Requirements mandates
  this. All user-supplied strings (topic names, matcher descriptions,
  branch names, environment labels) are rendered as React text nodes, never
  as HTML. This is enforced by a CI lint rule (ESLint
  `react/no-danger`) and a grep check.
- **No `eval` or `Function()`.** Neither Recharts nor uPlot uses `eval`.
  Tailwind's JIT compiler runs at build time, not runtime.
- **Custom heatmap tooltip safety.** The heatmap tooltip is a React
  component rendered via `createPortal` — it uses React text nodes, not
  `innerHTML`. This is verified by the same ESLint rule and a CI grep
  check for `innerHTML` across all `components/charts/` files.
- **CSP compatibility.** `script-src 'self'` is sufficient. No inline
  scripts. Recharts uses React's `style` prop for SVG element positioning,
  which requires `style-src 'self' 'unsafe-inline'`. Tailwind's utility
  classes compile to external stylesheets and do not require
  `unsafe-inline`. The custom Canvas heatmap does not inject any styles.
  See ADR-0021's open follow-up on nonce-based CSP as a future improvement.
- **SSE origin.** The `EventSource` connects to the same origin (same
  container). No CORS issues. The connection is tenant-scoped via query
  parameter — the server filters events (ADR-0021 §SSE Broadcast Channel).
- **URL state validation.** URL parameters are validated at the frontend
  layer before use in API calls. `useUrlState.ts` applies the same regex
  patterns the API enforces (e.g. tenant slug:
  `^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$`). Invalid params are rejected at
  the URL-state layer, not just at the API call layer. This prevents
  malformed values from reaching ARIA labels, `document.title`, or error
  messages where context-dependent encoding matters.
- **Dependency supply chain.** The frontend introduces ~15 direct npm
  dependencies. `npm ci --ignore-scripts` in the Dockerfile prevents
  post-install script attacks. `npm audit` runs in CI. Direct dependencies
  are pinned to exact versions (no ranges) in `package.json`.

---

## Implementation

### Code Structure

```
packages/chronicle/frontend/
  package.json
  tsconfig.json
  tailwind.config.ts
  vite.config.ts
  index.html
  src/
    main.tsx                     # React root, router, query client
    App.tsx                      # Layout shell, nav, tenant selector

    routes/
      RunSummary.tsx             # /runs — landing page
      RunDetail.tsx              # /runs/:runId — full run breakdown
      TopicDrilldown.tsx         # /topics/:topic — per-topic trends
      RegressionTimeline.tsx     # /regression — cross-topic view
      AnomalyFeed.tsx            # /anomalies — chronological feed

    components/
      layout/
        Shell.tsx                # Top nav, sidebar, main content area
        TenantSelector.tsx       # Tenant + environment picker
        TimeRangePicker.tsx      # Preset (24h/7d/30d/90d) + custom
        FilterBar.tsx            # Composable filter row

      charts/
        LatencyLineChart.tsx     # Recharts LineChart (p50/p95/p99)
        OutcomeBarChart.tsx      # Recharts BarChart (stacked outcomes)
        RegressionHeatmap.tsx    # Custom Canvas heatmap (topics x time)
        Sparkline.tsx            # uPlot wrapper for table cells

      data/
        RunTable.tsx             # Run list with sparkline column (virtualised rows)
        HandleTable.tsx          # Per-handle outcome/latency table
        AnomalyCard.tsx          # Single anomaly display card
        ScenarioAccordion.tsx    # Expandable scenario breakdown

      ui/
        Badge.tsx                # Severity/outcome badge
        Pagination.tsx           # Offset-based page controls
        EmptyState.tsx           # "No data" placeholder
        ErrorState.tsx           # Error display with retry action
        LoadingSkeleton.tsx      # Shimmer placeholders during fetch

    hooks/
      useUrlState.ts             # Type-safe URL param read/write (nuqs)
      useRuns.ts                 # React Query: GET /runs
      useRunDetail.ts            # React Query: GET /runs/:id
      useTopicLatency.ts         # React Query: GET /topics/:topic/latency
      useTopics.ts               # React Query: GET /topics
      useAnomalies.ts            # React Query: GET /anomalies
      useTenants.ts              # React Query: GET /tenants
      useSSE.ts                  # EventSource hook with reconnection
      useDebounce.ts             # Debounce for SSE-triggered invalidation

    api/
      client.ts                  # fetch wrapper (base URL, headers, error handling)
      types.ts                   # TypeScript types matching API response schemas

    theme/
      recharts.ts                # Recharts theme (colours, fonts, grid)
      heatmap.ts                 # Heatmap colour scale and cell config
      tokens.ts                  # Design tokens shared across Tailwind, Recharts, and heatmap
```

### Directory Conventions

- **`routes/`** — one file per URL route. Each route component composes
  hooks and presentational components. Route components are the only files
  that call `useUrlState` — they own the URL contract.
- **`components/`** — presentational components grouped by function. No
  data fetching inside components — data arrives via props.
- **`hooks/`** — all data fetching and URL state management. Each `use*.ts`
  hook wraps a single API endpoint with React Query. Hook files are the
  only files that import from `api/client.ts`.
- **`api/`** — HTTP client and TypeScript types. Types are generated from
  or manually kept in sync with the Pydantic response schemas in
  `packages/chronicle/src/chronicle/schemas/`.
- **`theme/`** — design tokens, Recharts theme, and heatmap colour config.

### State Flow

```
URL params ──────────────────────────────────────┐
  (tenant, env, topic, from, to)                 │
                                                 ▼
                                          ┌──────────────┐
                                          │  Route        │
                                          │  Component    │
                                          │  (e.g.        │
                                          │  TopicDrill-  │
                                          │  down.tsx)    │
                                          └──────┬───────┘
                                                 │ reads URL params
                                                 │ passes to hooks
                                                 ▼
                                          ┌──────────────┐
SSE stream ─────────────────────────────▶│  React Query  │
  (run.completed, anomaly.detected)      │  Cache        │
  triggers invalidateQueries()           │               │
                                          └──────┬───────┘
                                                 │ provides data
                                                 ▼
                                          ┌──────────────┐
                                          │  Presentat-   │
                                          │  ional        │
                                          │  Components   │
                                          │  (charts,     │
                                          │  tables)      │
                                          └──────────────┘
```

1. **Route component** reads URL parameters via `useUrlState`.
2. **Hooks** pass URL params as React Query keys. React Query fetches from
   the API when the key changes.
3. **SSE hook** listens for events. On `run.completed`, it invalidates
   the `['runs', tenant]` and `['topics', tenant]` query keys. On
   `anomaly.detected`, it invalidates `['anomalies', tenant]`.
4. **React Query** refetches invalidated queries in the background. The
   UI re-renders with fresh data.
5. **Presentational components** receive data via props. They never fetch
   or read URL state directly.

### URL State Contract

Each route declares its URL parameters explicitly:

```typescript
// routes/TopicDrilldown.tsx
import { useQueryState, parseAsString, parseAsIsoDateTime } from 'nuqs';

function TopicDrilldown() {
  const [tenant] = useQueryState('tenant', parseAsString.withDefault(''));
  const [env] = useQueryState('env', parseAsString);
  const [from] = useQueryState('from', parseAsIsoDateTime);
  const [to] = useQueryState('to', parseAsIsoDateTime);
  // topic comes from route param, not query string
  const { topic } = useParams<{ topic: string }>();

  const { data, isLoading, isError, error } = useTopicLatency({
    topic: topic!,
    tenant,
    environment: env ?? undefined,
    from: from ?? undefined,
    to: to ?? undefined,
    // Resolution (raw/hourly/daily) is auto-selected by the API based
    // on the time range. Not exposed as a URL param.
  });

  return (
    <div>
      <FilterBar tenant={tenant} environment={env} />
      <TimeRangePicker from={from} to={to} onChange={...} />
      {isLoading ? <LoadingSkeleton /> : isError ? (
        <ErrorState message={error.message} onRetry={() => refetch()} />
      ) : data.buckets.length === 0 ? (
        <EmptyState message="No data for this time range." />
      ) : (
        <>
          <LatencyLineChart data={data.buckets} />
          <OutcomeBarChart data={data.buckets} />
        </>
      )}
    </div>
  );
}
```

### React Query Configuration

```typescript
// main.tsx
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,       // 30s — data is fresh for 30s after fetch
      gcTime: 5 * 60_000,      // 5min — unused cache entries kept for 5min
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});
```

Per-endpoint overrides:

| Endpoint | `staleTime` | Rationale |
|----------|------------|-----------|
| `GET /tenants` | 60s | Rarely changes |
| `GET /runs` | 10s | New runs arrive via SSE-triggered invalidation; short stale time catches missed SSE events |
| `GET /runs/:id` | 5 min | Immutable once ingested |
| `GET /topics/:topic/latency` | 30s | Aggregate data; refreshed on SSE invalidation |
| `GET /anomalies` | 10s | New anomalies arrive via SSE-triggered invalidation |

### SSE Integration

```typescript
// hooks/useSSE.ts
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

export function useSSE(tenant: string) {
  const queryClient = useQueryClient();
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    if (!tenant) return;

    const source = new EventSource(
      `/api/v1/stream?tenant=${encodeURIComponent(tenant)}`
    );

    // Single debounced invalidation function for ALL SSE events.
    // During burst ingest (continuous testing), multiple run.completed
    // and anomaly.detected events arrive within seconds. Without
    // batching, each event triggers a full refetch of runs, topics,
    // and anomalies — creating refetch storms against the API.
    // The 2s debounce (PRD-009) collapses bursts into one refetch.
    const invalidateAll = () => {
      clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['runs', tenant] });
        queryClient.invalidateQueries({ queryKey: ['topics', tenant] });
        queryClient.invalidateQueries({ queryKey: ['anomalies', tenant] });
      }, 2_000);
    };

    source.addEventListener('run.completed', invalidateAll);
    source.addEventListener('anomaly.detected', invalidateAll);

    return () => {
      clearTimeout(debounceRef.current);
      source.close();
    };
  }, [tenant, queryClient]);
}
```

**Reconnection** is handled by the browser's native `EventSource`
reconnection. The server supports `Last-Event-ID` for replay (ADR-0021).
No custom reconnection logic is needed.

**Refetch storm prevention.** All SSE event types share a single debounced
invalidation function. During burst ingest (continuous testing at 5-minute
intervals with multiple tenants), the 2-second debounce window collapses
N events into one refetch wave. Without this, a burst of 5 SSE events in
10 seconds would produce 2-3 full refetch waves across multiple queries.

### Theme Integration

Design tokens are defined once and shared across Tailwind config, Recharts
component props, and the custom heatmap's Canvas rendering:

```typescript
// theme/tokens.ts
export const tokens = {
  colours: {
    // Latency percentile lines
    p50: '#3b82f6',     // blue-500
    p95: '#f59e0b',     // amber-500
    p99: '#ef4444',     // red-500

    // Outcome fills
    pass: '#22c55e',    // green-500
    fail: '#ef4444',    // red-500
    timeout: '#a855f7', // purple-500
    slow: '#f59e0b',    // amber-500

    // Heatmap scale (low → high latency)
    heatmapMin: '#dcfce7',  // green-100
    heatmapMid: '#fef08a',  // yellow-200
    heatmapMax: '#fca5a5',  // red-300

    // Severity badges
    warning: '#f59e0b',
    critical: '#ef4444',

    // Chrome
    background: '#ffffff',
    surface: '#f9fafb',     // grey-50
    border: '#e5e7eb',      // grey-200
    text: '#111827',        // grey-900
    textMuted: '#6b7280',   // grey-500
  },
  fonts: {
    body: "'Inter', system-ui, sans-serif",
    mono: "'JetBrains Mono', 'Fira Code', monospace",
  },
  spacing: {
    chartHeight: 320,
    heatmapCellSize: 16,
    sparklineHeight: 24,
    sparklineWidth: 80,
  },
} as const;
```

Recharts does not have a global theme object like Nivo. Instead, tokens
are applied via component props:

```typescript
// theme/recharts.ts
import { tokens } from './tokens';

/** Shared axis/grid/tooltip styling applied to all Recharts charts. */
export const axisStyle = {
  tick: { fontSize: 10, fill: tokens.colours.textMuted },
  axisLine: { stroke: tokens.colours.border },
};

export const gridStyle = {
  strokeDasharray: '3 3',
  stroke: tokens.colours.border,
};

export const tooltipStyle = {
  contentStyle: {
    background: tokens.colours.background,
    border: `1px solid ${tokens.colours.border}`,
    borderRadius: '6px',
    fontSize: '12px',
    fontFamily: tokens.fonts.body,
    boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
  },
};

export const lineColours = {
  p50: tokens.colours.p50,
  p95: tokens.colours.p95,
  p99: tokens.colours.p99,
};
```

The custom heatmap reads tokens directly for its colour scale interpolation
and cell sizing — no library theme layer involved.

```typescript
// theme/heatmap.ts
import { tokens } from './tokens';

/** Interpolate between green → yellow → red based on normalised value. */
export function latencyColour(value: number, min: number, max: number): string {
  const t = Math.min(1, Math.max(0, (value - min) / (max - min)));
  // Two-segment interpolation: green→yellow (0-0.5), yellow→red (0.5-1)
  // Implementation: linear RGB interpolation between token colours.
  // Actual interpolation logic omitted for brevity — ~15 lines.
  return interpolateRGB(
    t < 0.5 ? tokens.colours.heatmapMin : tokens.colours.heatmapMid,
    t < 0.5 ? tokens.colours.heatmapMid : tokens.colours.heatmapMax,
    t < 0.5 ? t * 2 : (t - 0.5) * 2,
  );
}

export const cellSize = tokens.spacing.heatmapCellSize;
```

### Interaction Patterns

#### Time Range Selection

The `TimeRangePicker` offers preset ranges (24h, 7d, 30d, 90d) and a
custom date picker. Selecting a range updates `from` and `to` URL params.
React Query refetches with the new range. The API auto-selects resolution
(raw/hourly/daily) unless explicitly overridden.

```
User clicks "30d" → URL updates ?from=2026-03-20&to=2026-04-19
                   → useTopicLatency refetches with new params
                   → API returns hourly buckets (auto-selected)
                   → LatencyLineChart re-renders
```

#### Run Marker Click-Through

Recharts' `<Line>` component supports `onClick` per data point via the
`activeDot` prop. Each point carries the `run_id` in its data payload.
Clicking navigates to `/runs/:runId`.

```
User clicks run marker on latency chart
  → router.push(`/runs/${point.payload.run_id}`)
  → RunDetail route loads
  → useRunDetail fetches full run data
```

#### Heatmap Cell Hover

The custom Canvas heatmap tracks mouse position via `onMouseMove` on the
`<canvas>` element, maps pixel coordinates to grid cell indices, and
renders a tooltip via `createPortal` as a single positioned `<div>`.
No DOM elements are created for cells — the tooltip is a React component
rendered outside the Canvas.

#### Anomaly Feed Live Updates

When a new `anomaly.detected` SSE event arrives:

1. React Query invalidates `['anomalies', tenant]`.
2. The Anomaly Feed re-renders with the new anomaly at the top.
3. A brief highlight animation (CSS transition) draws attention to the
   new card.
4. No toast or modal — the feed itself is the notification surface.

#### Filter Persistence

Tenant and environment selections persist in URL params. Navigating
between views preserves the active filters because the `TenantSelector`
reads from and writes to URL params that are shared across routes.

```
User selects tenant "platform", env "staging" on Run Summary
  → URL: /runs?tenant=platform&env=staging
User navigates to Topic Drilldown
  → URL: /topics/orders.created?tenant=platform&env=staging
  → Same filters applied
```

### Accessibility

- **Chart ARIA labels.** Each chart wrapper `<div>` includes
  `aria-label="p50, p95, p99 latency for topic {topic} over {range}"`.
  The Canvas heatmap's wrapper carries
  `aria-label="latency heatmap for {n} topics over {range}"`.
- **Data table fallback.** Every chart is accompanied by an expandable
  data table showing the raw numbers. Screen readers access the table;
  sighted users see the chart.
- **Keyboard navigation.** Radix UI primitives (Select, Tabs, Toggle)
  provide keyboard navigation out of the box.
- **Colour + shape.** Outcome types use both colour and icon/shape
  differentiation (circle for pass, triangle for fail, square for
  timeout, diamond for slow).
- **Focus management.** Filter changes and view transitions manage focus
  to prevent the user from losing their place.

### Build Tooling

- **Vite** for development server and production build. Fast HMR, native
  ESM, built-in TypeScript support.
- **Vitest** for unit and component tests (same config as Vite).
- **React Testing Library** for component interaction tests.
- **ESLint** with `react/no-danger` rule enforced (no
  `dangerouslySetInnerHTML`).
- **Production build** outputs to `frontend/dist/`.

### Distribution: Pre-Built Assets in the Python Wheel

The frontend build output (`frontend/dist/`) is included in the Python
wheel as package data. Consumers install via `pip install choreo-chronicle`
and get a working dashboard with no Node.js dependency.

**Vite output directory:**

```
packages/chronicle/frontend/dist/
  index.html
  assets/
    index-[hash].js       # ~160 KB gzipped
    index-[hash].css      # ~10 KB gzipped
```

**Hatchling configuration** (`pyproject.toml`):

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/chronicle"]

[tool.hatch.build.targets.wheel.force-include]
"src/chronicle/py.typed" = "chronicle/py.typed"
"frontend/dist" = "chronicle/static"
```

This maps `frontend/dist/` into `chronicle/static/` inside the wheel.
The FastAPI application mounts this directory:

```python
# app.py
from pathlib import Path
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

def create_app() -> FastAPI:
    app = FastAPI(...)
    # ... routers, middleware ...

    # Serve the built React frontend. The catch-all mount must be
    # registered LAST so API routes take precedence.
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
```

The `html=True` parameter enables SPA fallback: any path that does not
match an API route or a static file returns `index.html`, allowing React
Router to handle client-side routing.

The `if STATIC_DIR.exists()` guard allows the API server to start without
the frontend (e.g. during backend development or testing). The health
endpoint and API routes work regardless.

**Build pipeline (CI):**

```yaml
# .github/workflows/chronicle.yml (excerpt)
jobs:
  build:
    steps:
      # 1. Build frontend
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci --ignore-scripts
        working-directory: packages/chronicle/frontend
      - run: npm run build
        working-directory: packages/chronicle/frontend

      # 2. Build Python wheel (includes frontend/dist via force-include)
      - run: hatch build
        working-directory: packages/chronicle

      # 3. Publish to PyPI (existing workflow)
      - uses: pypa/gh-action-pypi-publish@release/v1
```

**Docker build** (unchanged from PRD-009, but now the wheel also works):

```dockerfile
# Build stage: React frontend
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY packages/chronicle/frontend/package.json packages/chronicle/frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY packages/chronicle/frontend/ .
RUN npm run build

# Runtime stage: FastAPI + built frontend
FROM python:3.12-slim
WORKDIR /app
COPY packages/chronicle/ .
COPY --from=frontend /app/frontend/dist ./src/chronicle/static
RUN pip install .
CMD ["uvicorn", "chronicle.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

**Consumer experience:**

```bash
# Option 1: pip (no Node.js, no Docker)
pip install choreo-chronicle
uvicorn chronicle.app:create_app --factory --host 0.0.0.0 --port 8000
# → API at /api/v1/*, dashboard at /

# Option 2: docker compose (unchanged)
docker compose up
# → same result
```

**Development workflow:**

```bash
# Backend development (no frontend needed)
cd packages/chronicle
pip install -e '.[test]'
pytest                          # API tests run without frontend

# Frontend development
cd packages/chronicle/frontend
npm install
npm run dev                     # Vite dev server at :5173, proxies API to :8000

# Full-stack local
# Terminal 1: uvicorn chronicle.app:create_app --factory --port 8000
# Terminal 2: cd frontend && npm run dev
# Vite proxies /api/* to uvicorn; hot-reloads frontend changes
```

**Vite proxy configuration** for local development:

```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  // ...
});
```

### Migration Path

Not applicable — greenfield.

### Timeline

- **Phase 5a:** Project scaffolding (Vite, Tailwind, React Router, React
  Query), layout shell, tenant selector, API client + TypeScript types.
  Hatchling `force-include` configuration. Vite proxy for local dev.
  CI step for `npm ci && npm run build` before `hatch build`.
- **Phase 5b:** Run Summary view (run table, sparklines, pagination,
  virtualised rows).
- **Phase 5c:** Topic Drilldown view (latency line chart, outcome bar
  chart, time range picker).
- **Phase 5d:** Regression Timeline view (custom Canvas heatmap,
  regression table).
- **Phase 5e:** Anomaly Feed view (anomaly cards, SSE integration, live
  updates).
- **Phase 5f:** Polish (loading skeletons, empty states, error states,
  accessibility audit, bundle optimisation, `pip install` integration
  test).

---

## Validation

### Success Metrics

- **Heatmap renders 100 topics x 90 buckets in under 500ms.** Measured by:
  a Vitest benchmark that renders `RegressionHeatmap` with synthetic data
  and asserts canvas paint completes within the target.
- **Bundle size under 200 KB gzipped.** Measured by: Vite build output
  checked in CI. Build fails if gzipped JS exceeds 200 KB.
- **No `dangerouslySetInnerHTML` in the codebase.** Measured by: ESLint
  rule `react/no-danger` set to `error`. CI lint step fails on violation.
- **Every view is fully described by its URL.** Measured by: Vitest tests
  that render a route with specific URL params, verify the correct API
  calls are made, and assert that rendering the same URL twice produces
  identical output.
- **SSE invalidation triggers refetch within 2 seconds.** Measured by:
  integration test that emits a mock SSE event and asserts
  `invalidateQueries` is called with the correct key within the debounce
  window.
- **Dashboard initial page load under 3 seconds.** Measured by: Playwright
  test that navigates to the Run Summary view and asserts
  `DOMContentLoaded` + data render completes within the target. Tested
  against a local Docker Compose deployment.
- **`pip install` delivers a working dashboard.** Measured by: a CI
  integration test that installs the wheel in a clean venv (no Node.js),
  starts uvicorn, and asserts that `GET /` returns `index.html` with a
  200 status code.
- **Wheel size under 2 MB.** Measured by: CI check on `hatch build`
  output. The wheel includes Python source (~100 KB) + frontend assets
  (~500 KB-1 MB). Fails if the wheel exceeds 2 MB.

### Monitoring

- **Bundle size regression.** CI reports bundle size on every PR. A
  size-limit configuration warns when gzipped JS grows by more than 10 KB.
- **Lighthouse CI.** Performance, accessibility, and best practices scores
  checked on every PR. Threshold: performance > 90, accessibility > 95.
- **React Query DevTools** (dev-only) for cache debugging during
  development.

---

## Related Decisions

- [ADR-0021](0021-chronicle-api-structure.md) — Backend API structure.
  Defines the response schemas, SSE events, and endpoint contracts that
  the frontend consumes.
- [PRD-009](../prd/PRD-009-chronicle-reporting-server.md) — Product
  requirements including the four views, security constraints (no
  `dangerouslySetInnerHTML`), Canvas heatmap requirement, and deployment
  model.

---

## References

- [Recharts documentation](https://recharts.org/) — React-native SVG
  charting library (line, bar, area, composed charts)
- [uPlot](https://github.com/leeoniya/uPlot) — lightweight time-series
  charts for sparklines
- [TanStack Query (React Query)](https://tanstack.com/query/latest) —
  server state management
- [TanStack Virtual](https://tanstack.com/virtual/latest) — row
  virtualisation for large tables with sparklines
- [nuqs](https://nuqs.47ng.com/) — type-safe URL search parameter state
  for React
- [Radix UI](https://www.radix-ui.com/) — accessible headless UI
  primitives
- [Tailwind CSS](https://tailwindcss.com/) — utility-first CSS framework
- [Vite](https://vitejs.dev/) — frontend build tool
- [Canvas 2D API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API) —
  MDN reference for the custom heatmap component

---

## Notes

- **Open follow-up — API type generation.** The TypeScript types in
  `api/types.ts` are manually maintained to match the Pydantic schemas in
  `packages/chronicle/src/chronicle/schemas/`. If schema drift becomes a
  problem, consider generating TypeScript types from the OpenAPI spec
  (`/api/v1/openapi.json`) using `openapi-typescript`. **Owner:** Chronicle
  frontend.
- **Open follow-up — design tokens as CSS custom properties.** The current
  approach defines tokens in TypeScript and passes them to Tailwind config,
  Recharts component props, and the heatmap colour scale. An alternative is
  to define tokens as CSS custom properties and read them in both contexts.
  This would allow runtime theme switching (for future dark mode) without a
  rebuild. Defer until dark mode is in scope. **Owner:** Chronicle frontend.
- **Open follow-up — Canvas accessibility.** The Canvas heatmap is not
  screen-reader accessible. The accompanying data table (expandable below
  the heatmap) provides the accessible alternative. If accessibility
  requirements tighten, consider an ARIA live region that announces the
  hovered cell's values. **Owner:** Chronicle frontend.
- **Open follow-up — `unsafe-inline` for styles.** Recharts uses React's
  `style` prop for SVG element positioning, which requires
  `style-src 'unsafe-inline'` in the CSP. The custom Canvas heatmap does
  not inject styles. If the security posture requires removing
  `unsafe-inline`, investigate Recharts' compatibility with nonce-based
  CSP or consider moving all inline styles to CSS classes. See also
  ADR-0021's corresponding open follow-up. **Owner:** Chronicle frontend.
- **Open follow-up — row virtualisation.** The Run Summary table uses
  `@tanstack/react-virtual` to virtualise rows containing uPlot sparkline
  Canvas instances. Off-screen rows destroy their Canvas context to prevent
  memory and repaint overhead. If the table grows beyond 100 rows per page,
  monitor GPU-backed bitmap memory (~4 bytes per pixel per visible Canvas).
  **Owner:** Chronicle frontend.

**Last Updated:** 2026-04-19
