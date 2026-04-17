# 0019. Pluggable Correlation Policy with No-Op Default

**Status:** Proposed
**Date:** 2026-04-18
**Deciders:** Platform / Test Infrastructure
**Technical Story:** The library currently mutates caller payloads by injecting a `correlation_id` field with a `TEST-` prefix. For a public, general-purpose library this is too opinionated: a third-party consumer cannot be assumed to want the mutation, cannot be assumed to name the field that way, and cannot be assumed to need parallel-scope routing at all. Correlation behaviour needs to be pluggable, with a transparent default, so consumers opt into the posture that fits their schema.

---

## Context

Choreo's parallel-test-isolation story is built on correlation-ID routing. Three call sites in [packages/core/src/choreo/scenario.py](../../packages/core/src/choreo/scenario.py) mutate payloads today:

- `Scenario.publish()` at [scenario.py:696](../../packages/core/src/choreo/scenario.py#L696) — `payload.setdefault("correlation_id", scope_corr)`.
- Reply emit path at [scenario.py:1048](../../packages/core/src/choreo/scenario.py#L1048) — same setdefault on the reply's reply payload.
- Scope construction at [scenario.py:1240](../../packages/core/src/choreo/scenario.py#L1240) — generates `TEST-<32 hex>` via [correlation.py:10-17](../../packages/core/src/choreo/correlation.py#L10-L17).

Inbound filtering at [scenario.py:788-790](../../packages/core/src/choreo/scenario.py#L788-L790) (expect) and [scenario.py:945-973](../../packages/core/src/choreo/scenario.py#L945-L973) (reply) silently drops messages whose `correlation_id` does not match the scope. [core/dispatcher.py:72-137](../../packages/core/src/choreo/dispatcher.py#L72-L137) routes on the same field for cross-scope isolation.

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

How does the library offer parallel-scope correlation routing as an opt-in capability for consumers who need it, without mutating payloads by default, hardcoding a field name, or foreclosing header-based correlation that open-source consumers will also reasonably expect?

### Goals

- **Transparent default.** With no explicit configuration, `s.publish(topic, payload)` sends `payload` unchanged. No library-injected fields. No hidden prefix. No filter on receive.
- **Opt-in parallel routing.** Consumers who want per-scope correlation isolation configure a `CorrelationPolicy` once at `Harness` construction; behaviour today is reproducible by selecting the shipped `DictFieldPolicy`.
- **Library does not embed deployment-specific choices.** The `TEST-` prefix, the `"correlation_id"` field name, and the ingress-filter story all move from library core into a named factory that consumers opt into.
- **Does not foreclose headers.** The abstraction works for both payload-field and transport-header correlation without a breaking protocol change.
- **Preserve call-site control.** A test that wants to stamp its own correlation on a specific publish can do so without fighting the framework.
- **Single code path for expect, reply, and dispatch.** One `CorrelationPolicy` instance drives injection and extraction everywhere; there is no "reply does it differently to expect".

### Non-Goals

- **Replacing the transport Protocol.** The envelope type introduced here sits between Scenario and the policy; transports continue to deal in bytes.
- **Automatic SUT-echo verification.** This ADR does not resolve [ADR-0002](0002-scoped-registry-test-isolation.md)'s open blocker on correlation echo. It changes who owns the assumption — from library to consumer.
- **Per-scope policy overrides.** One `CorrelationPolicy` per `Harness`; swapping mid-session is not supported.
- **Backwards compatibility with the 2026-04-17 default.** The default is changing; consumers who relied on implicit injection need to opt in. See Migration Path.

---

## Decision Drivers

- **Open-source trust posture.** A public user who reads `s.publish("orders.new", order)` should be able to predict what arrives on the wire without reading the library's source.
- **Field-name and transport-neutrality.** Real-world message schemas do not agree on a correlation field name, and some do not use a payload field at all. Hardcoding either makes the library unusable for a large fraction of potential users.
- **Observability of assumptions.** "We assume the SUT echoes correlation on every hop" is load-bearing but currently invisible. Making the policy explicit surfaces the assumption at the construction site.
- **Reversibility of abstraction shape.** Getting the policy surface wrong means a breaking change for every consumer. The shape must accommodate headers, async ID generation, and custom extraction without a v2.
- **Separation of transport, codec, and correlation.** Today the transport sends bytes, the codec owns encode/decode, and the library owns correlation injection. Correlation injection is the odd one out — it mutates payload shape without the codec's involvement.

---

## Considered Options

### Option 1: Pluggable `CorrelationPolicy` protocol over an envelope, with no-op default (chosen)

**Description:** The `Harness` accepts an optional `correlation: CorrelationPolicy` parameter. Default is `NoCorrelationPolicy()` — no id, no write, no read, no filter. The policy operates on a small `Envelope` dataclass (`topic`, `payload`, `headers`) so header-based and payload-field correlation share one shape. Methods: `new_id()` (async, returns `str | None`), `write(envelope, correlation_id) -> Envelope` (returns a new envelope; never mutates input), `read(envelope) -> str | None`, plus a `routes_by_correlation: bool` capability flag used to gate negative-assertion matchers. When `read` returns `None`, inbound routing falls back to broadcast. Call-site control is via `Scenario.correlation_id: str | None` (callers write whatever shape their schema needs).

**Pros:**
- Default is transparent — a user reading `s.publish(topic, payload)` sees exactly what goes on the wire.
- Envelope shape accommodates payload-field and header-based correlation without protocol change; a future `HeaderCorrelationPolicy` implements the same protocol.
- Current library behaviour is reproducible by calling the shipped `test_namespace()` factory in the consumer's fixture; no library-core regression for existing captive users.
- One contract for expect, reply, dispatch — no "same mechanism in three places" rewrites.
- `routes_by_correlation` capability flag generalises the negative-matcher gate to any policy (not tied to identity check against `NoCorrelationPolicy`).
- `new_id` is async, so policies that fetch IDs from a sidecar or external service don't require a breaking protocol change later.

**Cons:**
- Introduces a new `Envelope` type internal to the Scenario-policy boundary. Not wire-visible and not part of the transport Protocol, but callers of `s.stamped_envelope(...)` (a helper) see it. Mitigated by keeping the envelope internal and exposing `Scenario.correlation_id` as the primary call-site affordance.
- Changes the default for existing test suites. Tests that relied on implicit injection stop routing by correlation until their fixture is updated. Mitigated by shipping `test_namespace()` as a single factory call and publishing a loud deprecation warning during the migration window.
- Parallel-scope tests without a policy all broadcast; scopes could see sibling messages on the same topic. This is the intended semantics — no policy means no routing — but must be documented prominently because the failure mode is subtle (extra matches, not missing ones).

### Option 2: Keep implicit injection; parameterise the field name only

**Description:** Retain `setdefault` as the default; introduce a policy object only so consumers can change the field name.

**Pros:**
- Minimal migration for the captive suite.

**Cons:**
- Does not fix the trust posture problem. A public consumer still gets silent payload mutation; they just pick which key name it happens under.
- `TEST-` prefix still in library core; open-source users inherit an ingress-filter convention they have no use for.
- Does not accommodate header correlation; same breaking change deferred to a later ADR.

### Option 3: Per-call opt-in only (no Harness-level policy)

**Description:** Remove all implicit injection. The only way to stamp is per call: `s.publish(topic, payload, correlation_id=...)`. No policy object.

**Pros:**
- Maximally transparent.
- No new abstraction in library core.

**Cons:**
- Dispatcher inbound routing needs an extractor. Without a policy-like object, there is nowhere to look up how correlation is embedded in this payload shape.
- Every `publish()` in consumer code grows a parameter; ergonomics collapse under fan-out.

### Option 4: Remove correlation from the library entirely

**Description:** The library does not know about correlation. Consumers who need parallel isolation build their own layer.

**Pros:**
- Smallest library surface.

**Cons:**
- Sacrifices the parallel-isolation guarantee that [ADR-0002](0002-scoped-registry-test-isolation.md) exists to provide. The framework without correlation routing is a different product.
- Every consumer re-derives the dispatcher's correlation-to-scope lookup. Duplication and bugs.

---

## Decision

**Chosen Option:** Option 1 — pluggable `CorrelationPolicy` protocol over an envelope, with `NoCorrelationPolicy` default. The captive deployment ships `test_namespace()` in its own fixture, not in library core.

### Rationale

- Option 1 is the only option that simultaneously solves the public-trust posture and preserves the parallel-isolation guarantee for consumers who want it.
- The envelope shape is the difference between Option 1 and Option 2's smaller variant: it means the protocol does not bind the library to payload-field correlation for all future time. A `HeaderCorrelationPolicy` lands as a new implementation, not a new protocol.
- Option 3's ergonomics collapse under fan-out; Option 4 overshoots (parallel isolation is the product).
- `NoCorrelationPolicy` as default makes the broadcast fallback the library's default inbound behaviour. This aligns default with honest capability — no policy means no isolation — instead of shipping implicit isolation behind the user's back.

**Reading:** the library ships a correlation abstraction; the default behaviour is no correlation; consumers who need parallel-scope isolation name their policy, and the captive deployment is one such consumer.

---

## Consequences

### Positive

- A public user's `s.publish(topic, payload)` produces exactly `payload` on the wire. The library does not mutate caller data by default.
- Consumers with schemas that forbid unknown fields (protobuf strict, tag-value-protocol, Avro) can use the library without wrestling the injector.
- `TEST-` prefix and `"correlation_id"` field name leave library core and live as a `test_namespace()` factory in the captive consumer's fixture. Other consumers pick their own profile.
- Inbound routing degrades honestly: with `NoCorrelationPolicy`, broadcast; with a real policy, per-scope routing. No middle state where routing silently breaks.
- Reply stamping is identical to expect filtering — one policy instance drives both.
- A future `HeaderCorrelationPolicy` lands without a protocol change.

### Negative

- The captive suite must update fixtures to call `test_namespace()` or lose parallel isolation. Migration is one line per `Harness()` construction; blast radius is the shared conftest.
- Public consumers who need parallel isolation must read a policy section before they get it. The previous behaviour gave it implicitly; the new behaviour requires one decision.
- **Defence-in-depth against the captive threat model weakens.** [ADR-0006](0006-environment-boundary-enforcement.md)'s composed guard was allowlist + `TEST-` prefix. Removing the prefix from the library default means a captive fixture that forgets the policy publishes unprefixed traffic, which the downstream ingress filter would not catch. Mitigated by a runtime WARNING when `NoCorrelationPolicy` is paired with a non-`MockTransport` (see §Implementation) and a captive-side conftest assertion (see §Migration Path). The residual risk is real and named here — it is not "unchanged".
- Broadcast default with multiple scopes on the same topic can produce surprising match counts if users are not thinking about correlation. Mitigated by the `routes_by_correlation` capability flag gating negative-assertion matchers, a prominent doc warning, and a dispatcher DEBUG line when broadcast fan-out exceeds one matching scope.

### Neutral

- The policy lives in `choreo.correlation`; the public module re-exports `CorrelationPolicy`, `NoCorrelationPolicy`, `DictFieldPolicy`, `Envelope`, and the `test_namespace()` factory. The legacy `choreo.correlation._PREFIX` constant is removed.
- The prefix-enforcement check currently named `CorrelationIdNotInTestNamespaceError` moves inside `DictFieldPolicy.write()` and is renamed to `CorrelationIdNotInNamespaceError`. Enforcement happens only when the consumer opts into a prefixed policy (e.g. `test_namespace()`).
- `Scenario.correlation_id: str | None` exposes the scope's id so authors can stamp manually into any shape. Under `NoCorrelationPolicy` this is `None`; attempts to stamp it into a schema that rejects missing values surface as the consumer's type error, not a silent `""` value.

### Security Considerations

**Supersession scope.** This ADR supersedes the single `TEST-<environment>-` prefix bullet in [ADR-0006](0006-environment-boundary-enforcement.md) §Goals (as amended in that ADR's Notes to `TEST-`). The allowlist guard, default-deny posture, schema-validated config, and no-production-endpoint rules in ADR-0006 are **unchanged**. The ADR-0006 §Security Considerations item 4 ("Correlation ID prefixing") is superseded; that guarantee is now a property of the `test_namespace()` factory, not of library core. ADR-0006 must be re-accepted by Platform + Security alongside ADR-0019 merging; a documentation-only amendment updates §Security Considerations item 4 to reference this ADR. Until that re-acceptance, ADR-0006 remains Accepted-with-pending-supersession.

- **Defence-in-depth regression against the captive threat model.** Named in §Consequences §Negative. Residual risk is that a captive fixture without `test_namespace()` publishes unprefixed traffic. Mitigations: (a) `Harness.connect()` emits a structured WARNING (`correlator_noop_against_real_transport`) when `NoCorrelationPolicy` is paired with any transport other than `MockTransport`; (b) captive conftests assert `harness._correlation.routes_by_correlation` at fixture entry; (c) the `test_namespace()` docstring states that this factory implements ADR-0006's prefix contract and must be used for any captive deployment that relies on the downstream ingress filter.

- **Broadcast confidentiality on shared infrastructure.** Under `NoCorrelationPolicy`, every inbound message on a subscribed topic is delivered to every live scope in the process. If the library is used against shared infrastructure (a broker shared across tenants, CI runs, developers' machines pointing at one cluster), payloads fan out across scopes. `NoCorrelationPolicy` is only safe on **dedicated or per-run** infrastructure. Consumers using shared infrastructure **must** configure a policy whose `read()` distinguishes their scope's traffic (e.g. `DictFieldPolicy` with a unique `prefix`). The README carries this warning prominently; the `NoCorrelationPolicy` class docstring names it.

- **Consumer-supplied policy is a new trust boundary.** `CorrelationPolicy` instances are consumer code executed inside the Harness async path on every publish and every inbound message. The library defends itself by:
  - Wrapping every policy call; uncaught exceptions from `new_id()`, `write()`, or `read()` surface as named scenario failures (`CorrelationPolicyError`) that fail the scenario with the policy class name in the diagnostic, rather than poisoning the event loop.
  - Treating `read()` return values as opaque strings; the dispatcher lookup uses them as dictionary keys with no further parsing. The library does not limit length, but `CorrelationPolicyError` wraps any exception raised during dispatcher registration.
  - Documenting that `new_id()` output should be collision-resistant; shipped profiles default to `secrets.token_hex(16)`. Dispatcher's O(1) guarantee ([ADR-0004](0004-dispatcher-correlation-mediator.md)) degrades with collision-prone generators; the library does not attempt to detect collisions at runtime.
  - Recording policy class name in the structured startup log so audit can identify what policy code was in effect for a given run.

- **Log data classification under consumer-supplied IDs.** The pre-ADR-0019 library assumed correlation IDs were library-generated opaque tokens (`TEST-<hex>`), safe to log in surprise-log paths. Under this ADR the id is consumer-chosen: a misconfigured `DictFieldPolicy(id_generator=lambda: customer_order_ref())` would place regulated identifiers into that surprise-log field. Consumers must treat the policy's `new_id()` output as opaque-but-potentially-sensitive and register a classifier that redacts it unless they have independently audited their generator. The library does not introspect the id string.

- **Cross-scope disclosure under shared field names.** Two consumers using `DictFieldPolicy(field="trace_id")` without distinguishing prefixes share a namespace on shared infrastructure — a message stamped `{"trace_id": "abc"}` in process A matches a scope with id `"abc"` in process B. `DictFieldPolicy` without a distinguishing prefix is unsafe on shared infrastructure; the class docstring names this, and the `test_namespace()` factory takes `prefix` as a required parameter so the captive deployment cannot accidentally miss it.

- **Reply correlation override is noisy, not silent.** Unchanged from [ADR-0018](0018-reply-correlation-scoping.md) §Security Considerations: a builder that returns a correlation id different from the scope's still triggers a WARNING and sets `ReplyReport.correlation_overridden`. The check now runs through `read()` rather than a hardcoded dict lookup.

- **Missing-correlation disclosure.** A message whose `read()` returns `None` is broadcast to every live scope; this is consistent with the stated design, but the prior library (pre-ADR-0019) silently ignored such messages. Consumers migrating from the old default must know this: a test that previously passed because "unknown correlation meant ignored" will now see extra matches under `NoCorrelationPolicy`.

---

## Implementation

### New `CorrelationPolicy` surface

```python
# packages/core/src/choreo/correlation.py
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable
import secrets


@dataclass(frozen=True)
class Envelope:
    """Internal shape seen by a CorrelationPolicy. Not wire-visible.

    The transport Protocol still deals in bytes. The policy sits between
    Scenario and the codec/transport, working with a topic + payload + headers
    triple so header-based policies have somewhere to put the id.
    """
    topic: str
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class CorrelationPolicy(Protocol):
    """Strategy for embedding and extracting a correlation id.

    Implementations must:
      * be side-effect-free on input (return a new Envelope; do not mutate)
      * treat `new_id()` as possibly-async (implement with `async def`)
      * return None from `read()` when no id is present (triggers broadcast)

    See ADR-0019 Security Considerations for the trust-boundary contract.
    """

    async def new_id(self) -> str | None: ...
    def write(self, envelope: Envelope, correlation_id: str) -> Envelope: ...
    def read(self, envelope: Envelope) -> str | None: ...

    @property
    def routes_by_correlation(self) -> bool:
        """True if this policy produces per-scope isolation on inbound routing.

        Negative-assertion matchers (expect_none and similar) gate on this
        flag; matchers that depend on 'no message of shape X arrived' are
        unsound under a broadcast policy.
        """
        ...


class NoCorrelationPolicy:
    async def new_id(self) -> None:
        return None

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        return envelope

    def read(self, envelope: Envelope) -> None:
        return None

    @property
    def routes_by_correlation(self) -> bool:
        return False


class DictFieldPolicy:
    """Stamps / reads a string field on dict payloads.

    WARNING: without a distinguishing `prefix`, two instances using the same
    `field` on shared broker infrastructure share a correlation namespace.
    Use a prefix (see `test_namespace` for an example) on any non-dedicated
    deployment. See ADR-0019 Security Considerations.
    """

    def __init__(
        self,
        field: str = "correlation_id",
        prefix: str = "",
        id_generator: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._field = field
        self._prefix = prefix
        self._id_generator = id_generator

    async def new_id(self) -> str:
        return f"{self._prefix}{self._id_generator()}"

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        if not isinstance(envelope.payload, dict):
            return envelope
        if self._prefix and not correlation_id.startswith(self._prefix):
            raise CorrelationIdNotInNamespaceError(
                f"correlation id {correlation_id!r} does not start with {self._prefix!r}"
            )
        payload = {**envelope.payload, self._field: correlation_id}
        return Envelope(topic=envelope.topic, payload=payload, headers=envelope.headers)

    def read(self, envelope: Envelope) -> str | None:
        if not isinstance(envelope.payload, dict):
            return None
        value = envelope.payload.get(self._field)
        return value if isinstance(value, str) else None

    @property
    def routes_by_correlation(self) -> bool:
        return True


def test_namespace(field: str = "correlation_id") -> DictFieldPolicy:
    """Factory for the captive-deployment posture: `TEST-` prefix + dict field.

    Equivalent to the pre-ADR-0019 library default. Implements the
    downstream ingress-filter contract described in ADR-0006; consumers
    relying on that filter must call this factory rather than
    DictFieldPolicy directly.
    """
    return DictFieldPolicy(field=field, prefix="TEST-")


class CorrelationPolicyError(RuntimeError):
    """Raised when a consumer-supplied policy raises during new_id/write/read.

    Wraps the original exception and records the policy class name.
    """


class CorrelationIdNotInNamespaceError(ValueError):
    """Raised by DictFieldPolicy when an explicit correlation id is stamped
    that does not match the policy's configured prefix."""
```

### Harness wiring

```python
class Harness:
    def __init__(
        self,
        transport: Transport,
        codec: Codec = JSONCodec(),
        correlation: CorrelationPolicy = NoCorrelationPolicy(),
    ) -> None:
        """...

        correlation: controls whether and how correlation ids are attached to
            outbound messages and read from inbound ones. Default is
            NoCorrelationPolicy (transparent passthrough; all scopes receive
            all matching inbound messages). Pass a DictFieldPolicy or
            test_namespace() for per-scope isolation. See ADR-0019.
        """
        ...
```

`Harness.connect()` emits the `correlator_noop_against_real_transport` WARNING when `isinstance(self._correlation, NoCorrelationPolicy)` and `not isinstance(self._transport, MockTransport)`. The WARNING is structured and carries the policy and transport class names; consumers classify it with their own log-redaction rules.

### Scenario changes

- `_ScenarioScope.__init__` awaits `harness._correlation.new_id()`. Result is `Optional[str]`.
- `Scenario.correlation_id` returns that optional string. Typed `Optional[str]`.
- `Scenario.publish()` builds an `Envelope(topic, payload)`, calls `write(envelope, self._context.correlation_id)` if the scope has an id, and passes the envelope's payload to the codec. The hardcoded `CorrelationIdNotInTestNamespaceError` guard at [scenario.py:697-699](../../packages/core/src/choreo/scenario.py#L697-L699) is removed; `DictFieldPolicy.write` raises the renamed `CorrelationIdNotInNamespaceError` when a user explicitly stamps a mismatched id.
- Reply emit ([scenario.py:1048](../../packages/core/src/choreo/scenario.py#L1048)) uses the same `write()` call. The `correlation_overridden` flag is computed via `read()` after `write()` and compared to the scope id.
- Inbound filters at [scenario.py:788-790](../../packages/core/src/choreo/scenario.py#L788-L790) and [scenario.py:945-973](../../packages/core/src/choreo/scenario.py#L945-L973) call `read()` on an `Envelope(topic, decoded_payload)`. If `None`, broadcast; else, compare to scope id.
- Negative-assertion matchers (when added; see Notes) check `harness._correlation.routes_by_correlation` at registration time and raise a clear error if False.
- `Scenario.stamped(payload)` is **removed**. It was payload-shaped and incompatible with header policies. Authors wanting call-site stamping use `Scenario.correlation_id` and stamp into whatever shape their schema needs.

### Dispatcher changes

[core/dispatcher.py:72-137](../../packages/core/src/choreo/dispatcher.py#L72-L137) currently holds per-topic extractors. Behaviour after this ADR:

- The `CorrelationPolicy` is the base: on scope entry, the dispatcher binds the policy's `read()` as the extractor for scope-owned topics.
- Per-topic extractors from [ADR-0004](0004-dispatcher-correlation-mediator.md) remain available; they override the policy for specific topics when the consumer registers one.
- A conflict — registering both a per-topic extractor and a policy-implied extractor for the same topic — raises `DispatcherExtractorConflict` at registration time. No silent winner.
- Under `NoCorrelationPolicy` with no per-topic extractors, the dispatcher broadcasts inbound messages to every live scope subscribed to the topic. The surprise-log DEBUG path remains; under broadcast it is quiescent.

### Policy exception handling

Every `new_id()`, `write()`, `read()` call in Scenario and Dispatcher is wrapped:

```python
try:
    result = self._correlation.write(envelope, corr_id)
except Exception as exc:
    raise CorrelationPolicyError(
        f"{type(self._correlation).__name__}.write raised"
    ) from exc
```

The raised error fails the current scenario with a named diagnostic instead of propagating into the event loop. Unit tests assert this for each method.

### Migration Path

**Phase 1 — protocol lands, default preserved via opt-in internal flag.** Ship `CorrelationPolicy`, `Envelope`, `NoCorrelationPolicy`, `DictFieldPolicy`, `test_namespace()`. The Harness default remains the current pre-ADR-0019 behaviour, guarded by an **internal, undocumented** construction path used only by the captive test suite. The public construction path defaults to `NoCorrelationPolicy`. Phase 1 is **not published to PyPI**; it lives on an internal branch until Phase 2 gates are met.

**Phase 2 — public release.** The internal compatibility path is removed. Harness default in all construction paths is `NoCorrelationPolicy`. The captive conftest is updated to pass `test_namespace()` and to assert `routes_by_correlation` at fixture entry. The captive suite's reference runs must stabilise on the explicit policy before Phase 2 can ship. This is the first PyPI release.

**Phase 3 — cleanup.** One release after Phase 2: remove the legacy `_PREFIX` constant, the standalone `generate_correlation_id()`, and any remaining `CorrelationIdNotInTestNamespaceError` references. Delete the no-longer-needed compatibility shims. **Landed 2026-04-18:** the internal correlation module was deleted in Phase 1, and the `CorrelationIdNotInTestNamespaceError` alias was removed from `choreo/__init__.py` and `choreo/scenario.py` in this phase. Consumers catching the old name must switch to `CorrelationIdNotInNamespaceError`.

**Deprecation warnings.** Between Phase 1 and Phase 2, any construction that triggers the internal compatibility path emits a `DeprecationWarning` naming the ADR. Consumers pinning pre-release versions see the migration notice.

**Semver.** Pre-1.0: Phase 2 is a minor version bump (0.X → 0.X+1); release notes call out the breaking behavioural change. Post-1.0: Phase 2 would be a major version bump. The public announcement and README explicitly call this a **breaking change** — scopes that were isolated stop being isolated until the consumer configures a policy.

### Timeline

- **Phase 1:** internal only, pre-public. *Landed 2026-04-18.*
- **Phase 2:** first public release (default flipped to `NoCorrelationPolicy`). *Landed 2026-04-18.*
- **Phase 3:** one release after Phase 2 — compatibility shims removed. *Landed 2026-04-18.*

---

## Validation

### Success Metrics

- **Default is transparent.** Unit test: `Harness(MockTransport(...))` with no policy, publish `{"a": 1}`, assert the transport's recorded publish payload equals `{"a": 1}` exactly — no `correlation_id` key, no mutation. Target: assertion passes.
- **Captive behaviour reproducible via factory.** Unit test: `Harness(MockTransport(...), correlation=test_namespace())`, publish `{"a": 1}`, assert recorded payload has `correlation_id` starting with `"TEST-"` and the rest of the dict unchanged. Target: assertion passes.
- **Parallel isolation at 100 scopes.** Same test as [ADR-0002](0002-scoped-registry-test-isolation.md); fixture updated to pass `test_namespace()`. Target: zero cross-scope fires across 100 runs.
- **Broadcast fallback is visible.** Unit test: with `NoCorrelationPolicy`, two scopes publishing on the same topic; both scopes' expects match both publishes. Target: behaviour asserted as documented; dispatcher DEBUG log contains the fan-out count.
- **Non-MockTransport + NoCorrelationPolicy emits WARNING.** Unit test: construct `Harness(NatsTransport(...))` with no policy (stub NATS), call `connect()`, assert a structured WARNING with event name `correlator_noop_against_real_transport` is emitted and carries the transport class name. Target: assertion passes.
- **Policy exception becomes a named scenario failure.** Unit test: build a `CorrelationPolicy` whose `write()` raises `RuntimeError`; run a scenario; assert scenario fails with `CorrelationPolicyError` naming the policy class. Target: no event-loop crash; error message matches.
- **Header-policy test-double implements the protocol without modification.** Unit test: a minimal `HeaderCorrelationPolicy` stub that stamps into `envelope.headers` and reads from there. Asserted to satisfy the protocol and to interoperate with the Scenario publish/expect flow. Target: passes. Regression gate against re-introducing payload-field assumptions in the Scenario layer.
- **Dispatcher conflict raises.** Unit test: register both a per-topic extractor and a `DictFieldPolicy` covering the same topic; assert `DispatcherExtractorConflict` at registration. Target: raised.
- **`routes_by_correlation` gates negative matchers.** Unit test: with `NoCorrelationPolicy` active, registering a negative-assertion matcher raises a clear error naming the policy. Target: raised with policy class name.

### Monitoring

- CI gate on all Success Metrics above.
- Docs CI: grep the README and `docs/context.md` for the phrase `correlation_id` outside the pluggable-policy section; unexpected matches fail.
- Release notes template for Phase 2 includes a "breaking change" section naming the default flip.
- Structured WARNING / DEBUG log events from the Harness are captured in CI and inspected by the non-MockTransport and broadcast-fallback tests above.

---

## Related Decisions

- [ADR-0002](0002-scoped-registry-test-isolation.md) — parallel isolation via correlation-ID routing. This ADR changes the injection posture to opt-in; the scope / dispatcher mechanics are unchanged. ADR-0002's open blocker on correlation-echo verification is unaffected — it becomes a consumer concern when a consumer opts into a policy.
- [ADR-0004](0004-dispatcher-correlation-mediator.md) — dispatcher correlation mediator. This ADR routes the dispatcher's extractor through the `CorrelationPolicy`; the mediator pattern is preserved. Per-topic extractors remain; precedence is explicit (§Implementation).
- [ADR-0006](0006-environment-boundary-enforcement.md) — environment-boundary enforcement. This ADR **supersedes** the single bullet in §Goals that mandates a `TEST-` correlation prefix, and the paired §Security Considerations item 4. All other guarantees in ADR-0006 are unchanged. The amendment has landed in ADR-0006's Notes as the 2026-04-18 correction.
- [ADR-0018](0018-reply-correlation-scoping.md) — reply correlation scoping. This ADR reroutes 0018's stamping through the policy. Behaviour under `test_namespace()` is identical to 0018's decision; under `NoCorrelationPolicy`, replies do not stamp and inbound filters broadcast. ADR-0018 remains **Proposed**; promoting to Accepted requires this ADR to land first.
- Future ADR — `HeaderCorrelationPolicy`. The abstraction leaves room; a separate ADR documents the header-transport surface when the transport Protocol gains headers. **Owner:** Platform.

---

## References

- [framework-design.md §10](../framework-design.md) — dispatcher discussion, to be updated to reference the `CorrelationPolicy`.
- [context.md §6](../context.md) — parallel execution strategy, to be updated to describe the opt-in posture.
- Current injection points: [scenario.py:696](../../packages/core/src/choreo/scenario.py#L696), [scenario.py:1048](../../packages/core/src/choreo/scenario.py#L1048), [scenario.py:1240](../../packages/core/src/choreo/scenario.py#L1240).
- Current extraction points: [scenario.py:788-790](../../packages/core/src/choreo/scenario.py#L788-L790), [scenario.py:945-973](../../packages/core/src/choreo/scenario.py#L945-L973), [dispatcher.py:72-137](../../packages/core/src/choreo/dispatcher.py#L72-L137).
- Current generator: [correlation.py:10-17](../../packages/core/src/choreo/correlation.py#L10-L17).
- Current namespace assertion: [scenario.py:45-66](../../packages/core/src/choreo/scenario.py#L45-L66), [scenario.py:697-699](../../packages/core/src/choreo/scenario.py#L697-L699).

---

## Notes

- **Deferred — `HeaderCorrelationPolicy`.** A policy that stamps into transport headers rather than the payload. Requires the transport Protocol to grow a headers surface; the `Envelope` type introduced here reserves the shape so the policy protocol does not change. **Owner:** Platform.
- **Deferred — negative-assertion matchers.** `expect_none` / `expect_no_match` semantics are unsound under a broadcast policy. When those matchers land, they gate on `policy.routes_by_correlation` at registration time and raise with a clear message. The gate is a capability check, not an identity check — a future custom `ZeroCorrelationPolicy` with `routes_by_correlation = False` is handled identically. **Owner:** Framework.
- **Done — captive fixture migration.** The captive test suite's `harness` fixtures in `test_replies.py` and `test_scenario.py` now construct `Harness(..., correlation=test_namespace())` and assert `routes_by_correlation` at fixture entry (Phase 2 landed 2026-04-18).
- **Done — README correlation-policy notice.** The top-level README names the `NoCorrelationPolicy` default, the opt-in `test_namespace()` factory, and the shared-infrastructure caveat. `docs/context.md` §6 and `docs/framework-design.md` §4 were updated in the same pass.
- **Supersession and re-acceptance.** This ADR modifies the correlation-prefix portion of [ADR-0006](0006-environment-boundary-enforcement.md), which is Accepted. ADR-0006's Notes carry the 2026-04-18 correction documenting the supersession. This ADR moves from Proposed to Accepted when Platform + Security sign off on the amendment.

**Last Updated:** 2026-04-18
