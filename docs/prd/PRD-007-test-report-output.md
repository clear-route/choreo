# Test Report Output — Interactive HTML + Structured JSON — Product Requirements Document

**Status:** Draft (v2 — post-review)
**Created:** 2026-04-17
**Last Updated:** 2026-04-17
**Owner:** Platform / Test Infrastructure
**Stakeholders:** Platform Engineering, QA / Release Engineering

---

## Revision note (v2 vs. v1)

v2 incorporates a six-actor parallel review (architect, code review, security,
performance, test-automation, frontend). Material changes from v1:

- **Packaging.** The reporter ships as a **separate distribution `core-reporter`**, not as a submodule of `core`. `core` gains only a small observer seam; pytest is never in `core`'s dependency graph.
- **xdist.** v1 now explicitly supports `pytest-xdist` via per-worker JSON files merged by the controller at session-finish.
- **Security.** New Functional Requirement sections 9–12 codify an HTML rendering contract (no `innerHTML` of JSON-derived strings; escape `<` → `\u003c` in inlined JSON), hardened directory-wipe semantics, hardened git shell-out, and default credential-shape redaction.
- **HTML packaging.** Safari `file://` blocks `fetch`; v2 always inlines `results.json` into a `<script type="application/json">` block. The sibling `results.json` file is retained only as a tooling artefact and is never fetched by the HTML.
- **HTML testability.** A `data-*` attribute contract is now a hard requirement so structural tests avoid text-matching.
- **Performance.** Memory NFR restated with a worst-case bound; timeline lazy-rendering on scenario-expand is now required; git lookup runs on a background thread started at session-start.
- **Schema.** Field names aligned with existing `ScenarioResult` (`timeline_dropped`, not `dropped_entries`). A normalisation table maps `Outcome` enum values to serialised strings at each level. `skip_reason` added.
- **Outcome vocabulary.** Single normalisation table replaces three inconsistent vocabularies.
- **Open questions closed.** Most v1 open questions are now resolved (see "Decisions Already Made"). Two remain.

---

## Executive Summary

Downstream consumer repos run the harness under `pytest` and get nothing back except the standard pytest terminal output. When a scenario fails, the author reads the terminal `failure_summary()` and hopes that is enough. The rich per-scenario data already captured in `ScenarioResult` (handles, outcomes, latencies, timeline entries from PRD-006) is invisible outside one printed block.

This PRD introduces a **separate installable package `core-reporter`** that provides an opt-in pytest plugin. At the end of every pytest run it emits a directory containing:

1. A versioned **`results.json`** — full structured capture (run, tests, scenarios, handles, timelines, expected-vs-actual, stdout/stderr/log, traceback).
2. A self-contained **`index.html`** with a small amount of vanilla JavaScript that consumes an **inlined** copy of the same JSON and renders an interactive report: filter/search, collapsed-by-default tree, drill-down into scenario timelines, side-by-side expected/actual diffs, pretty-printed JSON payloads, per-level numeric summaries.

Aggregate reporting across runs, historical trending, and dashboards remain out of scope for v1; `results.json` is versioned so that a later system can consume many runs' worth.

---

## Problem Statement

### Current State

Every test run produces a `ScenarioResult` per scenario, with:

- A tuple of `Handle` objects — each with `topic`, `outcome` (`PASS`/`FAIL`/`TIMEOUT`/`SLOW`/`PENDING`), `latency_ms`, `budget_ms`, and the matched or last-rejected message.
- A `timeline` tuple of `TimelineEntry` objects on non-passing results (PRD-006), with a matching `timeline_dropped` integer count.
- A `failure_summary()` string.

That data is discarded at the end of the pytest process. Authors read the terminal and the `assert_passed()` exception text. Nothing is archived; nothing is shareable outside a terminal screenshot.

### User Pain Points

- **Local debugging is terminal-bound.** The timeline that PRD-006 captures on failure is printed once to stdout and lost. An author debugging a flake cannot scroll, filter, or compare.
- **CI runs are opaque after the fact.** A failed run is `exit 1` and a log file. The author cannot see which scenarios were slow, how payloads actually differed, or what the correlated message stream looked like.
- **No shared artefact.** Two engineers looking at the same failure cannot point at `report/index.html#test-foo::scenario-fill` — they screenshot the terminal.
- **`expect` mismatches are hard to read in text.** When `contains_fields({"qty": 1000, "side": "BUY", "price": 101.5})` fails against an actual payload with 30 fields, a one-line `failure_summary()` cannot show both sides legibly.

### Business Impact

- Diagnosis time for CI failures today is dominated by "reproduce locally and add prints". A checked-in artefact with the full structured capture cuts that loop.
- Onboarding new engineers to the harness requires them to read the scenario DSL to interpret terminal output. An interactive report with drill-down is self-explanatory.

---

## Goals and Objectives

### Primary Goals

