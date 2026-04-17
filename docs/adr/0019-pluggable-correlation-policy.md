# 0019. Pluggable Correlation Policy with No-Op Default

**Status:** Proposed
**Date:** 2026-04-18
**Deciders:** Platform / Test Infrastructure
**Technical Story:** The library currently mutates caller payloads by injecting a `correlation_id` field with a `TEST-` prefix. For a public, general-purpose library this is too opinionated: a third-party consumer cannot be assumed to want the mutation, cannot be assumed to name the field that way, and cannot be assumed to need parallel-scope routing at all. Correlation behaviour needs to be pluggable, with a transparent default, so consumers opt into the posture that fits their schema.

---

## Context

Choreo's parallel-test-isolation story is built on correlation-ID routing. Three call sites in [packages/core/src/core/scenario.py](../../packages/core/src/core/scenario.py) mutate payloads today:

- `Scenario.publish()` at [scenario.py:696](../../packages/core/src/core/scenario.py#L696) — `payload.setdefault("correlation_id", scope_corr)`.
- Reply emit path at [scenario.py:1048](../../packages/core/src/core/scenario.py#L1048) — same setdefault on the reply's reply payload.
- Scope construction at [scenario.py:1240](../../packages/core/src/core/scenario.py#L1240) — generates `TEST-<32 hex>` via [correlation.py:10-17](../../packages/core/src/core/correlation.py#L10-L17).

Inbound filtering at [scenario.py:788-790](../../packages/core/src/core/scenario.py#L788-L790) (expect) and [scenario.py:945-973](../../packages/core/src/core/scenario.py#L945-L973) (reply) silently drops messages whose `correlation_id` does not match the scope. [core/dispatcher.py:72-137](../../packages/core/src/core/dispatcher.py#L72-L137) routes on the same field for cross-scope isolation.

Three things are hardcoded in the library today:

1. **Field name** — the literal string `"correlation_id"` in a dict.
2. **Format** — the prefix `TEST-` plus 32 hex characters.
3. **Injection posture** — mutate any outbound dict payload on the caller's behalf.

All three are reasonable defaults *in a captive internal deployment* where every service echoes `correlation_id`, the `TEST-` prefix enforces the ingress-side test-traffic filter that [ADR-0006](0006-environment-boundary-enforcement.md) specifies, and the field-name assumption is baked into internal message schemas.

None of these are reasonable defaults for a public third-party consumer. A tag-value-protocol consumer wants tag 11. A Kafka consumer wants a header. A protobuf consumer has a schema that rejects unknown fields. A REST shop wants `X-Correlation-ID`. A user who drops the library on top of their service and calls `s.publish("orders.new", order)` should see their exact `order` arrive on the wire — not a dict silently augmented with a `correlation_id` key they did not ask for.

### Background

- [ADR-0002](0002-scoped-registry-test-isolation.md) — parallel isolation is layered on top of scoped cleanup via correlation-ID routing.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — the dispatcher is the single correlation → scope lookup.
- [ADR-0006](0006-environment-boundary-enforcement.md) — `TEST-` prefix is described as the ingress-side second line of defence.
- [ADR-0018](0018-reply-correlation-scoping.md) — replies reuse the expect-filter contract, auto-stamping the scope's correlation on reply payloads.
- Open-source posture: the repo is being prepared for public release. The library must not silently rewrite caller payloads, and its defaults must be transparent to a user reading the call site.

### Problem Statement

How does the library offer parallel-scope correlation routing as an opt-in capability for consumers who need it, without mutating payloads by default or hardcoding a field name, format, or prefix that only makes sense in a specific captive deployment?

### Goals

- **Transparent default.** With no explicit configuration, `s.publish(topic, payload)` sends `payload` unchanged. No library-injected fields. No hidden prefix. No filter on receive.
- **Opt-in parallel routing.** Consumers who want per-scope correlation isolation configure a `Correlator` once at `Harness` construction; behaviour today is reproducible by selecting the shipped `DictFieldCorrelator`.
- **Library does not embed deployment-specific choices.** The `TEST-` prefix, the `"correlation_id"` field name, and the ingress-filter story all move from library core into a named `Correlator` profile that consumers opt into.
- **Preserve call-site control.** A test that wants to stamp its own correlation on a specific publish can do so without fighting the framework.
- **Single code path for expect, reply, and dispatch.** One `Correlator` instance drives injection and extraction everywhere; there is no "reply does it differently to expect".

### Non-Goals

- **New header-level correlation primitive.** The `Correlator` operates on the payload the codec produced; header transport is a future transport-contract decision.
- **Automatic SUT-echo verification.** This ADR does not resolve [ADR-0002](0002-scoped-registry-test-isolation.md)'s open blocker on correlation echo. It changes who owns the assumption — from library to consumer.
- **Per-scope correlator overrides.** One `Correlator` per `Harness`; swapping mid-session is not supported.
- **Backwards compatibility with the 2026-04-17 default.** The default is changing; consumers who relied on implicit injection need to opt in to the `DictFieldCorrelator` profile. See Migration Path.

---

## Decision Drivers

- **Open-source trust posture.** A public user who reads `s.publish("orders.new", order)` should be able to predict what arrives on the wire without reading the library's source.
- **Field-name neutrality.** Real-world message schemas do not agree on a correlation field name. Hardcoding one makes the library unusable for a large fraction of potential users without them knowing why their SUT is misrouting.
- **Observability of assumptions.** "We assume the SUT echoes correlation on every hop" is load-bearing but currently invisible. Making the correlator explicit surfaces the assumption at the construction site.
- **Reversibility.** Removing auto-injection later would break every consumer's tests. Adding it back as a named profile is one line in a fixture.
- **Separation of transport, codec, and correlation.** Today the transport sends bytes, the codec owns encode/decode, and the library owns correlation injection. Correlation injection is the odd one out — it mutates payload shape without the codec's involvement.

---

## Considered Options

### Option 1: Pluggable `Correlator` protocol with `NoCorrelator` default (chosen)

**Description:** The `Harness` accepts an optional `correlator: Correlator` parameter. The default is `NoCorrelator` — no stamp, no extract, no filter. Consumers who want per-scope routing construct a named correlator (`DictFieldCorrelator`, `HeaderCorrelator` later, `TestNamespaceCorrelator` for the test-namespace ingress-filter posture) and pass it at `Harness` construction. Scenario and reply use one method set: `correlator.generate()`, `correlator.stamp(payload, correlation_id)`, `correlator.extract(payload)`. When extract returns `None`, inbound routing falls back to broadcast (every live scope gets a copy; scopes that don't match topic + matcher drop it silently). Authors who want to stamp at a call site use `s.stamped(payload)` or set the field themselves.

**Pros:**
- Default is transparent — a user reading `s.publish(topic, payload)` sees exactly what goes on the wire.
- The current library behaviour is reproducible by instantiating `TestNamespaceCorrelator()` in the consumer's fixture; no library-core regression.
- One contract for expect, reply, dispatch — no "same mechanism in three places" rewrites.
- Header-correlation, tag-value-protocol tag 11, protobuf-field correlation, etc., are all just different `Correlator` implementations; the library doesn't grow a new primitive per schema.
- Public users opt into complexity only when they need it; the "hello world" path is unsurprising.

**Cons:**
- Changes the default for existing test suites. Tests that relied on implicit injection stop routing by correlation until their fixture is updated. Mitigated by shipping `TestNamespaceCorrelator` and documenting the one-line migration.
- Parallel-scope tests without a correlator all broadcast; a scope could see a message from a sibling scope on the same topic. This is the intended semantics — no correlator means no routing — but must be prominently documented because the failure mode is subtle (extra matches, not missing ones).

### Option 2: Keep implicit injection; add a `Correlator` for field-name override only

**Description:** Retain `setdefault("correlation_id", ...)` as the default; introduce a `Correlator` abstraction only so consumers can change the field name (e.g. `"trace_id"`). `TEST-` prefix and injection posture are unchanged.

**Pros:**
- Minimal migration for an in-repo consumer suite.
- Preserves the [ADR-0006](0006-environment-boundary-enforcement.md) `TEST-` prefix story without a public-release carve-out.

**Cons:**
- Does not fix the trust posture problem. A public consumer still gets silent payload mutation; they just get to pick which key name it happens under.
- `TEST-` prefix is still in library core; public users inherit an internal ingress-filter convention they have no use for.
- Half-measure — the ADR reviews would ask "why not go all the way" and there's no honest answer.

### Option 3: Per-call opt-in only (no Harness-level correlator)

**Description:** Remove all implicit injection. The only way to stamp correlation is per call: `s.publish(topic, payload, correlation_id=...)` or a wrapper helper `s.stamp(payload)`. No policy object; the Harness never knows about correlation.

**Pros:**
- Maximally transparent — every correlation stamp is at a call site.
- No new abstraction in library core.

**Cons:**
- Dispatcher inbound routing needs an extractor. Without a `Correlator`-like object, the dispatcher has nowhere to look up "where does correlation live in this payload shape".
- Every `publish()` in consumer code grows a parameter. Ergonomics collapse.
- Fan-out case (consumer wants every publish correlated) has no "set it once" escape hatch.

### Option 4: Remove correlation from the library entirely

**Description:** Library does not know about correlation at all. Consumers who need parallel isolation build their own layer on top.

**Pros:**
- Smallest possible library surface.
- Zero public-release trust burden.

**Cons:**
- Sacrifices the parallel-isolation guarantee that [ADR-0002](0002-scoped-registry-test-isolation.md) exists to provide. Choreo without correlation routing is a different product.
- Every consumer re-derives the dispatcher's correlation-to-scope lookup. Duplication and bugs.
- Ruled out: the framework's reason to exist is parallel isolation at 100+ scopes; removing it to solve a public-posture problem overshoots.

---

## Decision

**Chosen Option:** Option 1 — Pluggable `Correlator` protocol with `NoCorrelator` default. `TestNamespaceCorrelator` ships as a named profile that in-house consumers instantiate in their own fixtures, not in the library core.

### Rationale

- Option 1 is the only option that simultaneously solves the public trust posture ("I can read the publish line and know what goes on the wire") and preserves parallel isolation ("an in-house 100-scope suite keeps working if I pass one constructor argument").
- Option 2 is the status quo with a rename; reviewers would — correctly — ask why the default still mutates payloads.
- Option 3's ergonomics collapse under fan-out. In-house consumer suites today do not set correlation per call; making them do so would be a footgun-per-test regression.
- Option 4 overshoots. Parallel isolation is the product.
- The `NoCorrelator` default makes the extract-returns-None fallback (broadcast) the library's default inbound behaviour. This aligns library default with the honest capability — no correlator means no isolation — instead of shipping implicit isolation behind the user's back.

**Reading:** the library ships a correlation abstraction; the default behaviour is no correlation; consumers who need parallel-scope isolation name their policy, and an in-house consumer that needs the behaviour is one such case.

---

## Consequences

### Positive

- A public user's `s.publish(topic, payload)` produces exactly `payload` on the wire. The library does not mutate caller data by default.
- Consumers with schemas that forbid unknown fields (protobuf strict, tag-value protocols, Avro) can use Choreo without wrestling the injector.
- `TEST-` prefix and `"correlation_id"` field name leave library core and live as a named `TestNamespaceCorrelator` profile that an in-house consumer fixture instantiates. Other consumers pick their own profile.
- Inbound routing degrades honestly: with `NoCorrelator`, broadcast; with a real correlator, per-scope routing. No middle state where routing silently breaks because the SUT stopped echoing a field the library never asked it to.
- Reply stamping is identical to expect filtering — one `Correlator` instance drives both, removing the one-off `setdefault` at [scenario.py:1048](../../packages/core/src/core/scenario.py#L1048).

### Negative

- Any in-repo consumer suite must update fixtures to pass `TestNamespaceCorrelator()` or lose parallel isolation. Migration is a single line per `Harness()` construction; worst-case blast radius is the shared conftest.
- Public consumers who need parallel isolation have to read one documentation page before they get it. The previous behaviour gave it implicitly; the new behaviour requires one decision.
- Broadcast default with multiple scopes on the same topic can produce surprising match counts if users are not thinking about correlation. Mitigated by a prominent doc warning and a runtime DEBUG line from the dispatcher when broadcast fan-out exceeds one matching scope.
- [ADR-0006](0006-environment-boundary-enforcement.md)'s ingress-filter story via `TEST-` prefix becomes a consumer-chosen posture rather than a library guarantee. In-house deployments that configure `TestNamespaceCorrelator` keep their threat model unchanged; deployments that copy a fixture without the correlator lose the downstream `TEST-` filter's test-traffic coverage. The fixture's docstring and the `TestNamespaceCorrelator` constructor both call this out.

### Neutral

- The `Correlator` lives in `core/correlation.py` alongside the existing generator; the old `_PREFIX` constant is removed. The `CorrelationIdNotInTestNamespaceError` assertion at [scenario.py:45-66](../../packages/core/src/core/scenario.py#L45-L66) and [scenario.py:697-699](../../packages/core/src/core/scenario.py#L697-L699) moves inside `TestNamespaceCorrelator.stamp()` — enforcement happens only when the consumer opts into that correlator.
- `Scenario.correlation_id` exposes the scope's generated ID so authors can stamp manually into any shape (protocol tag, header, nested protobuf field) without fighting the codec.
- `s.stamped(payload)` is a convenience that delegates to the active `Correlator.stamp()`. With `NoCorrelator`, it is an identity function — callers can write stamp-site code that works regardless of policy.

### Security Considerations

- **No default prefix on outbound correlation.** Downstream `TEST-`-prefix ingress filters only work for consumers who configure `TestNamespaceCorrelator`. An in-house fixture that needs the posture must configure it; [ADR-0006](0006-environment-boundary-enforcement.md)'s second-line-of-defence story only holds in deployments that instantiate the correlator. Consumers who rely on downstream filtering must configure a correlator that emits the prefix their ingress layer filters on, and the `TestNamespaceCorrelator` docstring says so explicitly.
- **Payload confidentiality.** The `NoCorrelator` default reduces the surface where the library touches payloads. A payload containing sensitive data is no longer silently augmented with a correlation field — lowering the chance that a redaction rule tuned to the original schema misses a library-added field.
- **Broadcast fan-out risk.** With `NoCorrelator`, all live scopes receive all inbound messages matching topic + matcher. A scope that asserts on the absence of a message (`expect_none` semantics, when added) could be fooled by a sibling scope's publish. Mitigated by documenting that `expect_none` and similar negative assertions require a correlator with a working extract path; the matcher API will gate negative assertions on `correlator is not NoCorrelator` in a follow-up.
- **Injection remains auditable.** With any non-`NoCorrelator` in place, every stamp and every extract flows through one object; a security review can inspect one file to understand what the library writes to and reads from payloads. The previous three-call-site setdefault scatter is eliminated.
- **Supersedes part of [ADR-0006](0006-environment-boundary-enforcement.md).** The allowlist guard, default-deny posture, and no-production-endpoint-in-repo rules in ADR-0006 are unchanged. The single bullet "Every outbound correlation ID is prefixed `TEST-<environment>-`" (ADR-0006 §Goals, amended in Notes to `TEST-`) is **moved out of the library** by this ADR; it becomes a property of `TestNamespaceCorrelator`, which a captive in-house fixture instantiates. ADR-0006's allowlist / environment guard remains intact.

---

## Implementation

### New `Correlator` surface

```python
# packages/core/src/core/correlation.py
from typing import Protocol, Callable, Any

class Correlator(Protocol):
    def generate(self) -> str: ...
    def stamp(self, payload: Any, correlation_id: str) -> Any: ...
    def extract(self, payload: Any) -> str | None: ...

class NoCorrelator:
    def generate(self) -> str: return ""
    def stamp(self, payload, correlation_id): return payload
    def extract(self, payload): return None

class DictFieldCorrelator:
    def __init__(
        self,
        field: str = "correlation_id",
        prefix: str = "",
        id_generator: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None: ...

class TestNamespaceCorrelator(DictFieldCorrelator):
    """Test-namespace ingress-filter posture: TEST- prefix + correlation_id field.
    Downstream SUTs filter on `correlation_id.startswith("TEST-")` at ingress.
    See ADR-0006 for why this prefix exists."""
    def __init__(self) -> None:
        super().__init__(field="correlation_id", prefix="TEST-")
```

### Harness wiring

```python
# packages/core/src/core/harness.py
class Harness:
    def __init__(
        self,
        transport: Transport,
        codec: Codec = JSONCodec(),
        correlator: Correlator = NoCorrelator(),
    ) -> None: ...
```

### Scenario changes

- `_ScenarioScope.__init__` calls `harness._correlator.generate()` instead of the hardcoded `generate_correlation_id()`. With `NoCorrelator`, scope correlation is the empty string and filters skip the extract-compare altogether.
- `Scenario.publish()` at [scenario.py:696](../../packages/core/src/core/scenario.py#L696) replaces the `setdefault` with `payload = self._harness._correlator.stamp(payload, self._context.correlation_id)`. The hardcoded `CorrelationIdNotInTestNamespaceError` guard is removed from here; `TestNamespaceCorrelator.stamp` raises it when active.
- Reply emit at [scenario.py:1048](../../packages/core/src/core/scenario.py#L1048) uses the same `stamp()` call. The `correlation_overridden` flag ([ADR-0018](0018-reply-correlation-scoping.md) §Security Considerations) is computed by comparing `correlator.extract(outgoing)` to the scope correlation after stamping.
- Inbound filters at [scenario.py:788-790](../../packages/core/src/core/scenario.py#L788-L790) and [scenario.py:945-973](../../packages/core/src/core/scenario.py#L945-L973) call `correlator.extract(decoded)`. If it returns `None`, the filter is skipped (broadcast). If it returns a value, it is compared to the scope correlation.
- `Scenario.stamped(payload)` delegates to `self._harness._correlator.stamp(payload, self._context.correlation_id)` — identity function under `NoCorrelator`, so call-site code is policy-agnostic.
- `Scenario.correlation_id` property exposes the scope's generated ID for callers who want to stamp into their own payload shape.

### Dispatcher changes

[core/dispatcher.py:72-137](../../packages/core/src/core/dispatcher.py#L72-L137) currently extracts via a per-topic registered extractor. That path remains, but registration is now done via the `Correlator` during scope entry. With `NoCorrelator`, the dispatcher registers no extractor and routes every inbound message to every live scope on the matching topic. The existing "surprise log" path (DEBUG line for unmatched correlations) remains; under `NoCorrelator` it is quiescent.

### Migration Path

Three phases.

**Phase 1 — land the Correlator contract.** Ship the `Correlator` protocol, `NoCorrelator`, `DictFieldCorrelator`, `TestNamespaceCorrelator`. Keep the current behaviour by setting the Harness default to `TestNamespaceCorrelator()` temporarily. Existing tests see no behavioural change. The only code motion is the `setdefault` → `correlator.stamp` replacement.

**Phase 2 — flip the default.** Change the Harness default to `NoCorrelator()`. In-house consumer conftests opt back into `TestNamespaceCorrelator()` explicitly. Update every test that was relying on the prefix-enforcement to either opt in or update its assertion. Document in the README that public users must pick a correlator if they want parallel-scope isolation.

**Phase 3 — remove legacy shims.** Delete the `_PREFIX` constant, the standalone `generate_correlation_id()`, and the `CorrelationIdNotInTestNamespaceError` gate from `scenario.py` (it lives in `TestNamespaceCorrelator` now).

Between Phase 1 and Phase 2 the library ships the capability without changing behaviour. Phase 2 is the public-readiness gate.

### Timeline

- **Phase 1:** before the public release.
- **Phase 2:** same release as the public announcement. The default flip is the public-readiness line.
- **Phase 3:** one release after Phase 2, once any in-repo consumer suite has stabilised on the explicit correlator.

---

## Validation

### Success Metrics

- **Default is transparent.** Unit test: construct `Harness(MockTransport(...))` with no correlator, publish `{"a": 1}`, assert the transport's recorded publish payload equals `{"a": 1}` exactly — no `correlation_id` key. Target: assertion passes.
- **Test-namespace behaviour reproducible.** Unit test: construct `Harness(MockTransport(...), correlator=TestNamespaceCorrelator())`, publish `{"a": 1}`, assert the recorded payload has `correlation_id` starting with `"TEST-"` and the rest of the dict unchanged. Target: assertion passes.
- **Parallel-isolation test still green.** Existing 100-scope isolation test updated to pass `TestNamespaceCorrelator()` in its fixture. Target: zero cross-scope fires across 100 runs, same target as [ADR-0002](0002-scoped-registry-test-isolation.md).
- **Broadcast-fallback visibility.** Unit test: with `NoCorrelator`, two scopes publishing on the same topic; both scopes' expects match both publishes. Target: behaviour documented, not flagged as a bug.
- **Public "hello world" test.** Integration test that follows the README's minimal example exactly; no correlator, one scope, one publish, one expect. Target: passes with zero config beyond the transport and codec.

### Monitoring

- CI gate on the two unit tests above.
- Docs CI gate that greps the README and `docs/context.md` for the phrase `correlation_id` outside the pluggable-policy section; unexpected matches fail the doc check.
- Release note template for Phase 2 includes a "breaking change" section naming the default flip.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — parallel isolation via correlation-ID routing. This ADR changes the injection posture to opt-in; the scope / dispatcher mechanics in 0002 are unchanged. 0002's open blocker on correlation-echo verification is unaffected — it becomes a consumer concern when a consumer opts into a correlator.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — dispatcher correlation mediator. This ADR routes the dispatcher's extractor through the `Correlator`; the mediator pattern is preserved.
- [ADR-0006](0006-environment-boundary-enforcement.md) — environment-boundary enforcement. This ADR **supersedes** the single bullet in 0006's Goals that mandates a `TEST-` correlation prefix. All other guarantees in 0006 (allowlist guard, default-deny, no-production-endpoint, schema-validated config) are unchanged. The prefix becomes a property of `TestNamespaceCorrelator`; captive in-house fixtures configure it and ADR-0006's downstream-ingress-filter story continues to hold for those deployments.
- [ADR-0018](0018-reply-correlation-scoping.md) — reply correlation scoping. This ADR reroutes 0018's `setdefault` call through the `Correlator`. The behaviour when `TestNamespaceCorrelator` is configured is identical to 0018's decision; with `NoCorrelator`, replies do not stamp and inbound filters broadcast. 0018 remains **Proposed**; promoting to Accepted requires this ADR to land first.
- Future ADR — SUT-originated correlation chains, currently deferred in [ADR-0018](0018-reply-correlation-scoping.md) §Notes. The `Correlator` abstraction is the natural home for that future opt-in.

---

## References

- [framework-design.md §10](../framework-design.md) — dispatcher discussion, to be updated to reference the `Correlator`.
- [context.md §6](../context.md) — parallel execution strategy, to be updated to describe the opt-in posture.
- Current injection points: [scenario.py:696](../../packages/core/src/core/scenario.py#L696), [scenario.py:1048](../../packages/core/src/core/scenario.py#L1048), [scenario.py:1240](../../packages/core/src/core/scenario.py#L1240).
- Current extraction points: [scenario.py:788-790](../../packages/core/src/core/scenario.py#L788-L790), [scenario.py:945-973](../../packages/core/src/core/scenario.py#L945-L973), [dispatcher.py:72-137](../../packages/core/src/core/dispatcher.py#L72-L137).
- Current generator: [correlation.py:10-17](../../packages/core/src/core/correlation.py#L10-L17).

---

## Notes

- **Deferred — header-level correlation.** A `HeaderCorrelator` that stamps into a transport-level header rather than a payload field is a natural next step. It requires the transport Protocol to grow a headers surface, which is a separate decision. **Owner:** Platform.
- **Deferred — negative-assertion gating.** `expect_none` / `expect_no_match` semantics are unsound under `NoCorrelator` because a sibling scope's publish can satisfy a broadcast route. When those matchers land, they must gate on `correlator is not NoCorrelator`. **Owner:** Framework.
- **Open — in-house fixture migration.** Any in-repo consumer conftest that relies on the old default must be updated to construct `Harness(..., correlator=TestNamespaceCorrelator())` before Phase 2 ships. The change is one line per fixture; coordination is scheduling, not design. **Owner:** Consumer teams.
- **Open — README correlation-policy doc.** The public README must include a short section on correlation policies and when to pick which. Draft depends on Phase 1 landing. **Owner:** Platform / Docs.
- **Supersession note.** This ADR modifies the correlation-prefix portion of [ADR-0006](0006-environment-boundary-enforcement.md), which is Accepted. Per [ADR README §Conventions](README.md), Accepted-ADR content changes require a superseding ADR; this ADR is that superseder for the prefix bullet only. The allowlist / environment guard portion of ADR-0006 is not superseded and remains Accepted as written.

**Last Updated:** 2026-04-18
