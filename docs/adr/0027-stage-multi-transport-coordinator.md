# 0027. Stage Coordinator for Multi-Transport Scenarios

**Status:** Proposed
**Date:** 2026-05-04
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [PRD-011 — Multi-Transport Scenarios](../prd/PRD-011-multi-transport-stage.md)

---

## Context

[PRD-011](../prd/PRD-011-multi-transport-stage.md) introduces `Stage`, a coordinator that wraps a named registry of `Harness` instances so a single scenario can publish, expect, and reply across multiple transports. The motivating case is a bridge AUT: a request arrives on NATS, the AUT translates and republishes onto a Kafka topic for the durable processing pipeline, the pipeline emits a result event on Kafka, the AUT translates the result back onto NATS for the original requester. Today this round trip is not expressible in one scenario.

This ADR commits to the implementation choices that the PRD intentionally left open: how the Stage holds harnesses, how scope ownership splits between the Stage and its constituent harnesses, where the correlation translation lives, how lifecycle errors are surfaced, and where the trust boundary sits for consumer-supplied bridge code.

### Background

- [PRD-011](../prd/PRD-011-multi-transport-stage.md) — surface-level requirements and the chosen `Stage`-coordinator API shape.
- [ADR-0001 — single-session-scoped harness](0001-single-session-scoped-harness.md) — the harness lifecycle this ADR coordinates across.
- [ADR-0002 — scoped registry, test isolation](0002-scoped-registry-test-isolation.md) — the `async with` cleanup boundary inherited per harness.
- [ADR-0004 — dispatcher correlation mediator](0004-dispatcher-correlation-mediator.md) — the per-topic dispatch path the Stage subscribes through.
- [ADR-0012 — type-state scenario builder](0012-type-state-scenario-builder.md) — the four-state DSL the Stage reuses.
- [ADR-0014 — handle result model](0014-handle-result-model.md) — `Handle` gains a `transport: str | None` attribute under this ADR.
- [ADR-0016 — reply lifecycle](0016-reply-lifecycle.md) — fire-once, scope-bound replies extended to cross-transport here.
- [ADR-0019 — pluggable correlation policy](0019-pluggable-correlation-policy.md) — per-harness correlation policies the Stage composes via the bridge.
- Current single-transport scope construction at [packages/core/src/choreo/harness.py:191](../../packages/core/src/choreo/harness.py#L191) and the per-context expectations / replies shape at [packages/core/src/choreo/scenario.py:568-576](../../packages/core/src/choreo/scenario.py#L568-L576).

### Problem Statement

How does choreo expose a single scenario timeline that spans multiple `Harness` instances, with one global deadline, scope-bound cleanup on every transport, and a translation surface for correlation identifiers that differ in shape between transports — without changing the existing `Harness` API and without coupling the harness to multi-transport awareness?

### Goals

- **One scope, multiple transports.** A single `stage.scenario(name)` block enrols expectations, publishes, and replies on any combination of registered harnesses.
- **One global deadline.** `await_all(timeout_ms)` covers every handle on every transport; no per-transport budgets in v1.
- **Per-transport correlation translation, owned by the Stage.** The Stage generates one logical scope id and uses a consumer-supplied `CorrelationBridge` to derive the per-transport wire identifiers.
- **Existing `Harness` API unchanged.** No new constructor parameters, no behavioural change for single-transport tests.
- **Reuse the existing scenario machinery.** The Stage composes existing per-harness scenario contexts; it does not reimplement matching, dispatch, or reply firing.
- **Fail-fast lifecycle with rollback.** Connect aborts on the first failure and disconnects every harness already up; disconnect is idempotent.

### Non-Goals

- **Generalising `Harness` to take multiple transports.** Considered and rejected; see Option 2 below.
- **Cross-transport message ordering guarantees.** The Stage does not serialise publishes across transports; ordering across transport boundaries depends on the SUT.
- **Cross-transport transactions.** No "publish on A and B atomically" primitive.
- **Learn-by-observation correlation bridges.** v1 covers deterministic bridges only — bridges that can derive each transport's wire id from the logical id without observing the AUT first. The bridge protocol leaves room for a future learn-by-observation extension; see Notes.
- **Per-transport latency budgets at the scope level.** `Handle.within_ms()` already exists per-handle; no transport-wide budget configuration in v1.
- **Multi-hop reply chains across transports.** Same deferral as [ADR-0016](0016-reply-lifecycle.md). The cross-transport version of `s.on(...).publish(...)` is in scope; chaining beyond one hop is not.

---

## Decision Drivers

- **No regression in the single-transport path.** Adoption is opt-in. Every existing test must run unchanged against the post-Stage build.
- **Trust boundary for consumer bridge code.** The `CorrelationBridge` is consumer code executed inside the framework's async path on every publish and inbound message. The pattern from [ADR-0019](0019-pluggable-correlation-policy.md) — wrap calls, surface failures as named scenario errors — applies again.
- **Reuse over reinvention.** Per-transport publishing, decoding, matching, replying, and teardown already work in `_ScenarioContext`. The Stage should drive these, not re-derive them.
- **Failure mode legibility.** When a multi-transport scenario fails, the diagnostic must name *which transport's handle* timed out or mismatched. Aggregated "scenario failed" with no transport breadcrumb makes bridge tests harder to debug than the bespoke two-harness fixtures they replace.
- **Lifecycle determinism.** Partial-up state across two transports is a worse failure mode than fully-down. A test that runs against half a fabric is a flake source; the Stage must not let it happen.

---

## Considered Options

### Option 1: `Stage` coordinator with per-transport child scopes; `CorrelationBridge` translates logical → wire (chosen)

**Description:** A new `Stage` class holds an ordered named registry of `Harness` instances and a consumer-supplied `CorrelationBridge`. `stage.scenario(name)` returns a `_StageScenarioScope` which, on entry, calls `bridge.fresh()` once to mint a logical scope id and then opens one per-transport `_ScenarioContext` per touched harness, each seeded with `correlation_id = bridge.to_wire(logical, name)`. The Stage's DSL methods (`expect`, `publish`, `on`) take an `on=<transport>` selector and route the call to the relevant per-transport child context. `await_all()` aggregates futures across all child contexts under one deadline. `__aexit__` tears down each child context in reverse touch-order.

**Pros:**
- Reuses every existing scenario primitive — matchers, handle resolution, reply firing, callback teardown — without reimplementation.
- Per-harness `CorrelationPolicy` keeps doing exactly what it does today; the Stage only drives the *value* of the correlation id, never how it is stamped or read.
- The `Harness` class is untouched. Single-transport tests run on the same code path they always have.
- `Handle.transport` falls out naturally: each child context is tagged with its transport name and propagates that tag onto its handles.
- Per-transport child contexts give a clean place to pin per-transport diagnostics: `result.by_transport["kafka"]` is a list of handles owned by the Kafka child.
- Bridge translation runs once per scope per transport (at scope entry), not per message; the inbound match uses straight wire-level comparison, the same code path as a single-transport scenario.
- The bridge protocol is small: `fresh()` and `to_wire(logical, transport)` cover the deterministic case; `from_wire(wire, transport)` is optional and used for diagnostics only in v1.

**Cons:**
- Two scope abstractions exist (`_ScenarioScope` and `_StageScenarioScope`). Care needed to keep their DSL surfaces in lockstep — adding a primitive to one means adding it to the other.
- A misconfigured bridge that returns the same wire id for two transports silently breaks cross-transport routing within a scope (the same id will match unrelated traffic). Mitigated by a two-pass validation: a startup smoke test at `Stage.__init__` against a synthetic logical id, and per-scope re-validation at `__aenter__` against the actual `bridge.fresh()` value. The per-scope pass catches bridges whose forward function is input-dependent and only collides on real input. See §Security Considerations.
- The deterministic-bridge constraint pushes the AUT-generates-id case (Case 2 in §Implementation) out of v1. Documented in Non-Goals; protocol leaves room for the extension.

### Option 2: Generalise `Harness` to take a `dict[str, Transport]`

**Description:** Drop `Stage`. Extend `Harness.__init__` to accept either a single `Transport` or a mapping. Add `on=<transport>` to the existing `Scenario` DSL.

**Pros:**
- One fewer top-level type for users to learn.

**Cons:**
- Forces every existing single-transport test through the multi-transport code path; backward compatibility burden lands inside `Harness`.
- Codec is per-harness today (one). Going multi-transport at the harness level forces codec to become a parallel mapping; every `Harness` constructor signature changes.
- Correlation policy is per-harness today (one). Same problem — the harness becomes a meta-harness.
- The harness's job is single-transport coordination. Folding multi-transport coordination into the same class violates [ADR-0001](0001-single-session-scoped-harness.md)'s separation and turns the harness into a god-object.
- The bridge has to live somewhere; the natural home becomes a mandatory `Harness` constructor argument, regressing the single-transport ergonomics that ADR-0019 preserved.

### Option 3: External coordination — keep `Harness` single-transport, no library support

**Description:** Document a pattern for users to instantiate two harnesses, run two scenarios, and rendezvous via `asyncio.gather` in their own fixture.

**Pros:**
- Zero library change.

**Cons:**
- Does not solve the problem named in PRD-011: there is no single timeline, no global deadline, no cross-transport reply, no shared correlation, no aggregated `ScenarioResult`. The user is left to re-invent every coordination primitive in fixture code, badly.
- Bridge correlation translation has nowhere to live; every consumer hand-rolls it.
- Defeats the purpose of choreo for the bridge-test class of test, which is the highest-value test in protocol-translation services.

### Option 4: Single `Scenario` class made stage-aware via optional bridge

**Description:** Rather than a separate `_StageScenarioScope`, extend the existing `Scenario` to accept an optional `_stage` reference and an optional `on=` selector on every DSL call. In single-harness mode, `_stage` is `None` and `on=` is forbidden; in multi-harness mode, `_stage` resolves the harness per call.

**Pros:**
- One scope abstraction; no risk of DSL drift.
- The DSL surface looks identical to users in both modes.

**Cons:**
- The `Scenario` class becomes a sum type with mode-conditional invariants in every method. Type-state guarantees from [ADR-0012](0012-type-state-scenario-builder.md) get harder to enforce — every method needs a runtime check that `on=` is required-or-forbidden.
- A bug in stage-mode handling becomes a bug in single-mode handling, because they share a class. Blast radius too wide for the value.
- The diagnostic surface is harder to keep clean — error messages have to say which mode they apply to.

---

## Decision

**Chosen Option:** Option 1 — `Stage` coordinator with per-transport child scopes and a consumer-supplied `CorrelationBridge`.

### Rationale

- Option 1 is the only option that satisfies all the goals: it keeps `Harness` unchanged (Option 2 fails this), provides real coordination (Option 3 fails this), and isolates multi-transport concerns from single-transport invariants (Option 4 fails this).
- The "child scope per touched harness" design unlocks free reuse: matching, handle resolution, reply firing, and callback teardown all work as-is. The Stage's net new code is the bridge plumbing, the lifecycle coordinator, and the DSL routing — narrow and testable.
- The bridge surface is intentionally small. A consumer with a simple deterministic translation rule writes a `MappedBridge`; a consumer with a complex rule writes a custom `CorrelationBridge` implementation. The framework does not try to be clever about translation patterns it cannot see.
- Failure-mode legibility is a forcing function. The per-transport child gives the diagnostic surface a place to attach: `Handle.transport` is the breadcrumb, `result.by_transport[name]` is the per-transport view.

---

## Consequences

### Positive

- A test author writes the bridge round-trip as one scope with one timeline. The PRD-011 success metric (≤15 lines for the canonical NATS ↔ Kafka round trip) is achievable.
- Per-transport handles carry their transport name; failure diagnostics name which side timed out without manual annotation.
- The existing scenario suite passes unchanged; no migration burden for single-transport users.
- The bridge interface generalises to any transport pair: NATS↔Kafka, Kafka↔Rabbit, Redis↔Kafka, custom↔Kafka, mock↔mock for parallel-isolation tests of the framework itself.
- `Stage.connect()` rollback behaviour means a CI run that hits a transient transport failure surfaces a single named error rather than an opaque "broker not ready" deep in a test body.

### Negative

- Two DSL surfaces exist (`Scenario` and `StageScenario`). Adding a new DSL primitive — say, a v2 reply modifier — requires implementing it on both. Mitigated by a shared abstract base for the DSL methods that delegate to per-context registration; primitive additions land in the base, not the subclass.
- Stage-aware tests need a bridge. For homogeneous-correlation cases the shipped `IdentityBridge` covers it, but a non-trivial bridge is consumer code that the framework cannot validate at type-check time. Mitigated by the startup validation in §Security Considerations and by `MappedBridge` for the common deterministic case.
- The deterministic-bridge constraint excludes the AUT-generates-fresh-id case from v1. Documented in Non-Goals and in the `CorrelationBridge` docstring. A future ADR will introduce a learn-by-observation bridge variant.
- Stage's `connect()` rollback is best-effort: if the rollback's `disconnect()` calls also fail, the user gets a multi-error. Mitigated by structured logging on every rollback step so the audit trail names what was attempted.

### Neutral

- New top-level types: `Stage`, `CorrelationBridge`, `IdentityBridge`, `MappedBridge`, `StageScenario`, plus the error types `StageConnectError`, `StageDisconnectError`, `MissingTransportError`, `UnknownTransportError`, `BridgeTranslationError`.
- New module `packages/core/src/choreo/stage.py`. Re-exports added to `choreo/__init__.py`. No relocation of existing types.
- `Handle` gains an optional `transport: str | None` attribute. `None` for handles created by a single-harness scenario; the transport name for handles created by a Stage scenario. No behavioural change to single-harness handles.
- `ScenarioResult` gains a `by_transport: Mapping[str, tuple[Handle, ...]]` view. For single-harness results, this view contains one entry keyed by an empty string; for Stage results, one entry per touched transport. The aggregate `passed` semantics are unchanged.

### Security Considerations

**Bridge code is a new trust boundary.** A `CorrelationBridge` is consumer-supplied code executed inside the Stage's async path on every scope entry (`fresh()` once; `to_wire()` once per registered transport, eagerly minted) and on the diagnostic-only path (`from_wire()` when an inbound message does not match any active scope). The library defends itself by:

- **Wrapping every bridge call.** Uncaught exceptions from `fresh()`, `to_wire()`, or `from_wire()` raise a named `BridgeTranslationError` carrying the bridge class name, the method, the transport, **and the original exception on a typed `.original` attribute** (mirroring ADR-0019's `CorrelationPolicyError` shape — consumers do not have to walk `__cause__`). Errors during scope entry abort the scope; errors during diagnostic `from_wire()` are logged at WARNING (structured event `stage_from_wire_failed`) and the message is treated as unmatched. The async event loop is never poisoned.
- **Validating bridge return shape.** Every `to_wire` return value is checked to be a non-empty `str` of length ≤ `_MAX_WIRE_ID_LEN` (1024). A bridge that returns `None`, an integer, an empty string, or a pathologically long string fails fast with `BridgeTranslationError`. This closes the silent-routing-break that would result from a `str` vs `int` mismatch on the inbound comparison path.
- **Asserting wire-id distinctness in two passes.** `Stage.__init__` runs a startup smoke-test against a synthetic logical id (`"STAGE-VALIDATION-1234567890abcdef"`); on collision it raises `BridgeAmbiguityError` with the colliding transport names sorted on the typed `.transports` attribute. **Critically, this is a smoke test, not a correctness proof** — a bridge that returns distinct values for the synthetic but collides on real input passes startup validation. The second pass catches the in-flight collision: every `StageScenarioScope.__aenter__` re-validates distinctness using the actual logical id during eager child minting; collisions there raise `BridgeAmbiguityError` and the scope never enters. **Both passes shipped 2026-05-04 (Groups A and E respectively); both call sites delegate to a shared `_check_distinctness` helper for one source of truth.**
- **Detecting transport-set mismatch eagerly.** Bridges advertising a `configured_transports` attribute (e.g. `MappedBridge`) are checked against the registered harness set at `Stage.__init__`; mismatch raises `BridgeTransportMismatchError` (a typed `ValueError`) rather than surfacing as a `KeyError`-wrapped `BridgeTranslationError` at first use. The advertised value is coerced to `frozenset` before comparison, so bridges returning a `list`, `tuple`, `set`, or any iterable of names are accepted uniformly.
- **Treating wire ids as opaque strings on inbound paths.** The per-transport child context compares the inbound wire id (extracted by the harness's existing `CorrelationPolicy.read()`) to its own wire id by string equality. The bridge is not invoked on the hot inbound path; bridge-call failure cannot disrupt routing under load.
- **Recording the bridge class name in the structured startup log.** `Stage.__init__` emits an `INFO`-level structured log event `stage_initialised` carrying the bridge class name and the registered transport names. Audit can identify which bridge was in effect for a given run. Same pattern as ADR-0019 for `CorrelationPolicy`.

**Bridge execution-time and resource bounds.** `to_wire` is synchronous and runs inline on the test event loop. A slow or blocking bridge stalls every concurrent scope sharing that loop — the parallel-isolation guarantee regresses silently. The Stage does not enforce a wall-clock budget on bridge calls (no async cancel point); the bridge protocol docstring states the contract (synchronous, fast, bounded). Consumers writing custom bridges that touch the network or the disk are violating the contract; the library does not detect this. Wire-id length is bounded (`_MAX_WIRE_ID_LEN`); per-call CPU time is not.

**Bridge state-leakage across scopes.** The bridge instance is shared by every scope opened against the Stage. Nothing in the protocol forbids stateful bridges, but a bridge that caches `(logical, transport) → wire` derivations on its instance dict accumulates every active scope's logical id in process memory. If that bridge is ever serialised, logged with `repr`, or inspected by another scope's bridge call, scope-1's logical id is visible to scope-2. The protocol docstring names this as "be stateless across scopes". The library defends against ambient leakage by never including the bridge instance in error messages or logs (only its class name) — but a bridge that logs its own internals defeats this.

**Cross-scope disclosure across the bridge.** Two scopes in the same process derive their per-transport wire ids from independent `bridge.fresh()` calls. If `fresh()` is collision-prone, two scopes can land on the same logical id and therefore the same per-transport wire ids; inbound traffic destined for one scope's transport then matches the other scope's expectations. The library does not attempt to detect collisions at runtime — same residual risk as `CorrelationPolicy.new_id()` under [ADR-0019](0019-pluggable-correlation-policy.md). The shipped `IdentityBridge` and `MappedBridge` default to `secrets.token_hex(16)` for `fresh()`. Custom bridges that derive `fresh()` from external sequence sources must guarantee per-process collision resistance themselves; the docstring names this.

**Cross-transport disclosure on shared infrastructure.** A bridge that maps a logical id to wire ids on two transports does not change the per-transport guarantees from ADR-0019. Each transport's `CorrelationPolicy` continues to enforce its own field-or-header conventions; the bridge only chooses *what value* gets stamped. If transport A and transport B share infrastructure with another tenant, the bridge does not protect against that other tenant — the per-transport policy must (typically via a unique prefix as documented in ADR-0019). This ADR does not weaken or strengthen that guarantee.

**Mid-scenario broker drop.** A broker connection that succeeds at `Stage.connect()` may drop during a scenario. The library handles this in three places:

1. **Inbound:** the dropped transport's harness stops delivering inbound messages. Handles registered against that transport simply do not resolve and are reported as `TIMEOUT` at the global `await_all` deadline. `Handle.transport` names which side dropped, so the diagnostic is unambiguous.
2. **Outbound:** any `s.publish(..., on=dropped_transport)` after the drop raises `RuntimeError("Harness is not connected")` from the underlying harness. The exception propagates out of the `async with` block; `__aexit__` runs and tears down every other transport's child cleanly.
3. **Teardown:** `__aexit__` per-child unsubscribe loop is wrapped in try/except (event `stage_scope_unsubscribe_failed`). A failing unsubscribe on the dropped transport does NOT leak the other transports' callbacks. Same isolation pattern as the single-transport scope ([packages/core/src/choreo/scenario.py:1302-1310](../../packages/core/src/choreo/scenario.py#L1302-L1310)).

The Stage does not attempt automatic reconnect or retry; that is a transport-layer concern. A broker drop is treated as a fatal scenario condition.

**Connect rollback leaves stale subscriptions on real brokers if disconnect itself fails.** `Stage._rollback` swallows disconnect failures with WARNING-level log lines (`stage_rollback_failing_transport_disconnect_failed`, `stage_rollback_sibling_disconnect_failed`). A failed disconnect typically leaves the underlying client in an undefined state — the existing transport implementations (`packages/core/src/choreo/transports/nats.py:243-260`, `rabbit.py:123-143`, `redis.py:149-169`, `kafka.py:169-189`) all swallow internal errors, but a `nats.drain()` followed by a failing `close()` can leave the NATS subscription alive on the broker. The test process believes the Stage is down, but a stale subscription may still receive messages and deliver them to a dead callback (which is itself silenced by harness-level guards). The risk is not data loss; it is **shared-infrastructure cross-test leak**: the next test's fixtures see leaked broker traffic delivered into the dead scope's correlation, which the dispatcher then routes by correlation-id match. Mitigation: prefer dedicated brokers per test process; on shared brokers, use a per-process correlation prefix as ADR-0019 §Security Considerations recommends.

**Error-message and log disclosure surface.**

- `BridgeAmbiguityError`: the colliding wire id is **truncated** in the message string via `_redact()` (head 8 chars, tail 4 chars, length annotation); the full value is on `.transports` only as transport names. Consumers needing the full wire id must implement a bridge whose `__cause__` carries it explicitly.
- `BridgeTranslationError`: the wrapped exception's `str()` lands in the message, which is rendered into log lines. A bridge that raises `KeyError("customer_secret_xyz")` propagates that key verbatim. Consumers writing custom bridges with sensitive data in exceptions must redact at the bridge boundary.
- `StageConnectError`: names the failing transport and the bridge class.
- `Handle.transport` is in `__repr__`. Consumers must not name transports with sensitive identifiers (cluster URLs, tenant codes carrying customer identity).
- **Rollback log lines may surface broker credentials** indirectly. `aiokafka.errors.KafkaConnectionError` and `aio_pika.exceptions.AMQPConnectionError` typically render the broker URL in `__repr__`; if the consumer constructed a Rabbit URL as `amqp://user:password@host`, that credential lands in the rollback's WARNING traceback. The Stage does not attempt to redact transport-layer exception messages. Recommend the consumer's log pipeline carries an `amqp://[^@]*@` redaction filter.

**Handle.transport is observable in repr.** Unlike `Handle.message` (which is redacted from `__repr__` per [ADR-0014](0014-handle-result-model.md)), `Handle.transport` is included in the repr — it is the transport's *name string*, not its connection state, and is needed in failure diagnostics. The `Stage.__init__` docstring states the no-sensitive-names contract. To prevent consumer-side post-hoc mutation (e.g. `handle.transport = "customer-secret-id"`) flowing into the dispatcher and the repr, `Handle.transport` is exposed as a read-only `@property` backed by a private `_transport` field; the framework sets the value once via the dataclass constructor and there is no public setter.

**`UnknownTransportError` enumerates the registered transport set.** The diagnostic message includes `sorted(self._stage._harnesses)` so the consumer can spot the typo without reading source. This amplifies the disclosure of every transport name when any one path errors. The contract that transport names are non-sensitive (above) makes this acceptable; consumers breaking that naming contract accept the consequence.

**Handles never carry another scope's message.** Cross-transport routing is enforced by per-child `correlation_id` comparison. Even under a buggy bridge that shares wire ids across scopes (the failure mode `BridgeAmbiguityError` defends against), the per-transport `CorrelationPolicy.read()` chain remains intact; a same-process scope-2 message never resolves a scope-1 handle without correlation-id match.

**Report-boundary redaction is stricter than in-process redaction (PRD-012).** ADR-0027's `_redact()` (head=8, tail=4, length annotation) is designed for in-process error messages with bounded process lifetime. The on-disk `results.json` boundary uses **hash-based redaction** via `choreo.redaction.redact_wire_id` (SHA-256 truncated to 16 hex chars, prefixed `sha256:`). The two are deliberately decoupled: error messages need to be human-debuggable in the moment; archived reports need to resist greppable correlation across months of retention. See [PRD-012 §1.5.1](../prd/PRD-012-test-report-stage-support.md#15-runtransport-becomes-optional-alongside-new-runtransports). Stage handles' `correlation_id` is hash-redacted at the report boundary; single-`Harness` handles' `correlation_id` is unchanged for backward compatibility with v1.0 consumers.

---

## Implementation

### New module: `packages/core/src/choreo/stage.py`

```python
from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from choreo.harness import Harness


log = logging.getLogger("choreo.stage")


@runtime_checkable
class CorrelationBridge(Protocol):
    """Maps a logical scope id to and from per-transport wire ids.

    A bridge is consumer code. The Stage calls `fresh()` once per scope and
    `to_wire(logical, transport)` once per registered transport per scope at
    scope entry (eager minting; see ADR-0027). The bridge is NOT invoked on
    the inbound message hot path; wire-level comparison is used there.
    `from_wire()` is invoked only for diagnostics when an inbound message
    does not match any active scope; an implementation may return None
    unconditionally if no inverse mapping is available.

    Implementations must:
      * be deterministic on `to_wire` for the duration of a scope (same
        logical id + same transport name => same wire id every call)
      * make `to_wire` return distinct, non-empty `str` values for distinct
        registered transports given the same logical id (the Stage runs a
        startup smoke test and re-validates per scope; see ADR-0027 Security
        Considerations)
      * keep `to_wire` synchronous AND fast (it runs inline on the test's
        event loop; a slow implementation stalls every concurrent scope)
      * keep wire id length within `_MAX_WIRE_ID_LEN` (1024 chars; the Stage
        rejects longer values)
      * be stateless across scopes: the bridge instance is shared between
        every scope opened against the Stage. Caching `(logical, transport)`
        derivations on the bridge instance leaks logical ids cross-scope and
        is unsafe on shared infrastructure (see ADR-0027 Security
        Considerations).
      * make `fresh()` collision-resistant per process

    The shipped `IdentityBridge` and `MappedBridge` honour all the above.
    """

    async def fresh(self) -> Any: ...
    def to_wire(self, logical: Any, transport: str) -> str: ...

    def from_wire(self, wire: str, transport: str) -> Any | None:
        """Recover a logical id from a wire id (diagnostics only).

        Default implementation returns None, signalling "no inverse mapping
        available". Override to enable richer diagnostics for inbound
        messages that did not match any active scope.
        """
        return None


class IdentityBridge:
    """Bridge for the homogeneous case: every transport sees the same wire id.

    NOTE: this bridge is rejected by `Stage.__init__` whenever more than one
    transport is registered, because `to_wire` returns the same value for
    every transport (which trips `BridgeAmbiguityError`). It is therefore
    only useful for framework-internal tests of single-transport Stages and
    parallel-isolation tests where multiple Stages each carry one transport.
    Production code wanting per-transport translation must use `MappedBridge`
    or a custom implementation.
    """

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        return str(logical)

    def from_wire(self, wire: str, transport: str) -> str:
        return wire


@dataclass(frozen=True)
class _MapEntry:
    forward: Callable[[Any], str]
    inverse: Callable[[str], Any] | None = None


class MappedBridge:
    """Bridge with explicit per-transport forward functions.

    forwards: mapping of transport name -> function that turns a logical id
        into the wire id for that transport. Functions must be deterministic,
        synchronous, and return a non-empty `str`. They must not retain
        state across calls; the bridge instance itself does not cache.
    inverses: optional mapping of transport name -> function that turns a
        wire id back into the logical id, used for diagnostics only. Missing
        inverses are silently treated as "no diagnostic available".

    Example:
        bridge = MappedBridge(
            forwards={
                "nats": lambda logical: str(logical),
                "kafka": lambda logical: f"evt-orders-{logical}",
            },
        )

    Stage validation: if `Stage(harnesses={"nats": ..., "kafka": ...},
    bridge=MappedBridge(forwards={"nats": ...}))` (kafka missing), the Stage
    surfaces a `BridgeTransportMismatchError` at `__init__` rather than
    surfacing the underlying KeyError as a generic translation failure.
    """

    def __init__(
        self,
        forwards: Mapping[str, Callable[[Any], str]],
        inverses: Mapping[str, Callable[[str], Any]] | None = None,
    ) -> None:
        inv = inverses or {}
        self._entries: dict[str, _MapEntry] = {
            name: _MapEntry(forward=forwards[name], inverse=inv.get(name))
            for name in forwards
        }

    @property
    def configured_transports(self) -> frozenset[str]:
        """Public view used by Stage to detect transport-set mismatches
        before any to_wire() call runs."""
        return frozenset(self._entries)

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        entry = self._entries[transport]   # KeyError caught and re-raised
                                           # as BridgeTransportMismatchError
                                           # by Stage.__init__.
        return str(entry.forward(logical))

    def from_wire(self, wire: str, transport: str) -> Any | None:
        entry = self._entries.get(transport)
        if entry is None or entry.inverse is None:
            return None
        return entry.inverse(wire)


class _StageState(Enum):
    NEW = "new"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class Stage:
    """Coordinator for multi-transport scenarios.

    Holds an ordered named registry of Harness instances and a bridge that
    translates a logical scope id into per-transport wire ids. Existing
    Harness behaviour is unchanged; the Stage drives the harnesses through
    their public surface (subscribe, publish, scenario) and adds nothing
    that single-transport callers can see.

    State machine:
        NEW -> CONNECTED via connect(); raises StageConnectError on failure
            (state stays NEW; any partially-up transports are rolled back).
        CONNECTED -> DISCONNECTED via disconnect(); idempotent thereafter.
        Re-use is not supported: calling connect() on a DISCONNECTED stage
        raises StageStateError. Construct a new Stage instead.

    See ADR-0027 for the full design discussion.
    """

    def __init__(
        self,
        harnesses: Mapping[str, Harness],
        bridge: CorrelationBridge,
    ) -> None:
        if not harnesses:
            raise ValueError("Stage requires at least one harness")
        self._harnesses: dict[str, Harness] = dict(harnesses)  # preserves order
        self._bridge = bridge
        self._state: _StageState = _StageState.NEW
        self._connected: list[str] = []   # ordered, deduplicated by state machine
        self._validate_bridge_transport_set()
        self._validate_bridge_distinctness()

    def _validate_bridge_transport_set(self) -> None:
        """If the bridge advertises a configured transport set (e.g.
        MappedBridge), check it matches the registered harnesses exactly.
        Surfaces a typed error rather than a generic KeyError-wrapped
        BridgeTranslationError at first use."""
        configured = getattr(self._bridge, "configured_transports", None)
        if configured is None:
            return  # bridge does not advertise its set; skip
        registered = frozenset(self._harnesses)
        if configured != registered:
            raise BridgeTransportMismatchError(
                bridge_class=type(self._bridge).__name__,
                bridge_transports=tuple(sorted(configured)),
                registered_transports=tuple(sorted(registered)),
            )

    def _validate_bridge_distinctness(self) -> None:
        """Smoke-test the bridge: reject configurations that produce the same
        wire id for distinct transports against a synthetic input.

        This is a startup smoke test, NOT a correctness proof. A bridge that
        returns distinct values for the synthetic input but collides on real
        logical ids will pass this check. See ADR-0027 Security Considerations
        for the residual risk and the per-scope re-validation that catches
        the in-flight collision case.
        """
        synthetic = "STAGE-VALIDATION-1234567890abcdef"  # length-stable, type-string
        seen: dict[str, str] = {}
        for name in self._harnesses:
            wire = self._call_to_wire(synthetic, name)
            if wire in seen:
                raise BridgeAmbiguityError(
                    f"bridge {type(self._bridge).__name__} produced wire id "
                    f"{_redact(wire)} for both transport {seen[wire]!r} and "
                    f"transport {name!r} during startup smoke-test (synthetic "
                    f"input length {len(synthetic)}). The bridge must return "
                    f"distinct wire ids per transport.",
                    transports=(seen[wire], name),
                )
            seen[wire] = name

    def _call_to_wire(self, logical: Any, transport: str) -> str:
        """Wrap to_wire: type-check the return, surface failures as
        BridgeTranslationError. Single chokepoint so every call site uses
        the same validation."""
        try:
            wire = self._bridge.to_wire(logical, transport)
        except Exception as exc:
            raise BridgeTranslationError(
                bridge_class=type(self._bridge).__name__,
                method="to_wire",
                transport=transport,
                original=exc,
            ) from exc
        if not isinstance(wire, str) or not wire:
            raise BridgeTranslationError(
                bridge_class=type(self._bridge).__name__,
                method="to_wire",
                transport=transport,
                original=TypeError(
                    f"to_wire must return a non-empty str, got "
                    f"{type(wire).__name__}"
                ),
            )
        if len(wire) > _MAX_WIRE_ID_LEN:
            raise BridgeTranslationError(
                bridge_class=type(self._bridge).__name__,
                method="to_wire",
                transport=transport,
                original=ValueError(
                    f"to_wire returned a wire id of length {len(wire)}; "
                    f"limit is {_MAX_WIRE_ID_LEN}"
                ),
            )
        return wire

    async def connect(self) -> None:
        """Connect every registered harness in registration order. Fail-fast.

        On the first transport to fail, every harness up to and including the
        failing one is disconnected (the failing harness's connect() may have
        opened resources before raising). The Stage stays in NEW state on
        failure; the caller may construct a fresh Stage to retry, but cannot
        retry connect() on this instance.
        """
        if self._state is not _StageState.NEW:
            raise StageStateError(
                f"Stage.connect() requires state NEW, got {self._state.value}; "
                f"construct a new Stage to reconnect"
            )
        for name, harness in self._harnesses.items():
            try:
                await harness.connect()
            except Exception as exc:
                # Roll back already-connected siblings AND attempt to disconnect
                # the failing harness in case it opened resources before raising.
                await self._rollback(
                    connected_so_far=list(self._connected),
                    failing=(name, harness),
                )
                raise StageConnectError(
                    failing_transport=name,
                    bridge_class=type(self._bridge).__name__,
                ) from exc
            self._connected.append(name)
        self._state = _StageState.CONNECTED

    async def _rollback(
        self,
        *,
        connected_so_far: list[str],
        failing: tuple[str, Harness] | None = None,
    ) -> None:
        """Disconnect the failing harness (best-effort) then every fully-up
        sibling in reverse order. All errors are logged and swallowed; the
        rollback path never raises."""
        if failing is not None:
            failing_name, failing_harness = failing
            try:
                await failing_harness.disconnect()
            except Exception:
                log.warning(
                    "stage_rollback_failing_transport_disconnect_failed",
                    extra={"transport": failing_name},
                    exc_info=True,
                )
        for name in reversed(connected_so_far):
            try:
                await self._harnesses[name].disconnect()
            except Exception:
                log.warning(
                    "stage_rollback_sibling_disconnect_failed",
                    extra={"transport": name},
                    exc_info=True,
                )
        self._connected.clear()

    async def disconnect(self) -> None:
        """Disconnect every connected harness in reverse registration order.

        Idempotent: safe to call from a finally block whether connect()
        succeeded, partially succeeded, or never ran. After this call the
        Stage is in DISCONNECTED state and cannot be reconnected.
        """
        if self._state is _StageState.DISCONNECTED:
            return
        errors: list[Exception] = []
        for name in reversed(self._connected):
            try:
                await self._harnesses[name].disconnect()
            except Exception as exc:  # noqa: BLE001, collected into ExceptionGroup
                errors.append(exc)
        self._connected.clear()
        self._state = _StageState.DISCONNECTED
        if errors:
            # PEP 654: lets consumers use `except* StageError` to handle
            # subsets, and traceback.format_exception walks the group.
            raise StageDisconnectError(
                "stage disconnect raised on one or more transports", errors
            )

    def scenario(self, name: str) -> "StageScenarioScope":
        if self._state is not _StageState.CONNECTED:
            raise StageStateError(
                f"Stage.scenario() requires state CONNECTED, got {self._state.value}"
            )
        from choreo.stage_scenario import StageScenarioScope
        return StageScenarioScope(name=name, stage=self)


_MAX_WIRE_ID_LEN = 1024  # bytes/chars; rejects pathological bridge return values


def _redact(s: str, head: int = 8, tail: int = 4) -> str:
    """Truncate a string for safe inclusion in error messages and logs.

    Wire ids may carry consumer-supplied data; the full value goes only into
    structured fields a redaction policy can scrub, not into message strings.
    """
    if len(s) <= head + tail + 3:
        return repr(s)
    return f"{s[:head]!r}...{s[-tail:]!r} (len={len(s)})"
```

### New module: `packages/core/src/choreo/stage_scenario.py`

The Stage scope owns one per-transport child `_ScenarioContext` per touched harness. The DSL methods route to the right child based on the `on=` selector.

```python
class StageScenarioScope:
    """async-with scope for a multi-transport scenario.

    Children (one _ScenarioContext per registered transport) are minted
    EAGERLY at __aenter__. Eager minting gives bridge translation errors a
    deterministic firing point (scope entry) rather than racing the test
    body, and re-exercises the bridge's per-transport distinctness against
    the actual logical id (the Stage __init__ check uses a synthetic).
    """

    def __init__(self, name: str, stage: Stage) -> None:
        self._name = name
        self._stage = stage
        self._logical_id: Any | None = None
        self._children: dict[str, _ScenarioContext] = {}
        self._entered = False

    async def __aenter__(self) -> "StageScenario":
        if self._entered:
            raise StageStateError("StageScenarioScope is not re-entrant")
        self._entered = True
        try:
            self._logical_id = await self._call_fresh()
            self._mint_all_children()         # eager; raises BridgeTranslationError
                                              # or BridgeAmbiguityError on conflict
        except Exception:
            # Mint failure: __aexit__ will not be called by `async with`,
            # so we must clean up any children minted before the failure.
            await self._teardown()
            raise
        return StageScenario(scope=self)

    async def _call_fresh(self) -> Any:
        try:
            return await self._stage._bridge.fresh()
        except Exception as exc:
            raise BridgeTranslationError(
                bridge_class=type(self._stage._bridge).__name__,
                method="fresh",
                transport=None,
                original=exc,
            ) from exc

    def _mint_all_children(self) -> None:
        """Mint one _ScenarioContext per registered transport with the
        per-transport wire id pre-seeded. Re-validates per-scope distinctness
        using the actual logical id (defends against the smoke-test escape
        described in ADR-0027 Security Considerations)."""
        seen: dict[str, str] = {}
        for name, harness in self._stage._harnesses.items():
            wire_id = self._stage._call_to_wire(self._logical_id, name)
            if wire_id in seen:
                raise BridgeAmbiguityError(
                    f"bridge {type(self._stage._bridge).__name__} produced wire "
                    f"id {_redact(wire_id)} for both transport {seen[wire_id]!r} "
                    f"and transport {name!r} for the active logical scope id; "
                    f"the bridge passed startup validation but collides on "
                    f"real input",
                    transports=(seen[wire_id], name),
                )
            seen[wire_id] = name
            self._children[name] = make_context(
                harness=harness,
                correlation_id=wire_id,
                transport=name,
            )

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        """Tear down every minted child in reverse insertion order. Each
        child's unsubscribe loop is isolated: a failing unsubscribe on one
        transport does not leak callbacks on the others. Same isolation
        pattern as the single-transport _ScenarioScope (scenario.py:1302)."""
        for name in reversed(list(self._children)):
            ctx = self._children.pop(name)
            harness = self._stage._harnesses[name]
            for topic, callback in ctx.subscriber_refs:
                try:
                    harness.unsubscribe(topic, callback)
                except Exception:
                    log.warning(
                        "stage_scope_unsubscribe_failed",
                        extra={"transport": name, "topic": topic},
                        exc_info=True,
                    )
            ctx.subscriber_refs.clear()

    def _child(self, transport: str | None) -> _ScenarioContext:
        if transport is None:
            raise MissingTransportError(
                "Stage scenario DSL methods require an `on=` selector naming "
                "the transport; got on=None"
            )
        ctx = self._children.get(transport)
        if ctx is None:
            raise UnknownTransportError(
                f"transport {transport!r} not registered on Stage; "
                f"known: {list(self._stage._harnesses)}"
            )
        return ctx
```

`StageScenario` is the user-facing object. It mirrors the `Scenario` DSL with `on=` required on every transport-touching call. Internally it constructs a per-call view of the relevant child context and delegates registration to the same `_register_expectation` / `_register_reply` helpers used by single-transport scenarios — extracted to module-level functions so they do not need a `Scenario` instance.

```python
class StageScenario:
    """User-facing DSL surface for a Stage scenario.

    The on= keyword on every method defaults to None so that omission
    surfaces as MissingTransportError (a framework concept) rather than
    Python's generic TypeError. Pass a registered transport name explicitly
    on every transport-touching call.
    """

    def __init__(self, scope: StageScenarioScope) -> None:
        self._scope = scope

    def expect(
        self, topic: str, matcher: Matcher, *, on: str | None = None
    ) -> Handle:
        ctx = self._scope._child(on)
        # transport is set inside _register_expectation via the ctx.transport
        # field, BEFORE the dispatcher can resolve the handle. No post-hoc
        # mutation; no race between handle return and assignment.
        return _register_expectation(ctx, topic=topic, matcher=matcher)

    def publish(
        self, topic: str, payload: Any, *, on: str | None = None
    ) -> "StageScenario":
        ctx = self._scope._child(on)
        _publish_via_context(ctx, topic=topic, payload=payload)
        return self

    def on(
        self,
        trigger_topic: str,
        matcher: Matcher | None = None,
        *,
        on: str | None = None,
    ) -> "StageReplyChain":
        ctx_trigger = self._scope._child(on)
        return StageReplyChain(
            scope=self._scope,
            ctx_trigger=ctx_trigger,
            trigger_transport=on,
            trigger_topic=trigger_topic,
            matcher=matcher,
        )

    async def await_all(self, timeout_ms: int) -> ScenarioResult:
        all_expectations = [
            exp
            for ctx in self._scope._children.values()
            for exp in ctx.expectations
        ]
        # Reuse the existing _await_all implementation, which works on a flat
        # list of expectations. The deadline applies once across them.
        return await _await_all(
            expectations=all_expectations, timeout_ms=timeout_ms
        )


class StageReplyChain:
    """Builder returned by StageScenario.on(). Terminate with .publish() to
    register a reply. The response transport's on= may differ from the
    trigger transport's on=; that is the cross-transport bridge case.

    Reply lifecycle ownership (cross-transport semantics):
      * The _Reply record lives on the TRIGGER context. Its state field
        (ARMED -> FIRED, per ADR-0016) is mutated only by the trigger
        transport's dispatcher, preserving the single-writer invariant
        ADR-0016 relies on for fire-once.
      * The reply emit calls publish on the RESPONSE context's harness with
        the response context's correlation_id stamped via the response
        harness's CorrelationPolicy.
      * If the response transport is disconnected at emit time (e.g. broker
        dropped mid-scenario), Harness.publish raises and _register_reply
        records the reply as FAILED (state FIRED_BUILDER_ERROR with the
        publish exception). The trigger-side fire-once invariant still
        holds: the second arrival is recorded but does not re-emit.
      * If the trigger transport disconnects, the trigger callback never
        fires; the reply ends scope as ARMED_NO_MATCH and surfaces in the
        per-transport ReplyReport breakdown.
    """

    def __init__(
        self,
        *,
        scope: StageScenarioScope,
        ctx_trigger: _ScenarioContext,
        trigger_transport: str,
        trigger_topic: str,
        matcher: Matcher | None,
    ) -> None:
        self._scope = scope
        self._ctx_trigger = ctx_trigger
        self._trigger_transport = trigger_transport
        self._trigger_topic = trigger_topic
        self._matcher = matcher

    def publish(
        self,
        response_topic: str,
        *,
        on: str | None = None,
        build: Callable[[Any], Any],
    ) -> StageScenario:
        """Terminate the chain. on= names the RESPONSE transport. Returns
        the parent StageScenario so chains can be expressed inline."""
        ctx_response = self._scope._child(on)
        _register_reply(
            ctx_trigger=self._ctx_trigger,
            ctx_response=ctx_response,
            trigger_transport=self._trigger_transport,
            response_transport=on,
            trigger_topic=self._trigger_topic,
            trigger_matcher=self._matcher,
            response_topic=response_topic,
            build=build,
        )
        # Returning the parent scenario rather than self makes one-statement
        # chains type-stable (StageScenario in -> StageScenario out).
        return StageScenario(scope=self._scope)
```

### Per-transport context refactor

`_ScenarioContext` already has the right shape ([packages/core/src/choreo/scenario.py:568-576](../../packages/core/src/choreo/scenario.py#L568-L576)). Three changes land in `scenario.py`:

1. **`_ScenarioContext` gains a `transport: str | None` field.** None for single-transport contexts (existing behaviour); the transport name for Stage children. `_register_expectation` reads this field when constructing the `Handle` so `Handle.transport` is set inside the constructor — no post-hoc mutation, no race against the dispatcher.

2. **Construction moves to a module-level `make_context(harness, correlation_id, transport=None)` helper.** Both single-transport `Scenario` and the Stage's children call this helper. Single-transport callers omit `transport=`; the default is `None`. No behavioural change for single-transport scenarios.

3. **Registration helpers extracted to module level.**
   - `_register_expectation(ctx, *, topic, matcher) -> Handle` — reads `ctx.transport` when constructing the `Handle`, never mutates the `Handle` after return. (Existing logic at [scenario.py:718-851](../../packages/core/src/choreo/scenario.py#L718-L851).)
   - `_publish_via_context(ctx, *, topic, payload) -> None` — wraps the encode/correlation-write/transport-publish path that `Scenario.publish` runs inline today.
   - `_register_reply(ctx_trigger, ctx_response, *, trigger_transport, response_transport, trigger_topic, trigger_matcher, response_topic, build) -> None` — two-context signature. For same-transport replies, both context arguments are the same instance; the implementation reads correlation_id from `ctx_trigger` for the inbound filter and from `ctx_response` for the outbound stamp. The fire-once `_Reply.state` field is mutated only on `ctx_trigger` (single-writer, preserving ADR-0016's invariant; see `StageReplyChain` docstring above). The `_Reply` dataclass gains `trigger_transport: str | None` and `response_transport: str | None` fields, propagated into `ReplyReport` for per-transport diagnostics.

`_await_all` at [packages/core/src/choreo/scenario.py:1166-1214](../../packages/core/src/choreo/scenario.py#L1166-L1214) already operates on a flat list of expectations and a single deadline. No signature change.

### Handle and ScenarioResult changes

```python
@dataclass
class Handle:
    topic: str
    matcher_description: str
    correlation_id: str
    outcome: Outcome = Outcome.PENDING
    transport: str | None = None   # None for single-harness, name for Stage.
                                   # Set inside _register_expectation from
                                   # ctx.transport; never assigned post-hoc.
    _message: Any = None
    _latency_ms: float | None = None
    _reason: str = ""
```

```python
@dataclass(frozen=True)
class ScenarioResult:
    handles: tuple[Handle, ...]
    # ... existing fields ...
    by_transport: Mapping[str, tuple[Handle, ...]]   # populated at construction

# Construction-time helper used inside the existing ScenarioResult factory.
# Single-harness Scenarios produce results where every handle.transport is
# None and `by_transport` is an empty mapping; callers checking
# `result.by_transport.get("foo")` get None, which is the correct "not a
# Stage scenario" signal. Stage Scenarios produce one entry per touched
# transport. Single-harness callers who want to iterate per-transport can
# always use `result.handles` directly.
def _build_by_transport(
    handles: Iterable[Handle],
) -> Mapping[str, tuple[Handle, ...]]:
    groups: dict[str, list[Handle]] = {}
    for h in handles:
        if h.transport is None:
            continue
        groups.setdefault(h.transport, []).append(h)
    return {k: tuple(v) for k, v in groups.items()}
```

### Error types

The hierarchy splits along Python's standard taxonomy: configuration / lookup errors inherit from `LookupError` or `ValueError` so consumers can `except LookupError` for typo-style mistakes, while runtime translation failures inherit from `RuntimeError` (the same base ADR-0019 chose for `CorrelationPolicyError`). Every type also inherits from a `StageError` mixin so consumers wanting catch-all handling can `except StageError` regardless of the standard taxon.

```python
class StageError(Exception):
    """Mixin marker for every Stage-emitted exception. Allows
    `except StageError` as a catch-all without losing the standard taxonomy
    each subclass also inherits from."""


# --- configuration / lookup errors (caller mistakes; ValueError/LookupError) ---

class StageStateError(StageError, RuntimeError):
    """Stage method called in the wrong lifecycle state (e.g. connect()
    twice, scenario() before connect(), connect() after disconnect())."""


class MissingTransportError(StageError, ValueError):
    """Raised when a Stage scenario DSL call passes on=None or omits on=."""


class UnknownTransportError(StageError, LookupError):
    """Raised when on= names a transport not registered on the Stage."""


class BridgeAmbiguityError(StageError, ValueError):
    """Raised at Stage.__init__ (synthetic input) or scope entry (real
    logical id) when the bridge maps two transports to identical wire ids.
    Carries the colliding transport names; the wire id is redacted in the
    message string. Consumers needing the full value must implement a
    bridge whose __cause__ carries it explicitly."""

    def __init__(self, message: str, *, transports: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.transports = transports


class BridgeTransportMismatchError(StageError, ValueError):
    """Raised at Stage.__init__ when a bridge advertising
    `configured_transports` does not match the registered harness set
    exactly. Surfaces the mismatch as a typed error rather than as a
    generic KeyError-wrapped BridgeTranslationError at first use."""

    def __init__(
        self,
        *,
        bridge_class: str,
        bridge_transports: tuple[str, ...],
        registered_transports: tuple[str, ...],
    ) -> None:
        super().__init__(
            f"{bridge_class} configured for transports {bridge_transports} but "
            f"Stage registered {registered_transports}; the sets must match"
        )
        self.bridge_class = bridge_class
        self.bridge_transports = bridge_transports
        self.registered_transports = registered_transports


# --- runtime errors (bridge/transport failures during operation; RuntimeError) ---

class StageConnectError(StageError, RuntimeError):
    def __init__(self, *, failing_transport: str, bridge_class: str) -> None:
        super().__init__(
            f"Stage.connect aborted: transport {failing_transport!r} failed "
            f"(bridge: {bridge_class}); the failing transport AND every "
            f"already-connected transport were disconnected"
        )
        self.failing_transport = failing_transport
        self.bridge_class = bridge_class


class StageDisconnectError(StageError, ExceptionGroup):
    """PEP 654 ExceptionGroup for one or more disconnect failures.

    Use with `except*`:
        try:
            await stage.disconnect()
        except* StageDisconnectError as eg:
            for exc in eg.exceptions:
                ...
    """

    def __new__(cls, message: str, errors: Sequence[Exception]) -> "StageDisconnectError":
        return super().__new__(cls, message, list(errors))


class BridgeTranslationError(StageError, RuntimeError):
    """Wraps any exception raised by bridge.fresh / to_wire / from_wire, or
    a type-validation failure on the bridge's return value.

    Mirrors ADR-0019's `CorrelationPolicyError` shape: bridge_class, method,
    and the original exception are all on named attributes, not just on
    __cause__. Consumers can write `except BridgeTranslationError as e:
    log(e.original)` without walking the chain."""

    def __init__(
        self,
        *,
        bridge_class: str,
        method: str,
        transport: str | None,
        original: BaseException,
    ) -> None:
        location = f" for transport {transport!r}" if transport else ""
        super().__init__(
            f"{bridge_class}.{method} raised "
            f"{type(original).__name__}{location}: {original}"
        )
        self.bridge_class = bridge_class
        self.method = method
        self.transport = transport
        self.original = original
```

### `Harness` is unchanged

No constructor changes. No method signature changes. The `on=` keyword is **not** added to `harness.scenario(...)`'s DSL surface — calling `s.expect(topic, matcher, on="x")` on a single-harness scenario raises `TypeError` from the existing kwargs handling, which is the desired failure mode.

### Migration Path

Not applicable — additive feature. Existing single-transport tests run unchanged. Adoption is opt-in: a consumer wanting multi-transport scenarios constructs a `Stage` instead of (or in addition to) a `Harness`.

### Timeline

- **Phase 1:** Land `Stage`, `CorrelationBridge`, `IdentityBridge`, `MappedBridge`, `StageScenarioScope`, `StageScenario`, `StageReplyChain`, error types, and `Handle.transport` / `ScenarioResult.by_transport` additions. Refactor `_ScenarioContext` / `_register_expectation` / `_register_reply` to module-level helpers. Unit and integration tests with `MockTransport`.
- **Phase 2:** End-to-end test with two real transports under `pytest -m e2e`. Add a Stage section to the README and `docs/framework-design.md`. Publish a worked example fixture.
- **Phase 3 (optional):** Add convenience helpers like `Stage.from_pairs([(name, harness), ...])` if telemetry shows the dict-construction ergonomics need improving. Do not ship in v1.

---

## Validation

### Success Metrics

**Backward compatibility:**
- **Single-transport regression suite passes unchanged.** Run the existing scenario test suite against the post-Stage main; 100% pass with no test modifications. Target: passes.
- **Mixed-mode coexistence.** A test fixture constructs both a `Harness` (single-transport) and a `Stage` (multi-transport) in the same process. Both run scenarios concurrently; neither leaks state into the other. Target: passes.

**Core round-trip:**
- **Canonical bridge round-trip ≤ 15 lines.** Worked-example test `tests/integration/test_stage_round_trip.py` implements NATS-publish / Kafka-reply / NATS-expect using two `MockTransport` instances. Counted lines of the scenario body (excluding fixture setup): target ≤ 15.
- **Parallel isolation at 100 Stage scopes.** Test launches 100 concurrent `stage.scenario()` blocks, each with two transports and a deterministic `MappedBridge`. Every scope's handles resolve from messages tagged with its scope's wire ids on each transport; zero cross-scope matches across all 100. Target: passes.

**Lifecycle and state machine:**
- **Connect rollback disconnects the failing transport too.** Test installs a transport whose `connect()` opens a side-effect resource (a counter increment) before raising. Rollback must run `disconnect()` on that failing transport, surfaced via the side-effect resource being released. Target: passes; raised error is `StageConnectError`; every harness reports `is_connected() is False`.
- **State machine rejects re-use.** Test calls `stage.connect()`, `stage.disconnect()`, then `stage.connect()` again. The second connect raises `StageStateError`. Target: passes.
- **`disconnect()` is idempotent.** Test calls `stage.disconnect()` twice in a row. The second call returns without raising. Target: passes.
- **`stage.scenario()` rejects pre-connect.** Test calls `stage.scenario("x")` before `stage.connect()`. Raises `StageStateError`. Target: passes.

**Disconnect aggregation:**
- **`StageDisconnectError` is an `ExceptionGroup`.** Test installs two transports both raising on disconnect. `await stage.disconnect()` raises a `StageDisconnectError` whose `.exceptions` lists both originals. `try/except* StageDisconnectError` walks the group correctly. Target: passes.

**Bridge protocol enforcement:**
- **`BridgeAmbiguityError` at startup smoke test.** Stage with two transports and a bridge whose `to_wire` returns the same value for the synthetic input. `Stage.__init__` raises `BridgeAmbiguityError` with the colliding transport names on `.transports`. Target: raised before `connect()`.
- **`BridgeAmbiguityError` at scope entry (smoke-test escape).** Bridge passes the synthetic-input smoke test but collides on the real `bridge.fresh()` value. `__aenter__` raises `BridgeAmbiguityError`; the scope never enters; no children are minted. Target: raised; no callback leakage.
- **`BridgeTransportMismatchError` for `MappedBridge`.** Stage registers transports `{"nats", "kafka"}`; `MappedBridge` configured with only `{"nats"}`. `Stage.__init__` raises `BridgeTransportMismatchError` (NOT `BridgeTranslationError`) listing both sets. Target: passes.
- **`BridgeTranslationError` carries `.original`.** Bridge whose `fresh()` raises `RuntimeError("boom")`; running a scenario surfaces `BridgeTranslationError(method="fresh")` with `e.original is the RuntimeError` AND `e.__cause__ is the RuntimeError`. Target: both true; event loop unaffected.
- **`to_wire` return-type validation.** Bridges returning `None`, `42`, `""`, and a 2000-char string all raise `BridgeTranslationError` with `e.original` being a `TypeError` or `ValueError` describing the violation. Target: four parametrised cases pass.

**DSL error semantics:**
- **`MissingTransportError` raised on `on=None`.** `s.expect("topic", matcher)` (no `on=` passed) raises `MissingTransportError`, NOT `TypeError`. Same for `s.publish` and `s.on`. Target: passes.
- **`UnknownTransportError` raised on `on="typo"`.** `s.expect("topic", matcher, on="ntas")` raises `UnknownTransportError` listing the known transports. Target: passes.
- **`StageScenarioScope` not re-entrant.** Calling `__aenter__` twice on the same scope raises `StageStateError`. Target: passes.

**Reply lifecycle:**
- **Cross-transport reply fires once per scope.** Test registers `s.on("trigger", on="kafka").publish("response", on="nats", build=...)`. Publishing one trigger on Kafka produces exactly one response on NATS; a second trigger does not produce a second response (ADR-0016 fire-once preserved). `_Reply.state` is mutated only on the trigger context. Target: passes.
- **Cross-transport reply with response transport disconnected.** Trigger arrives on Kafka; the NATS harness has been disconnected mid-scenario. The reply emit's publish raises; the reply records as `FIRED_BUILDER_ERROR`; the scenario fails with the response transport named in `ReplyReport.response_transport`. Target: passes.
- **Same-transport reply via Stage matches single-transport reply behaviour.** Test registers `s.on("a", on="nats").publish("b", on="nats", build=...)`. Identical observable behaviour to the single-transport `harness.scenario(...)` reply test (parametrised pair). Target: both pass.

**Mid-scenario broker drop:**
- **Inbound after broker drop resolves as TIMEOUT.** Test registers `s.expect("x", on="nats")`, simulates a broker drop on NATS mid-scenario, awaits the global deadline. Handle resolves as `TIMEOUT` with `Handle.transport == "nats"`. Target: passes.
- **`__aexit__` survives a unsubscribe failure on one transport.** Test installs a harness whose `unsubscribe` raises after broker drop. Scope exit logs a WARNING (`stage_scope_unsubscribe_failed`) but completes; siblings' subscribers are still torn down. Subsequent scopes start clean. Target: passes; no leaked callbacks.

**Handle and result shape:**
- **`Handle.transport` populated at construction, never post-hoc.** Test registers an expectation, immediately resolves it (synthetic match), asserts `handle.transport == on` AND that no race-window code in `StageScenario.expect` mutates the handle after return. Target: passes (verified by reading the implementation, asserted by absence of post-hoc mutation in code review).
- **`ScenarioResult.by_transport` keys are real transport names only.** Stage test asserts keys are exactly the transports touched. Single-harness test asserts `result.by_transport` is empty (handles' `transport` is None, no key). Target: passes; `result.by_transport.get("any")` returns `None` for single-harness results (no surprising `""` key).

### Monitoring

- CI gate on every Success Metric above.
- Structured WARNING events emitted by the Stage are captured in CI and asserted on by the relevant test:
  - `stage_rollback_failing_transport_disconnect_failed`
  - `stage_rollback_sibling_disconnect_failed`
  - `stage_scope_unsubscribe_failed`
  - `stage_from_wire_failed`
- A docs CI check greps the README and `docs/framework-design.md` for the Stage section after Phase 2 lands; absence fails the build.
- The bridge-distinctness validation runs in two passes (startup + per-scope); regression tests assert both fire on the appropriate failure mode.
- A regression-style code-review check (manual or via a custom linter) flags any post-hoc mutation of `Handle.transport` outside `_register_expectation`. Direct attribute assignment to a returned `Handle` is the bug pattern this ADR explicitly designs against.

---

## Related Decisions

- [PRD-011](../prd/PRD-011-multi-transport-stage.md) — the requirements this ADR implements.
- [ADR-0001](0001-single-session-scoped-harness.md) — the single-harness lifecycle the Stage coordinates across; unchanged.
- [ADR-0002](0002-scoped-registry-test-isolation.md) — per-harness scoped registry the Stage's child contexts inherit; unchanged.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — the per-topic dispatch path the Stage subscribes through; unchanged.
- [ADR-0012](0012-type-state-scenario-builder.md) — the type-state DSL `Scenario` uses; `StageScenario` mirrors it but does not subclass (separate types per Option 1 rationale).
- [ADR-0014](0014-handle-result-model.md) — `Handle` gains `transport: str | None` under this ADR. ADR-0014 should be amended in its Notes to reference this addition; no behavioural change for single-harness handles.
- [ADR-0016](0016-reply-lifecycle.md) — fire-once, scope-bound replies. Cross-transport replies inherit the same lifecycle; the trigger and response live in different per-transport child contexts but share the Stage scope's teardown.
- [ADR-0019](0019-pluggable-correlation-policy.md) — per-harness `CorrelationPolicy`. The Stage composes harness policies via the bridge; per-harness policy semantics are unchanged. The bridge is to the Stage what the policy is to a harness — the exception-wrapping pattern is reused verbatim.

---

## References

- Current single-transport `Harness.scenario()`: [packages/core/src/choreo/harness.py:191](../../packages/core/src/choreo/harness.py#L191).
- `_ScenarioContext` shape: [packages/core/src/choreo/scenario.py:568-576](../../packages/core/src/choreo/scenario.py#L568-L576).
- Expectation registration to be extracted: [packages/core/src/choreo/scenario.py:718-851](../../packages/core/src/choreo/scenario.py#L718-L851).
- Reply registration to be extracted: [packages/core/src/choreo/scenario.py:899-1132](../../packages/core/src/choreo/scenario.py#L899-L1132).
- `_await_all` aggregation: [packages/core/src/choreo/scenario.py:1166-1214](../../packages/core/src/choreo/scenario.py#L1166-L1214).
- `Handle` dataclass: [packages/core/src/choreo/scenario.py](../../packages/core/src/choreo/scenario.py) (search `class Handle`).
- Per-harness `CorrelationPolicy` Envelope shape: [packages/core/src/choreo/correlation.py](../../packages/core/src/choreo/correlation.py).

---

## Notes

- **Deferred — learn-by-observation bridge.** A bridge variant for the case where the AUT generates a fresh wire id on the second transport and the test cannot predict it upfront. The bridge would observe the AUT's first cross-transport message, learn the mapping, and register it for the response leg. Requires an inbound observation hook the v1 design does not have. **Owner:** Platform.
- **Deferred — per-transport latency budgets.** `Handle.within_ms()` covers per-handle budgets today. A scope-level "Kafka is slow, default 200ms; NATS 20ms" might emerge from real usage; if so, design as a `Stage(default_budgets={...})` constructor parameter consumed at expectation registration. **Owner:** Platform.
- **Deferred — `Stage` introspection helpers.** `stage.transport_names()`, `stage.harness(name)`, etc. Add when consumer code needs them; not required for v1. **Owner:** Framework maintainers.
- **Deferred — topic-name conflict diagnostics.** PRD-011 §Open Questions raised whether two transports with the same topic name should emit a registration-time warning. Resolved: no warning in v1 — `on=` already disambiguates and per-protocol topic naming conventions differ. Revisit if real consumers report confusion. **Owner:** Platform.
- **Deferred — Stage-level CorrelationBridge inference.** PRD-011 §Open Questions raised whether the Stage should infer `IdentityBridge` when the consumer omits `bridge=`. Resolved: no — `bridge=` is required, and `IdentityBridge` self-rejects for multi-transport Stages via `BridgeAmbiguityError`. Failing fast is preferred over a silent default that would produce wrong correlation routing. **Owner:** Platform.
- **Deferred — mixed-mode coexistence.** PRD-011 §Open Questions asked whether `Harness` and `Stage` can be used together in one process. Resolved: yes, they share no state. §Validation Success Metrics includes a coexistence regression test. **Owner:** Implementing engineer.
- **Deferred — worked end-to-end example.** Neither this ADR nor PRD-011 ships the canonical end-to-end example (publish → trigger → reply → expect → assert) in one block. The §Implementation sketches show internals; PRD-011 §Stage API shows snippets. The user-facing complete example lands in Phase 2 alongside the README / `docs/framework-design.md` Stage section. **Owner:** Author of this ADR.
- **Open follow-up — amend ADR-0014 Notes.** `Handle.transport` is added by this ADR. ADR-0014's Notes section should reference this addition once ADR-0027 is Accepted. **Owner:** Author of this ADR.
- **Open follow-up — refactor of single-transport scope.** Extracting `_register_expectation` and `_register_reply` to module-level helpers, plus the new `_ScenarioContext.transport` field and the two-context reply signature, is a non-trivial refactor of `scenario.py`. Reviewers should pay close attention to the existing replies test suite (`test_replies.py`) — its assertions on reply lifecycle are the load-bearing regression gate, especially the fire-once invariant ([ADR-0016](0016-reply-lifecycle.md) §Decision) which the cross-transport reply preserves by mutating `_Reply.state` only on the trigger context. **Owner:** Implementing engineer.
- **Open follow-up — `_MAX_WIRE_ID_LEN` calibration.** Set to 1024 chars in v1 as a generous upper bound that catches pathological bridge return values. If real consumers find legitimate use cases approaching the limit (long structured event ids with embedded checksums, traceparents, etc.) the constant should be raised before they hit it; if telemetry shows nothing approaches even 256, the limit could tighten. **Owner:** Platform — review at first PyPI release.
- **Naming.** `Stage` was chosen over `Ensemble`, `Conductor`, `Choir` in the PRD-011 design discussion (2026-05-04). Open to revisit before public API freeze; the name is concentrated in one module and the migration cost is low if changed pre-1.0.

**Last Updated:** 2026-05-04