1. **A separate package `core-reporter`** that consumers install alongside `core`, providing an opt-in pytest plugin.
2. **At the end of every pytest run, write `<report-dir>/results.json` + `<report-dir>/index.html`.** Default path `./test-report/`; overridable via `--harness-report=<path>` (CLI wins) or `HARNESS_REPORT_DIR` env var (plugin-layer only; `core` never reads env).
3. **`results.json` is a versioned, documented schema** (`schema_version: "1"`), described in `docs/schemas/test-report-v1.json` (JSON Schema draft 2020-12).
4. **`index.html` is a single self-contained HTML file.** It embeds an inlined JSON block and never performs runtime `fetch` calls. A sibling `results.json` is also written for tooling.
5. **Collapsed by default; failures auto-expanded.** A 100-test run opens with failing tests visible.
6. **Filter and search** over test name, file, pytest marker, scenario name, outcome, duration. Free-text over test and scenario names.
7. **Drill-down per scenario:** PRD-006 timeline rendered chronologically; lazy-mounted on scenario expand.
8. **Expected-vs-actual side-by-side** for failing handles.
9. **Numeric summaries only (no charts in v1).** Per-run, per-test, per-scenario durations and handle-latency p50/p95/p99.
10. **pytest-xdist supported in v1** via per-worker partial files + controller-side merge at `pytest_sessionfinish` on the master process.
11. **Default credential-shape redaction** on payloads, stdout, stderr, log, and traceback, before anything reaches disk.

### Success Metrics

- A consumer repo adds `core-reporter` to its test dependencies and enables the plugin; `pytest` produces `test-report/` on every invocation.
- For a run of 100 tests where 3 fail, opening `index.html` shows the 3 failures expanded and 97 collapsed; the author can reach the failing payload's expected-vs-actual diff in ≤2 clicks from the page load.
- `results.json` round-trips: writing it, reading it back, and rendering `index.html` from the re-read file produces the same UI.
- The report is a static directory: uploading it as a GitHub Actions artefact is the only step needed to share.
- The generated directory is ≤5 MB for a **typical** 100-test run; worst-case pathological runs are capped at 10 MB and abort with a clear error past that.
- Rendering 100 tests with up to 1000 scenarios and up to 256 timeline entries per scenario (PRD-006 ring-buffer cap) completes first meaningful render in <1 second on a laptop.

### Non-Goals

- **Aggregate / historical reporting.** Trend lines, regression detection, flake ranking. Deferred.
- **Charts.** No latency histograms, no Gantt, no sparklines in v1.
- **Raw transport frame rendering.** Payloads shown are the codec-decoded objects flowing across the harness bus.
- **Run-over-run comparison UI.** One run per report.
- **Re-run / copy-command buttons.** Cut from v1.
- **Live / streaming updates during a run.** Report is produced once at session teardown.
- **Hosted dashboards, authentication, CI-provider-specific integrations.**
- **Dark mode, print stylesheet, localisation.** v1 is light-theme, on-screen, UK English only.
- **IndexedDB / localStorage persistence.** The report is stateless between loads.
- **Domain-level PII redaction.** Only credential-shape keys are redacted automatically (see §12); domain PII remains a consumer concern.

---

## User Stories

### Primary User Stories

**US-1. As a** test author whose scenario failed locally,
**I want to** open `test-report/index.html` and see the failing scenario's timeline and expected-vs-actual payloads without re-running,
**So that** I can diagnose in one pass instead of iteratively adding prints.

**Acceptance Criteria:**

- [ ] Running `pytest` in a consumer repo that has enabled the plugin writes `test-report/index.html` and `test-report/results.json`.
- [ ] Opening `index.html` in a browser (no server needed; `file://` works) lists every test in the run, with failing tests expanded and passing tests collapsed by default.
- [ ] Every test element carries `data-nodeid="<nodeid>"` and `data-outcome="<test-outcome>"`; every scenario element carries `data-scenario-name="<name>"` and `data-outcome="<scenario-outcome>"`. Outcome strings follow the normalisation table in §3.
- [ ] For a failing scenario, its timeline is mounted to the DOM when the scenario is expanded (not on page load) and renders chronologically with `offset_ms`, `topic`, `action`, `detail` per entry.
- [ ] For a failing handle, clicking it reveals two side-by-side `<pre>` panels with `data-panel="expected"` and `data-panel="actual"`, each pretty-printed.
- [ ] Page load and first meaningful render complete in under 2 seconds on a 100-test run with typical scenario sizes.

---

**US-2. As a** CI reviewer,
**I want to** download the `test-report/` directory as a GitHub Actions artefact and open `index.html` locally,
**So that** I can triage failures without checking out the repo or re-running tests.

**Acceptance Criteria:**

- [ ] `test-report/` is a flat directory containing exactly `index.html`, `results.json`, and a `.harness-report` sentinel file. No hidden dependencies, no subdirectories.
- [ ] `index.html` does not perform runtime `fetch`; its JSON is inlined in a `<script type="application/json" id="harness-results">` block. The sibling `results.json` is identical in content and exists only for tooling.
- [ ] The header element carries `data-field` attributes for each metadata item (`data-field="transport"`, `data-field="git_sha"`, etc.) so structural tests can assert metadata presence without coupling to text layout.

---

**US-3. As a** tooling engineer,
**I want to** post `results.json` to an external system later,
**So that** we can build cross-run analytics without re-parsing HTML.

**Acceptance Criteria:**

- [ ] `results.json` has a top-level `schema_version: "1"` string.
- [ ] The schema is documented in `docs/schemas/test-report-v1.json` (JSON Schema draft 2020-12) with `required` arrays on every object so field renames fail validation loudly.
- [ ] Adding a new optional field is a minor bump (`"1.1"`); removing or renaming is a major bump (`"2"`).

---

**US-4. As a** test author with 200 passing tests and 3 failures,
**I want to** filter to only failures and search within them,
**So that** I can ignore the noise.

**Acceptance Criteria:**

