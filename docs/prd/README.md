# PRDs — Product Requirements Documents

Product requirement documents for Choreo. Each PRD captures **what** a component needs to do and **why**, with enough detail to reach agreement before implementation starts. Architectural *how* lives in ADRs ([../adr/](../adr/)) and [framework-design.md](../framework-design.md).

## Conventions

- Filename: `PRD-NNN-kebab-case-title.md`
- Template: [template.md](template.md)
- Status lifecycle: `Draft` → `In Review` → `Approved` → `Implemented`
- Writing style: [context.md §15](../context.md) — UK English, no em dashes in code, plain verbs, no "leverage / seamless / robust".

## Current PRDs

| # | Title | Status | Covers |
|---|-------|--------|--------|
| [PRD-001](PRD-001-framework-foundations.md) | Framework foundations — Harness, Dispatcher, transports | Draft | Suite-scoped plumbing: Harness Facade, session fixture, transports, Dispatcher, correlation routing, thread-safety |
| [PRD-002](PRD-002-scenario-dsl.md) | Scenario DSL — type-state builder, handles, timeouts | In Review | What test authors touch: fluent builder, expect-before-publish enforcement, Handles, Matchers, deadlines |
| [PRD-006](PRD-006-latency-observability.md) | Latency observability | Draft | Per-scenario latency budgets, timeline recording, SLOW outcome classification |
| [PRD-007](PRD-007-test-report-output.md) | Test report output | Draft | HTML + JSON reporter, Jaeger-style waterfall, diff rendering, redaction pipeline |
| [PRD-008](PRD-008-scenario-replies.md) | Scenario replies — `on().publish()` | Draft | Scope-bound reply primitive for staging upstream services inside a test |

## Dependency graph

```
PRD-001 (foundations)
  └─▶ PRD-002 (DSL)
        ├─▶ PRD-006 (latency observability)
        ├─▶ PRD-007 (test report output)
        └─▶ PRD-008 (scenario replies)
```

Implementation order roughly follows the arrows.

## Background documents

These inform the PRDs but aren't PRDs themselves:

- [context.md](../context.md) — architecture, harness DSL principles, CI pipeline sketch, writing style
- [framework-design.md](../framework-design.md) — Internal framework design: type-state builder, timeouts, correlation-ID parallelism
- [framework-design.excalidraw](../../framework-design.excalidraw) — Visual architecture diagram

## When to write a PRD

Write one when the answer to any of these is *no*:

- Is the scope small enough that the change can be reviewed in a single PR?
- Are the trade-offs and alternatives obvious enough that everyone will reach the same conclusion?
- Is there no cross-team dependency?

A new service, a new mode, a new storage backend, or a change in a public API all warrant PRDs. A bug fix or refactor doesn't.
