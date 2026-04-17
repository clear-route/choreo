# ADRs — Architecture Decision Records

Decisions that shape the Choreo architecture. Each ADR records **what was decided**, **why**, and **what we rejected** — captured once so future maintainers don't relitigate.

## Conventions

- Filename: `NNNN-kebab-case-title.md` (4-digit zero-padded).
- Template: [template.md](template.md) — an **ADR template** (not a PRD template). Contains style rules to follow.
- Status lifecycle: `Proposed` → `Accepted` → (later: `Deprecated` | `Superseded by ADR-XXXX`).
- Writing style: [context.md §15](../context.md) — UK English, no em dashes in code, plain verbs. The template lists banned words.
- Keep each ADR focused on **one** decision. Cross-link to others.
- Once Accepted, content changes require a superseding ADR. The `Last Updated` metadata field records the original write date and does not change.
- Every ADR must include a **Security Considerations** stanza (even if the answer is "N/A — see ADR-XXXX").
- Deferred decisions in Notes must name an owner.

## Current ADRs

| # | Title | Status | Decision |
|---|-------|--------|----------|
| [0001](0001-single-session-scoped-harness.md) | Single session-scoped Harness for connection reuse | Accepted | One Harness per pytest session, shared across all tests |
| [0002](0002-scoped-registry-test-isolation.md) | Scoped registry + correlation IDs for test isolation | **Proposed** (blocker: correlation echo unverified) | `async with scenario_scope` cleanup + correlation-ID routing for parallel |
| [0003](0003-threadsafe-call-soon-bridge.md) | Thread-safety bridge via `loop.call_soon_threadsafe` | Accepted | Single helper; AST CI check; constrained callable whitelist |
| [0004](0004-dispatcher-correlation-mediator.md) | Dispatcher as Mediator for correlation-based inbound dispatch | Accepted | Central router; O(1) lookup; single dispatch point |
| [0005](0005-pytest-asyncio-session-loop.md) | Session-scoped event loop via pytest-asyncio `loop_scope` | Accepted | `loop_scope="session"` in `pyproject.toml`; requires pytest-asyncio ≥ 0.24 |
| [0006](0006-environment-boundary-enforcement.md) | Environment-boundary enforcement at `Harness.connect()` | Accepted | Explicit `environment` parameter + per-environment allowlist YAML; default-deny |
| [0007](0007-harness-failure-recovery.md) | Harness failure recovery policy | Accepted | Quarantine-and-rebuild on detected corruption; no automatic retries; rebuild ceiling |
| [0012](0012-type-state-scenario-builder.md) | Type-state scenario builder | Accepted | Four states: Builder → Expecting → Triggered → Result; publish / await_all only on the right one |
| [0013](0013-matcher-strategy-pattern.md) | Matcher Strategy pattern | Accepted | Protocol + built-ins + `all_of`/`any_of`/`not_` composition; matchers own their description |
| [0014](0014-handle-result-model.md) | Handle-based result model | Accepted | Each `expect*()` returns a Handle; resolved state survives scope teardown; non-pickleable |
| [0015](0015-deadline-based-scenario-timeouts.md) | Deadline-based scenario timeouts | Accepted | `asyncio.timeout_at` + `asyncio.wait(ALL_COMPLETED)`; collect all results then cancel pending |
| [0016](0016-reply-lifecycle.md) | Reply lifecycle — scope-bound, fire-once, pre-publish | **Proposed** | `on()` is a subscription; fire-once; deregister on scope exit; no post-publish registration |
| [0017](0017-reply-fire-and-forget-results.md) | Fire-and-forget reply results with ReplyReport | **Proposed** | Replies are not assertions; observability via `ScenarioResult.replies` (four states); no Handle |
| [0018](0018-reply-correlation-scoping.md) | Reply correlation scoping — reuse of expect-filter | **Proposed** | Same filter as `expect`; scope-correlation stamped on reply; SUT-originated correlation deferred |
| [0019](0019-pluggable-correlation-policy.md) | Pluggable correlation policy with no-op default | **Proposed** | `CorrelationPolicy` protocol over an `Envelope`; `NoCorrelationPolicy` default for public release; `test_namespace()` factory ships the current `TEST-` posture as opt-in |
| [0020](0020-transport-auth.md) | Transport authentication — typed per-transport auth with optional resolver | **Proposed** | `auth: <Concrete>Auth \| AuthResolver \| None` per transport; bounded lifetime, structural redaction, no pickle; allowlist unchanged |

## Dependency graph