- [ ] A status filter (pills) toggles visibility of `passed`, `failed`, `slow`, `skipped`, `errored` at the test level.
- [ ] A free-text search box filters the test list by substring on `nodeid`, test name, or scenario name.
- [ ] A marker filter dropdown lists every pytest marker seen in the run and toggles visibility.
- [ ] A duration filter allows "only show tests slower than Nms" as a numeric input.
- [ ] Filter state is mirrored into `location.hash` so an author can share a URL that reproduces a filtered view.
- [ ] Filter behaviour is covered by a **manual browser test checklist** in `tests/reporter/MANUAL.md` (not pytester — these are interactive behaviours). See §Testing Strategy.

---

**US-5. As a** CI pipeline using pytest-xdist,
**I want to** run tests in parallel across workers and still get a single merged report,
**So that** xdist users are not second-class.

**Acceptance Criteria:**

- [ ] Under `pytest -n <N>`, each worker writes `test-report/_partial/worker-<id>.json` during its `pytest_sessionfinish`.
- [ ] The controller's `pytest_sessionfinish` (detected by absence of `config.workerinput`) reads every `worker-*.json`, merges the `tests` arrays, recomputes `run.totals` and `run.duration_ms`, and writes the final `results.json` + `index.html`.
- [ ] The `_partial/` directory is deleted after a successful merge.
- [ ] If a worker crashes without writing its partial, the merge produces a best-effort report with a top-level `run.incomplete_workers: [...]` field and a visible banner in the HTML.

---

## Functional Requirements

### 1. Package + plugin layout

- **New distribution `core-reporter`** with its own `pyproject.toml`, shipped in the same repo (monorepo) under `packages/core-reporter/`. Depends on `core` and `pytest>=8.0`. `core` itself has no dependency on `pytest` or `core-reporter`.
- `core-reporter` exposes a pytest plugin registered via entry point `pytest11 = "core_reporter = core_reporter.plugin"`, so a consumer only needs `pip install core-reporter[test]` to activate it; no `pytest_plugins` edit required. Consumers who want explicit opt-in can pin via `-p core_reporter.plugin` or `addopts` in their `pyproject.toml`.
- Optional opt-out: `--harness-report-disable` or `HARNESS_REPORT_DIR=""` (the plugin reads env; `core` does not).
- The plugin:
  - Registers `pytest_addoption` for `--harness-report=<path>` (default `./test-report`) and `--harness-report-disable`.
  - Reads `HARNESS_REPORT_DIR` as a fallback **in the plugin only**. Flag wins over env var.
  - Installs hooks for `pytest_sessionstart`, `pytest_runtest_protocol` (wrapper to set the nodeid contextvar), `pytest_runtest_logreport`, `pytest_sessionfinish`.
  - Registers a scenario observer via `core._reporting.register_observer(...)`.
  - On `pytest_sessionstart`, kicks off git metadata lookup on a background thread (see §11) so the shell-out is already resolved by `sessionfinish`.

### 2. Scenario observation hook (the only `core` surface change)

- New module `packages/core/src/core/_reporting.py` with:
  - A contextvar `current_test_nodeid: ContextVar[str | None] = ContextVar("harness_current_test_nodeid", default=None)`.
  - `register_observer(cb: Callable[[ScenarioResult, str | None, bool], None]) -> None` — the third bool argument is `completed_normally`: `True` when `_do_await_all` returned the result, `False` when the scope exited via an exception before `await_all` ran.
  - `unregister_observer(cb) -> None`.
  - `_emit(result, *, completed_normally) -> None` — internal; iterates observers, swallows exceptions from them with `warnings.warn(...)`, never propagates.
- Instrumentation:
  - `_do_await_all` calls `_emit(result, completed_normally=True)` after constructing the `ScenarioResult`.
  - `_ScenarioScope.__aexit__` calls `_emit(partial_result, completed_normally=False)` when the scope body raised before `await_all` ran. `partial_result` carries the handles in their current state (typically `Outcome.PENDING`) plus the captured timeline so far; the observer decides what to do with it.
- This is **the only library-surface change in `core`**. Env vars, pytest, and HTML all live in `core-reporter`.
- Observer errors never propagate. Observer calls are synchronous with the emitting code path; an observer that blocks delays the scenario scope — stated to the reporter author as a constraint.

### 3. `results.json` schema (v1)

Top-level:

```json
{
  "schema_version": "1",
  "run": { "...": "..." },
  "tests": [ { "...": "..." } ]
}
```

**Outcome normalisation table.** One mapping, applied consistently:

| Source                                       | Serialised string |
|----------------------------------------------|-------------------|
| `Outcome.PASS`                               | `"pass"`          |
| `Outcome.FAIL`                               | `"fail"`          |
| `Outcome.TIMEOUT`                            | `"timeout"`       |
| `Outcome.SLOW`                               | `"slow"`          |
| `Outcome.PENDING`                            | `"pending"`       |
| pytest passed                                | `"passed"`        |
| pytest failed                                | `"failed"`        |
| pytest error (setup/teardown)                | `"errored"`       |
| pytest skipped                               | `"skipped"`       |
| Test-level derivation: passed but any handle `SLOW` | `"slow"`    |
| Scenario-level derivation (worst-handle)     | `"pass"` / `"fail"` / `"timeout"` / `"slow"` |

Test-level outcomes are one of `{passed, failed, errored, skipped, slow}`. Scenario-level outcomes are one of `{pass, fail, timeout, slow}`. Handle-level outcomes are the full `Outcome` enum.

Filter pills in the HTML operate at the **test level only**; scenario-level and handle-level outcomes are visible inside the drill-down.

`run`:

- `started_at` — ISO8601 UTC
- `finished_at` — ISO8601 UTC
- `duration_ms` — number
- `totals` — object with `passed`, `failed`, `errored`, `skipped`, `slow`, `total` (integers)
- `transport` — class name as a string
- `allowlist_path` — string or null
- `python_version` — string
- `harness_version` — string (from `core.__version__`)
- `reporter_version` — string (from `core_reporter.__version__`)
- `git_sha` — string or null
- `git_branch` — string or null
- `environment` — string or null (from `HARNESS_ENV` env var, read by the plugin only)
- `hostname` — string (`socket.gethostname()`)
- `xdist` — object or null: `{ "workers": N, "incomplete_workers": [...] }` when xdist is in use, else null
- `truncated` — boolean; `true` only if any size cap (§5) fired

`tests[]`:

- `nodeid` — pytest nodeid (`tests/test_foo.py::test_bar[param]`)
- `file` — relative path
- `name` — function name
- `class` — class name or null
- `markers` — array of marker names as strings
- `outcome` — one of `passed`, `failed`, `errored`, `skipped`, `slow`
- `duration_ms` — number
- `traceback` — string or null
- `stdout` — string (truncated — §5)
- `stderr` — string (truncated)
- `log` — string (truncated)
- `skip_reason` — string or null (from `pytest.mark.skip(reason=...)` or an `xfail` reason; null for non-skipped tests)
- `worker_id` — string or null (`"gw0"` under xdist, null otherwise)
- `scenarios[]` — array of scenario objects; may be empty

Per-scenario:

- `name` — string passed to `harness.scenario("fill")`
- `correlation_id` — string
- `outcome` — one of `pass`, `fail`, `timeout`, `slow`
- `duration_ms` — number
- `completed_normally` — boolean (see §2)
- `handles[]` — array
- `timeline[]` — array of timeline-entry objects
- `timeline_dropped` — integer (source: `ScenarioResult.timeline_dropped`)
- `summary_text` — string (verbatim `failure_summary()`)

Per-handle:

- `topic` — string
- `outcome` — handle-level string from the normalisation table
- `latency_ms` — number or null
- `budget_ms` — number or null
- `matcher_description` — string (from `Handle.matcher_description`)
- `expected` — object/array/scalar or null. Sourced from `matcher.expected_shape()` if implemented, else `null`. Custom matchers without `expected_shape()` serialise only `matcher_description`.
- `actual` — object/array/scalar or null. For `PASS`/`SLOW`, the matched payload (from `Handle.message`). For `FAIL`, the last-rejected payload (from the new `Handle.last_rejection_payload` property). For `TIMEOUT` or `PENDING`, `null`.
- `attempts` — integer (from `Handle.attempts`)
- `reason` — string (from `Handle.reason` for PASS/SLOW, or `Handle.last_rejection_reason` for FAIL)
- `truncated` — boolean, `true` if the payload was truncated per §5

Per-timeline-entry:

- `offset_ms` — number
- `wall_clock` — ISO8601 string
- `topic` — string
- `action` — `TimelineAction` string value
- `detail` — string

**All keys listed above are required** in `docs/schemas/test-report-v1.json`; fields whose value may be `null` are `required` with `"type": ["string", "null"]` etc. Missing-field renames are caught by schema validation.

### 4. `index.html` — structure, contract, and interactions

**Layout.** Header (sticky) with run metadata; left column (~30%) with file-grouped test list; right column (~70%) detail pane; top toolbar with filter pills, marker dropdown, search input, duration input.

**Required DOM contract (machine-readable for tests):**

- Root element has class `harness-report` and `data-schema-version` matching the JSON.
- Every test list item carries `data-nodeid="<nodeid>"` and `data-outcome="<test-outcome-string>"`.
- Every scenario element carries `data-scenario-name="<name>"` and `data-outcome="<scenario-outcome-string>"`.
- Every handle element carries `data-topic="<topic>"` and `data-outcome="<handle-outcome-string>"`.
- Expected/actual panels carry `data-panel="expected"` / `data-panel="actual"`.
- Header metadata items carry `data-field="<field-name>"` for each field in `run.*`.
- Inlined JSON lives at `<script type="application/json" id="harness-results">…</script>`.

**Accessibility:**

- The test list uses `role="tree"` at the root, `role="treeitem"` per node, `aria-expanded="true|false"`.
- Outcome badges use colour **and** an icon/letter (`P`/`F`/`S`/`T`/`E`), not colour alone.
- A live region `<div aria-live="polite" id="filter-announce">` announces "N tests shown" after every filter change.
- Diff panels carry `aria-label="expected"` / `aria-label="actual"` on the `<pre>` so screen readers distinguish them.
- Focus returns to the last-interacted filter control after a filter state change; focus is not dumped to `<body>`.
- Keyboard: arrow keys walk the tree, Enter / Space toggles expand, `/` focuses the search input.

**Default state.** Failing and `SLOW` tests auto-expanded; passing tests collapsed. Status filter defaults to "non-passing only" (passing pill un-highlighted); single-click toggles passing back on.

**State model (required implementation shape):**

- A single `state` object holds filter + selection state: `{statuses: Set<string>, markers: Set<string>, search: string, slowerThanMs: number | null, selectedNodeId: string | null, expanded: Set<string>}`.
- One `render(state)` function applies visibility by toggling `data-visible="true|false"` (not by removing DOM nodes, not by `innerHTML` rewrites) and expand state via `aria-expanded` / `hidden` attributes.
- `state` is mirrored into `location.hash` (URL-encoded query-string style), so `foo.html#status=failed&search=fill` reproduces a view.

**Drill-down.** Handles listed with outcome badge, topic, latency/budget, matcher description. Expanding a handle reveals the side-by-side `expected` vs `actual` panels. The scenario's timeline is **mounted on scenario expand, not on page load** (lazy render); it does not exist in the DOM until the user expands the scenario.

**Numeric summaries.**

- Run header: total, passed/failed/slow/skipped/errored, total duration.
- Per-test row: duration, # scenarios, # handles, # matched, # slow, # timed out.
- Per-scenario row: duration, # timeline entries, # handles by outcome, p50/p95/p99 latency of its handles.
- Run-level p50/p95/p99 of all handles with a recorded `latency_ms`, in a stats block.

### 5. Data volume controls

- **Per-payload cap: 8 KB** of JSON-encoded `actual` per handle (reduced from v1's 32 KB). On overflow, store `{"_truncated": true, "_original_bytes": N, "_head": "<first 8000 bytes>"}`.
- **Per-field string cap: 2 KB.** Longer strings stored as `{"_truncated": true, "_original_bytes": N, "_head": "<first 2000 chars>"}`.
- **Truncation marker shape is unified.** Every truncation uses the `{"_truncated": true, "_original_bytes": N, "_head": ...}` object. No ad-hoc `"<string of N bytes, truncated>"` markers.
- **Per-stream cap (stdout/stderr/log): 64 KB each, tail-kept.** Head-dropped with a leading `[<N bytes truncated>]` marker.
- **Timeline length:** bounded by PRD-006's ring buffer (256 per scenario). Serialised as `timeline_dropped`.
- **Per-array caps:** `tests`, `scenarios` per test, `handles` per scenario, `timeline` per scenario are each capped (1000 / 100 / 100 / 256). Excess is dropped with a `<parent>._dropped` sibling integer.
- **Total-file hard cap: 10 MB.** If exceeded, the reporter **refuses to write** and emits a pytest warning. `run.truncated: true` is set on the partial in-memory document, but no JSON/HTML is written. Silent multi-MB uploads are not acceptable.
- **Size caps are applied by walking the object tree once** (truncate strings > 2 KB during walk, then encode). Two-pass encode-then-measure is forbidden on the hot path.

### 6. CLI surface

```
pytest --harness-report=test-report           # default path
pytest --harness-report=/tmp/foo              # custom path
pytest --harness-report-disable               # skip report generation this run
HARNESS_REPORT_DIR=/tmp/foo pytest            # via env (plugin reads only)
HARNESS_ENV=dev pytest                        # stamped into run.environment
```

Env vars are read **only** by `core_reporter.plugin`. `core` itself reads no env vars.

### 7. HTML packaging (no runtime fetch)

- `index.html` inlines its CSS in a `<style>` block, its JavaScript in a `<script>` block, and `results.json` in a `<script type="application/json" id="harness-results">` block.
- There are no separate `.css` / `.js` files, no CDNs, no external font loads, no `fetch` calls at runtime.
- The sibling `results.json` file is byte-identical to the inlined payload but exists purely as a tooling artefact. The HTML never reads it.
- Inlined JSON is escaped before embedding: `<` → `\u003c`, `\u2028` and `\u2029` replaced with their `\u...` escape sequences. This prevents `</script>` break-out and parser incompatibilities across engines.
- All CSS selectors are scoped under `.harness-report` (no bare `body {}`, no `* { reset }`), so embedding the HTML in a CI provider's iframe cannot leak styles either direction.

### 8. Failure modes of the reporter itself

- If collecting or writing the report raises, the reporter logs a warning to the pytest terminal and lets the run finish with whatever exit code pytest would otherwise have produced. The reporter is never a test-failure source.
- If a payload cannot be JSON-serialised (e.g. contains a custom class instance), the payload is replaced with `{"_unserialisable": true, "_repr": "<truncated repr>"}` and the scenario is still recorded.
- If a scenario observer raises, the error is swallowed via `warnings.warn(...)` and the scenario proceeds.

### 9. HTML rendering contract (security)

- All payload-derived content is injected into the DOM via `textContent` or `document.createTextNode`. `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, and `new Function` are forbidden anywhere in the bundled JavaScript.
- The JSON syntax highlighter must tokenise the input and emit individual DOM nodes with `textContent`; no concatenated HTML strings.
- `href` and `src` attributes built from JSON are rejected (replaced with `#` or omitted). The report never produces clickable links from test payload data.
- Header metadata fields (`git_branch`, `environment`, `hostname`) are rendered via `textContent` — branch names and env values can legally contain HTML-significant characters.
- A lint step (simple regex scan of `dist/index.html`'s inlined JS) in the reporter's own test suite forbids the banned sinks; refactors that introduce them fail CI.

### 10. Directory-wipe safety

- Refuse with a clear error if `--harness-report` resolves to: `/`, `/home`, the user's home directory, any path whose resolved parent is not writable-owned by the invoking user, or any path containing `.git` / `.svn` / `.hg` at its root.
- Write to a temp sibling directory `<report-dir>.tmp-<pid>/`, then **atomic rename** to `<report-dir>/` at the end. On second and subsequent runs, the existing `<report-dir>/` is removed only after the temp sibling is fully written and validated.
- A `.harness-report` sentinel file is written inside the report directory. The reporter refuses to wipe a pre-existing directory that does not contain the sentinel — this prevents accidentally nuking a user-provided directory.
- Directory operations use `os.lstat`, not `os.stat`; symlinks inside the report directory are refused. Files are opened with `O_NOFOLLOW | O_EXCL` where the host OS supports it.

### 11. Git shell-out hardening

- Git lookup runs on a background `threading.Thread` started in `pytest_sessionstart`; the result is joined (with a 500ms wait) in `pytest_sessionfinish`.
- Subprocess invocation: `subprocess.run(["git", "rev-parse", "HEAD"], shell=False, capture_output=True, timeout=0.5, cwd=<explicit>, env={...})`.
- Environment scrubbing for the subprocess: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`, `GIT_OPTIONAL_LOCKS=0`, `HOME=<tempdir>`. This defeats a malicious repo-local `.git/config` with `core.fsmonitor` / `core.sshCommand` that would otherwise execute on `rev-parse`.
- On timeout, non-zero exit, or exception, `git_sha` and `git_branch` are `null`. No error is surfaced to the user.

### 12. Default credential-shape redaction

- Before any payload / stream / traceback is serialised, a redactor walks the object tree and replaces the values of keys matching a credential-shape regex with `"<redacted>"`. Regex (case-insensitive, on keys): `(password|passwd|pwd|secret|token|api[_-]?key|authorization|auth|cookie|bearer|private[_-]?key|client[_-]?secret)`.
- Traceback, stdout, stderr, and log content are scanned for common credential-value shapes (`authorization: bearer <token>`, `x-api-key: <token>`, etc.) and the token body is replaced with `<redacted>`.
- A `run.redactions: { "fields": N, "stream_matches": M }` counter records how many redactions occurred so the author knows the report is not a 1:1 copy of their data.
- Consumers who need domain-level PII redaction register an additional redactor via `core_reporter.register_redactor(fn)`. The default redactor always runs first; consumer redactors run after.

---

## Non-Functional Requirements

### Performance

- **End-of-session report generation** completes in under 1 second for a 100-test run on a typical laptop, **given the background-thread git lookup (§11)**. The hot serialisation path does not shell out.
- **HTML first meaningful render** under 1 second for a 100-test run with up to 256 timeline entries per scenario. Scaling beyond 1000 tests is not a v1 target.
- **Hot-path overhead on the green run.** The scenario observer retains a reference to each `ScenarioResult` (O(1) per scenario). Payloads are not serialised per-scenario; serialisation happens once at `pytest_sessionfinish`. Retained references prevent Python GC of scenario objects until session-finish; this is intentional and bounded by the per-payload and per-array caps (§5).
- **Lazy timeline mount.** Timelines are DOM-mounted on scenario-expand. A page load with 100 tests × 10 scenarios renders 1000 scenario rows but zero timeline rows until interacted with.

### Memory

- **Per-test in-memory capture** is bounded by the size caps in §5. For a typical run of 100 tests with 3 handles and ~20 timeline entries per scenario, in-memory reporter state is ≤50 MB.
- **Worst-case bound** with every cap saturated (100 tests × 10 scenarios × 10 handles × 8 KB `actual` + 8 KB rejection-payload + 64 KB × 3 streams per test) is ~200 MB. This is the documented worst case and why the 10 MB file cap (§5) exists as a separate backstop.

### Correctness of the JSON contract

- `docs/schemas/test-report-v1.json` uses JSON Schema draft 2020-12 with `"required"` keywords on every documented object so field renames fail schema validation loudly.
- A test-suite fixture loads the schema, validates the Appendix A example, and asserts the Appendix A example is also usable as input to the HTML generator. This catches schema/example/implementation drift in a single test.

### Security / data handling

- See §9 (rendering contract), §10 (directory-wipe safety), §11 (git shell-out hardening), §12 (credential redaction). Together these are the security-functional requirements; this NFR section only declares their presence.
- `.gitignore` / `.dockerignore` guidance for `test-report/` is bundled into the README snippet the plugin documentation produces. The plugin warns (does not error) when `test-report/` lives inside a git repo without a matching `.gitignore` entry.

### Accessibility

- See §4 "Accessibility". No `!important` overrides on font-size. Keyboard-navigable. Colour-and-icon badges.

### Browser compatibility

- Modern evergreen Chrome, Firefox, Safari (latest two releases). No IE, no Edge Legacy, no mobile-specific optimisation.

---

## Decisions Already Made

| # | Decision | Why |
|---|----------|-----|
| 1 | Output = directory with `index.html` (inlined JSON) + sibling `results.json` + `.harness-report` sentinel | Safari `file://` blocks `fetch`; inlining avoids CORS; sibling JSON exists for tooling only |
| 2 | Stack = vanilla JS, no build step, no third-party JS | Keeps harness dependency-light; CI artefact portability; no licence / CORS entanglement |
| 3 | Trigger = pytest plugin, end of session | Matches where the data lives |
| 4 | **Packaging: separate distribution `core-reporter`** | Keeps pytest out of `core`'s dependency graph; matches the CLAUDE.md "pure tool" rule |
| 5 | Single run only in v1; versioned JSON for later | Explicit forward-compatibility hook for a future aggregation system |
| 6 | Reporting unit = nested (pytest test → scenario → handle + timeline) | Maps to the in-memory shape from PRDs 002 + 006 |
| 7 | Collapsed by default, failures auto-expanded | Matches the typical scanning path |
| 8 | No raw transport frame rendering in v1 | Raw frames are a distinct rendering problem; deferred |
| 9 | No charts in v1; numbers only, including p50/p95/p99 | Charts are v2; JSON already contains the numbers |
| 10 | Side-by-side expected vs actual (not structural diff) | v1 accepts the legibility tradeoff; structural diff is v2 |
| 11 | Test list grouped by test file | Matches author scanning model |
| 12 | Full stdout/stderr/log + traceback + scenario summary on failure | Capped per §5; redacted per §12 |
| 13 | `schema_version: "1"` stamped; JSON Schema file checked in with `required` arrays | Forward compatibility + silent-drift detection |
| 14 | No third-party JS libraries | Self-contained, no CVE surface |
| 15 | **xdist in v1: per-worker partial files + controller merge** | Matches typical CI topology; avoids "pytest-xdist is unsupported" footgun |
| 16 | **Inlined JSON in `<script type="application/json">`, HTML never fetches** | Safari `file://` works; no runtime network; no sync drift because there is one source of truth |
| 17 | **`data-*` DOM contract required** | Structural tests without text matching; stable HTML-test API |
| 18 | **Single state object + `render()` + `location.hash` mirror** | Deterministic reactive pattern without a framework; shareable URLs |
| 19 | **Default credential-shape redaction** | Prevents common secrets leakage into uploaded CI artefacts; domain PII remains consumer-owned |
| 20 | **Env-var reads only in `core-reporter`, never in `core`** | Preserves the "pure tool" rule from CLAUDE.md |
| 21 | **Per-payload cap: 8 KB (not 32 KB)** | Keeps worst-case memory within a documented bound |
| 22 | **Total-file cap: 10 MB, hard refuse (not warn)** | Silent multi-MB uploads are a CI-budget footgun |
| 23 | **Git lookup on background thread at sessionstart** | Removes 500 ms from the sessionfinish critical path |
| 24 | **Observer called at both `_do_await_all` and `__aexit__`** | Captures scenarios whose test body raised before `await_all` |
| 25 | **Matcher protocol extension: `expected_shape()` default method** | Formal extension to PRD-002 rather than duck-typed attribute |
| 26 | **Open Question #2 closed: default filter = non-passing only** | Matches the scanning model; resolves v1 ambiguity |
| 27 | **No Playwright in v1 test suite** | US-4 filter/search acceptance verified by a manual checklist; US-1/2/3/5 by pytester + HTML structural parse |
| 28 | **Stream redaction: on by default** | A false-positive redaction in a log is inconvenient; a missed real credential leaking into CI is an incident. Opt-out via `--harness-report-no-stream-redact` |
| 29 | **Unsafe `--harness-report` path: abort, do not fall back** | Silently moving the output is more surprising than failing fast. The pytest run exits with a clear error naming the offending path |

---

## Open Questions

None. All v1 open questions closed in Decisions Already Made (rows 26-29).

---

## Out of Scope (explicit)

- Latency charts, histograms, Gantt timelines.
- Run-over-run comparison, trend lines, flake ranking.
- Raw transport frame decoding and display.
- Dashboards, hosted services, authentication.
- Re-run / copy-command buttons.
- Live / streaming updates during a run.
- Nightly scheduling infrastructure.
- Posting `results.json` to an external system (schema is the hand-off point).
- Third-party JS frameworks and a build pipeline.
- Dark mode, print stylesheet, localisation.
- IndexedDB / localStorage persistence.
- Domain-level PII redaction (only credential-shape is automatic).
- Playwright or headless-browser test integration in v1.
- Controller-side merge for workers that crashed without flushing a partial file (the `incomplete_workers` banner signals this; the merge does not attempt reconstruction).

---

## Testing Strategy

### Unit tests (no pytester)

- `tests/test_reporting_observer.py`
  - `test_a_registered_observer_should_receive_the_completed_scenario_result`
  - `test_observer_errors_should_not_propagate_to_the_scenario_scope`
  - `test_an_exception_in_the_scenario_body_should_still_notify_the_observer_with_completed_normally_false`
  - `test_contextvar_should_isolate_observers_across_concurrent_scenarios`
  - `test_a_matcher_exposing_expected_shape_should_populate_handle_matcher_expected`
  - `test_a_matcher_without_expected_shape_should_leave_matcher_expected_null`

### JSON Schema tests

- `tests/reporter/test_schema.py` (in the `core-reporter` package)
  - `test_schema_file_should_load_as_valid_json_schema`
  - `test_appendix_a_example_should_validate_against_the_schema`
  - `test_a_minimal_valid_document_should_validate`
  - `test_a_document_missing_schema_version_should_fail_validation`
  - `test_every_required_field_should_be_listed_in_the_schema_required_array`

### End-to-end tests via pytester

- `tests/reporter/test_plugin_activation.py`
- `tests/reporter/test_json_output_shape.py`
- `tests/reporter/test_directory_wipe_safety.py`
- `tests/reporter/test_reporter_errors_never_break_tests.py`
- `tests/reporter/test_html_structural_contract.py` (uses `beautifulsoup4` with `html.parser` backend; asserts `data-*` attributes present)
- `tests/reporter/test_xdist_merge.py` (spawns inner pytest session with `-n 2`)
- `tests/reporter/test_credential_redaction.py`

### Manual checklist (US-4 filter/search)

- `tests/reporter/MANUAL.md` with a concrete list of browser-based checks an author runs before releasing a reporter version. Covers filter pills, marker dropdown, free-text search, duration filter, URL-hash round-trip, accessibility smoke test, Safari `file://` open.

### Dependencies added to `[project.optional-dependencies.test]`

- `jsonschema>=4.18` (for schema validation)
- `beautifulsoup4>=4.12` (for HTML structural parsing)

---

## Related PRDs / ADRs

- [PRD-001 — Framework foundations](PRD-001-framework-foundations.md) — the Harness and transport layer the reporter observes.
- [PRD-002 — Scenario DSL](PRD-002-scenario-dsl.md) — defines `Handle`, `ScenarioResult`, `Outcome`, the `Matcher` protocol. v2 of this PRD requires a minor extension to the matcher protocol (see §3, handle-level `expected` field, and Decision #25).
- [PRD-006 — Latency observability](PRD-006-latency-observability.md) — produces the `timeline` tuple, `timeline_dropped` counter, and `Outcome.SLOW` that this report renders.
- [ADR-0014 — Handle / Result model](../adr/0014-handle-result-model.md) — the per-expectation object whose fields the schema serialises.

---

## Appendix A — Example `results.json` (elided)

```json
{
  "schema_version": "1",
  "run": {
    "started_at": "2026-04-17T09:12:04.118Z",
    "finished_at": "2026-04-17T09:12:07.942Z",
    "duration_ms": 3824,
    "totals": {"passed": 97, "failed": 2, "errored": 0, "skipped": 0, "slow": 1, "total": 100},
    "transport": "MockTransport",
    "allowlist_path": "config/allowlist.yaml",
    "python_version": "3.12.4",
    "harness_version": "0.5.1",
    "reporter_version": "0.1.0",
    "git_sha": "a1b2c3d4",
    "git_branch": "feat/reporter",
    "environment": "dev",
    "hostname": "laptop-mkl",
    "xdist": null,
    "truncated": false,
    "redactions": {"fields": 0, "stream_matches": 0}
  },
  "tests": [
    {
      "nodeid": "tests/test_orders.py::test_fill_updates_positions",
      "file": "tests/test_orders.py",
      "name": "test_fill_updates_positions",
      "class": null,
      "markers": ["smoke"],
      "outcome": "slow",
      "duration_ms": 612,
      "traceback": "AssertionError: scenario 'fill' failed -- 1 of 2 expectations did not pass\n  ...",
      "stdout": "",
      "stderr": "",
      "log": "",
      "skip_reason": null,
      "worker_id": null,
      "scenarios": [
        {
          "name": "fill",
          "correlation_id": "TEST-corr-91f3b2",
          "outcome": "slow",
          "duration_ms": 501,
          "completed_normally": true,
          "handles": [
            {
              "topic": "state.changed",
              "outcome": "slow",
              "latency_ms": 72.4,
              "budget_ms": 50,
              "matcher_description": "contains_fields({'count': 1000})",
              "expected": {"count": 1000},
              "actual": {"count": 1000, "kind": "CREATE", "amount": 101.5, "actor": "A-42"},
              "attempts": 2,
              "reason": "matched in 72.4ms, budget 50ms (exceeded by 22.4ms)",
              "truncated": false
            },
            {
              "topic": "validation.approved",
              "outcome": "timeout",
              "latency_ms": 500.0,
              "budget_ms": null,
              "matcher_description": "contains_fields({'status': 'ACCEPTED'})",
              "expected": {"status": "ACCEPTED"},
              "actual": null,
              "attempts": 0,
              "reason": "no matching message arrived on topic 'validation.approved' within 500ms",
              "truncated": false
            }
          ],
          "timeline": [
            {"offset_ms": 2.1,  "wall_clock": "2026-04-17T09:12:04.120Z", "topic": "events.created",      "action": "published",  "detail": "evt-7f2"},
            {"offset_ms": 14.8, "wall_clock": "2026-04-17T09:12:04.133Z", "topic": "state.changed",       "action": "mismatched", "detail": "count=500, want 1000"},
            {"offset_ms": 72.4, "wall_clock": "2026-04-17T09:12:04.190Z", "topic": "state.changed",       "action": "matched",    "detail": "count=1000"},
            {"offset_ms": 500.0,"wall_clock": "2026-04-17T09:12:04.618Z", "topic": "validation.approved", "action": "deadline",   "detail": "no message"}
          ],
          "timeline_dropped": 0,
          "summary_text": "scenario 'fill' failed -- 1 of 2 expectations did not pass\n..."
        }
      ]
    }
  ]
}
```

---

## Appendix B — Example `index.html` shape

```
+-------------------------------------------------------------------------+
| Harness Test Report -- 2026-04-17 09:12:04 UTC                          |
| 100 tests -- 97 passed, 2 failed, 1 slow -- 3.8s                        |
| MockTransport  |  env=dev  |  sha=a1b2c3d4  |  harness=0.5.1            |
| p50=12ms  p95=184ms  p99=498ms  (handle latencies)                      |
+--------------- filters --------------------------------------------------+
| [passed:hidden] [failed:O] [slow:O] [skip:O] [err:O]   search:[______]  |
| markers: [smoke v]   slower than: [____]ms                              |
+------------------------+------------------------------------------------+
| v tests/test_events.py |  tests/test_events.py::test_event_changes_st.. |
|   v test_event_changes_|  data-nodeid=".." data-outcome="slow"          |
|   > test_reject_event  |                                                |
| > tests/test_audit.py  |  scenario 'fill' [slow] 501ms                  |
| > tests/test_query.py  |    v state.changed    [slow]  72.4ms / 50ms    |
|                        |        expected              | actual          |
|                        |        {count: 1000}         | {count:1000,    |
|                        |                              |  kind:"CREATE", |
|                        |                              |  amount:101.5,  |
|                        |                              |  actor:"A-42"}  |
|                        |    > validation.approved [timeout] 500ms       |
|                        |                                                |
|                        |  Timeline (4 entries)                          |
|                        |      2.1ms  events.created       published     |
|                        |     14.8ms  state.changed        mismatched    |
|                        |     72.4ms  state.changed        matched       |
|                        |    500.0ms  validation.approved  timed out     |
+------------------------+------------------------------------------------+
```