```
ADR-0001 (Single Harness)
  ├── depends on ADR-0002 for safety — scoped cleanup bounds shared-state risk
  ├── depends on ADR-0003 — transports run on non-loop threads; need the poster
  ├── depends on ADR-0005 — session-scoped fixture requires session-scoped loop
  ├── depends on ADR-0006 — allowlist guard is the accidental-prod-connection check
  ├── depends on ADR-0007 — recovery policy when the Harness corrupts mid-suite
  └── contains ADR-0004 — Dispatcher lives inside the Harness

ADR-0002 (Scoped registry + correlation IDs)
  └── depends on ADR-0004 — scopes register correlation IDs with the dispatcher

ADR-0003 (Thread bridge)
  └── used by ADR-0004 — dispatcher dispatches via the poster

ADR-0004 (Dispatcher)
  ├── depends on ADR-0003 — poster
  └── depends on ADR-0002 — scope registration

ADR-0005 (Session loop)
  └── prerequisite for ADR-0001 and ADR-0003

ADR-0006 (Allowlist enforcement)
  └── prerequisite for ADR-0001's Accepted status

ADR-0007 (Recovery)
  └── rebuild-on-corruption policy

ADR-0016 (Reply lifecycle)
  ├── depends on ADR-0002 — scope-bound cleanup path
  ├── depends on ADR-0012 — extends type-state with `on()` on pre-publish states
  └── paired with ADR-0017 and ADR-0018 — splits result + correlation into separate decisions

ADR-0017 (Reply fire-and-forget results)
  ├── depends on ADR-0014 — deliberately not extended; Handle stays for `expect` only
  └── paired with ADR-0016 and ADR-0018

ADR-0018 (Reply correlation scoping)
  ├── depends on ADR-0004 — reuses dispatcher's correlation → scope lookup
  ├── depends on ADR-0002 — scope boundary is the isolation primitive
  ├── depends on ADR-0019 — injection and extraction flow through the Correlator
  └── defers to future ADR — SUT-originated correlation chains out of v1 scope

ADR-0019 (Pluggable correlation policy)
  ├── supersedes the TEST- prefix bullet in ADR-0006 §Goals
  ├── rewires ADR-0002's parallel-isolation story as consumer opt-in
  ├── rewires ADR-0004's extractor registration through the Correlator
  └── prerequisite for ADR-0018's move to Accepted

ADR-0020 (Transport authentication)
  ├── additive to ADR-0006 — auth sits alongside the endpoint allowlist
  ├── inherits ADR-0001's __reduce__ / repr-redaction posture
  └── rehomes the deleted ADR-0010 (Secret Management) at the transport layer
```

Reading order for a new contributor: 0005 → 0001 → 0003 → 0002 → 0004 → 0006 → 0019 → 0007 → 0012 → 0013 → 0014 → 0015 → 0016 → 0017 → 0018.

## Open ADRs to write

| Intended # | Working title | Unblocks | Priority |
|-----------|---------------|----------|----------|
| 0011 | Adversarial / buggy SUT handling (correlation cross-field sanity) | ADR-0004 | Medium |

Remaining blocker for ADR-0002 (**Proposed**): **correlation-echo verification** against real downstream services. Not an ADR — a real-world spike that confirms protobuf `correlation_id` and equivalent headers round-trip as expected. Tracked in [ADR-0002 Notes](0002-scoped-registry-test-isolation.md).

## When to write an ADR

Write one when:

- A decision is **hard to reverse** (connection lifecycle, isolation model, cross-cutting dispatch).
- A decision **closes off alternatives** that a future reader might wonder why we rejected.
- A decision **introduces a dependency** that downstream work relies on.
- A decision has **security implications** in a messaging-platform context.

Don't write one for:

- Tactical choices within a component (obvious from code).
- Decisions already captured elsewhere (writing-style conventions in [context.md §15](../context.md); no ADR needed).
- Bug fixes or refactors that don't change the architecture.

## Relationship to PRDs

| ADR tells you | PRD tells you |
|---------------|---------------|
| **How** the system is built | **What** the system must do and **why** |
| The trade-off between options we considered | The user need and acceptance criteria |
| What we rejected and why | The requirements, risks, and milestones |
| Decision date and decider | Status, owner, and stakeholders |

A PRD may spawn multiple ADRs ([PRD-001](../prd/PRD-001-framework-foundations.md) spawned five, with six more pending). An ADR may serve multiple PRDs (e.g. ADR-0002 applies to PRD-001 and PRD-002).

## Background documents

- [context.md](../context.md) — architecture and writing style
- [framework-design.md](../framework-design.md) — Internal framework design; options + trade-offs that the ADRs crystallise
- [docs/prd/](../prd/) — Product Requirements Documents (the "why" and "what" that these ADRs answer "how")
