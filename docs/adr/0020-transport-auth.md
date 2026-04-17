# 0020. Transport Authentication — Typed Per-Transport Auth with Optional Resolver

**Status:** Proposed
**Date:** 2026-04-18
**Deciders:** Platform / Test Infrastructure
**Technical Story:** Follow-up to [ADR-0006](0006-environment-boundary-enforcement.md); supersedes (and re-scopes) the removed draft on Harness-level secret management.

---

## Context

Choreo's four real-network transports (`NatsTransport`, `KafkaTransport`,
`RabbitTransport`, `RedisTransport`) presently accept no explicit
authentication parameters. NATS and Kafka have no auth wiring at all: their
constructors pass `servers` / `bootstrap_servers` straight into the client
library with no credentials, no token, no TLS context. Rabbit and Redis
accept credentials only via the connection URL (`amqp://user:pw@host`,
`redis://:pw@host`); [transports/base.py:59-82](../../packages/core/src/choreo/transports/base.py#L59-L82) redacts that userinfo out
of connect-failure tracebacks, but the URL itself is the only auth surface
the transport exposes.

This is acceptable for a local Docker Compose broker on `127.0.0.1` with no
authentication configured. It is **not** acceptable for a library that will
be consumed by downstream repositories running against authenticated
brokers — which is every non-trivial deployment. A consumer who needs to
point `NatsTransport` at a NATS cluster requiring a token, NKey, or JWT
credentials file has no API to do so today.

Choreo is being released as OSS. The auth design must accommodate the full
spread of how consumers fetch credentials: plain environment variables,
`.env` files, HashiCorp Vault, AWS Secrets Manager, Azure Key Vault,
hardware security modules, dev laptops with no secrets at all. The
library must not bake in any one of those pathways; it must offer a
minimum-surface API that every pathway can drive.

### Background

- [ADR-0006](0006-environment-boundary-enforcement.md) establishes the
  allowlist as an **endpoint** guard (default-deny for hosts). It is
  explicitly silent on trust properties of the channel (TLS required?
  mTLS required?). This ADR keeps that separation: auth is not the
  allowlist's job.
- The deleted `ADR-0010 Secret Management in the Harness` (git history;
  removed when the `Environment` / `HarnessConfig` model was dropped) was
  framed around a `HarnessConfig.password` field that no longer exists.
  Its **principles** — bounded lifetime, no read-back accessor, redacted
  in every representation, no pickle, fetched-not-stored, auditable
  boundary — are still correct; this ADR rehomes them at the transport
  layer, which is now the correct surface per [ADR-0006 §Notes](0006-environment-boundary-enforcement.md).
- [CLAUDE.md §Runtime configuration](../../CLAUDE.md) already commits the
  library to a "transport owns its own config" posture. Auth is a first-
  class part of that config. [ADR-0006 §Notes — "Correction: library no longer owns the environment concept"](0006-environment-boundary-enforcement.md#notes) records the same posture from the allowlist angle.
- `safe_url()` and `_redact.py` ([_redact.py:31-42](../../packages/core/src/choreo/_redact.py#L31-L42)) already ship. The
  framework has a working pattern for structured redaction on log / report
  egress; auth surfaces slot into the same pattern rather than inventing
  a new one.

### Problem Statement

How does each built-in transport accept, use, and discard authentication
material such that (a) every auth mode the underlying client library
supports is reachable through a public API, (b) the material's lifetime
is bounded to the logon handshake, (c) no credential leaks into `repr`,
pickling, logs, or exception messages, and (d) the API is open enough for
an OSS consumer to wire an arbitrary secret store behind it without
modifying the library?

### Goals

- **Full coverage of client-library auth modes.** Every auth mode the
  underlying Python client supports for NATS, Kafka, RabbitMQ, and Redis
  is reachable through a transport's public constructor. Non-negotiable
  minimum: NATS user/password + token + NKey seed + credentials file +
  TLS; Kafka SASL PLAIN + SCRAM-SHA-256/512 + OAUTHBEARER + SSL; Rabbit
  PLAIN + EXTERNAL + AMQPS; Redis password + ACL user/pass + rediss.
- **Bounded credential lifetime (resolver path).** Credentials supplied
  via a resolver callable are fetched inside `connect()`, consumed by
  logon, and cleared from the transport's reachable state immediately
  after logon. The **literal path** (`auth=NatsAuth.token("...")`) is
  deliberately weaker — the secret enters process memory at construction
  time, not at `connect()` — and its lifetime is bounded only at
  `connect()`'s tail. See §Security Considerations item 1 for the
  literal-vs-resolver split.
- **No accidental egress.** `__repr__`, `__getstate__`, exception
  messages, and structured log events never contain cleartext credential
  values. `pickle.dumps(transport)` raises unconditionally (preserving
  [ADR-0001](0001-single-session-scoped-harness.md) §Security
  Considerations item 2).
- **One API shape across all transports.** Each transport gets an
  `auth=<TransportAuth>` kwarg. The concrete type differs per transport
  (a NATS transport cannot accept a Kafka SASL descriptor) but the
  surface and lifecycle rules are identical.
- **Pluggable resolver for OSS flexibility.** The `auth=` argument
  accepts a literal auth descriptor, a sync zero-arg callable that
  returns one, or an async zero-arg callable that returns one. The
  callable is invoked inside `connect()`, giving Vault, Secrets Manager,
  and env-var wrappers a single integration point. Sync and async are
  both supported because most secret-store SDKs are async-native.
- **Backwards-compatible default.** An omitted `auth=` argument preserves
  the current behaviour (no authentication for NATS / Kafka; URL-embedded
  credentials still accepted on Rabbit / Redis for the one-line case).

### Non-Goals

- **A library-owned secret store.** Choreo does not ship Vault / Secrets
  Manager / HSM integrations. It offers the resolver extension point
  and documents one-page recipes; the integration code lives in the
  consumer repo.
- **Credential rotation mid-session.** The session-scoped Harness rebuild
  path ([ADR-0007](0007-harness-failure-recovery.md)) handles the
  out-of-band rotation case by fetching credentials fresh. Mid-session
  rotation without a rebuild is out of scope.
- **Bounded lifetime for OAUTHBEARER.** Kafka's OAUTHBEARER SASL
  mechanism expects a callable that aiokafka invokes repeatedly to
  refresh the access token. The callable is retained for the transport's
  lifetime by design; the "cleared after logon" rule cannot apply to it.
  See §Security Considerations item 11 for the carve-out and the
  residual-risk statement.
- **Channel trust enforcement (TLS / mTLS required).** The library does
  not enforce that a given transport must use TLS or mTLS. A TLS-capable
  descriptor paired with a plaintext-only broker fails at logon, but
  nothing prevents a consumer wiring a plaintext descriptor against a
  broker that also offers plaintext. Channel trust is the operator's
  responsibility via broker configuration, deployment network
  boundaries, and the [ADR-0006](0006-environment-boundary-enforcement.md)
  endpoint allowlist. See §Security Considerations item 10.
- **Auth policy in the allowlist.** The allowlist remains an endpoint
  guard. `require_tls` / `require_mtls` are not added to the allowlist
  schema in this ADR. A future ADR may revisit this once one or more
  consumers demonstrates the need.
- **Cryptographic zeroisation of string memory.** Python's garbage
  collector does not guarantee it. Documented limitation, not a bug.
- **Breaking changes to existing Rabbit/Redis URL-embedded-credential
  users.** The URL form continues to work; it is merely joined by a
  typed explicit form, with a collision rule when both are supplied.

---

## Decision Drivers

- **OSS audience heterogeneity.** Consumers range from a laptop pointing
  at a local broker to regulated institutions with HSM-backed rotation.
  The API must not force either extreme on the other.
- **Regulatory posture (inherited).** For consumers in regulated
  environments, credential lifetime, redaction, and auditability are
  hard requirements — the same posture the deleted ADR-0010 enforced.
- **Blast radius of a leak.** A credential that appears in a CI log, a
  pickled snapshot, or a connect-failure traceback is materially worse
  than one that never left the transport's `connect()` stack frame. The
  API shape must make the safe path the default.
- **Minimum surface.** Every new field is a new thing reviewers, fuzzers,
  and future maintainers must reason about. Prefer a typed descriptor
  over N loose kwargs.
- **Symmetry with MockTransport.** Auth must be a no-op for Mock, so
  tests that use the Scenario DSL in the unit suite remain identical
  whether a consumer later swaps for a real authenticated transport.
- **Alignment with existing framework patterns.** Redaction (`safe_url`,
  `_redact.py`), default-deny allowlisting, lazy-import of optional
  extras, and transport-owned config all have precedent; auth should
  reuse those patterns, not fork them.

---

## Considered Options

### Option 1: Typed per-transport `auth=` dataclass with optional resolver callable (chosen)

**Description:** Each transport grows a single `auth: TransportAuth | AuthResolver | AsyncAuthResolver | None = None` kwarg. `TransportAuth` is a transport-specific frozen dataclass-like discriminated union — e.g. `NatsAuth.user_password(...)`, `NatsAuth.token(...)`, `NatsAuth.nkey_with_tls(...)`, `NatsAuth.credentials_file_with_tls(...)`. `AuthResolver = Callable[[], TransportAuth]`; `AsyncAuthResolver = Callable[[], Awaitable[TransportAuth]]`. `connect()` resolves the callable form (if any), validates it is a concrete `TransportAuth` instance, runs per-variant co-requirement checks, hands the descriptor to the client library's native kwargs, then drops its reference on the transport. Secret-bearing fields use `repr=False` and `eq=False`, and the dataclass defines an explicit `__repr__` that prints only the variant tag. The transport's `__reduce__` raises unconditionally (auth-bearing and auth-free alike), matching [ADR-0001](0001-single-session-scoped-harness.md).

**Pros:**
- Full client-library coverage: every auth mode is a named constructor
  on the per-transport union, discoverable by autocomplete.
- Typed at the API boundary: a Kafka SASL descriptor passed to
  `NatsTransport` is a static type error.
- Literal and resolver forms share one parameter. The literal form
  (`auth=NatsAuth.token("foo")`) exists for small dev and test cases; the
  resolver form (`auth=lambda: NatsAuth.token(vault.get("nats/token"))`
  or an async equivalent) is the Vault / Secrets Manager path and gets
  the stronger bounded-lifetime guarantee.
- Sync and async resolvers are both accepted, accommodating async-native
  secret-store SDKs without a sync wrapper.
- Redaction is structural (per-field `repr=False` plus `eq=False` on the
  dataclass, blocking pytest's assertion-rewrite introspection), not a
  defensive scan at every egress point.
- OSS-friendly: no library dependency on a secret store; resolvers are
  ordinary Python.
- Mock validates the descriptor's shape but ignores its values, emitting
  a `mock_transport_ignored_auth` WARNING — the "write once, swap for a
  real transport later" property works, and an authentication-expecting
  test that accidentally runs against Mock is visible in the log.

**Cons:**
- Each transport gains a module-level union type (~8–15 small dataclasses
  per transport). Offset by the fact that users import only the variants
  they need.
- A consumer wiring OAUTHBEARER for Kafka must still write a token
  provider function for `aiokafka`, and the bounded-lifetime guarantee
  does not apply to that variant (the callable is retained). Documented
  in §Non-Goals and §Security Considerations item 11.
- The collision rule (URL-embedded credentials on Rabbit / Redis + an
  explicit `auth=`) must be enforced at construction time and documented.
  Handled by a targeted test (see §Validation).
- Composing multiple NATS auth modes (NKey + TLS, creds file + TLS) is
  expressed as named variants (`nkey_with_tls`, `credentials_file_with_tls`)
  rather than a free-form `compose(*parts)`, because the legal pairings
  are narrow and enumerable. Enumerating is more surface than composing
  would be, but it prevents illegal combinations at construction time.

### Option 2: Loose kwargs on each transport (`username=`, `password=`, `token=`, `tls_cert=`, …)

**Description:** Add the union of all auth-related fields as optional
kwargs directly on each transport's `__init__`. Validation happens at
`connect()` time.

**Pros:**
- No new types; the shape reads like the underlying client library.
- One-line call sites for the simple cases.

**Cons:**
- Combinatorial surface: `NatsTransport.__init__` grows by ~10 fields,
  most of them mutually exclusive (token *or* NKey *or* creds file *or*
  user/pass). Validation is ad-hoc and regression-prone.
- No place to hang a resolver without duplicating every field
  (`username=` vs `username_resolver=`), doubling the surface.
- Redaction must be applied field-by-field everywhere — an opt-in
  posture, exactly the pattern ADR-0010 rejected. One forgotten
  `repr=False` is a leak.
- Cross-transport consistency is accidental rather than enforced; a
  future Kafka field added without a matching NATS equivalent drifts.

### Option 3: URL-embedded credentials only

**Description:** Every transport takes a single connection URL; all
credentials live inside it. Wrap `safe_url` in every egress point.

**Pros:**
- Zero new API surface.
- Already works for Rabbit and Redis.

**Cons:**
- NATS and Kafka have no standard URL-embedded-credential form for
  NKey / creds-file / SASL SCRAM / OAUTHBEARER. The format does not
  exist; it would have to be invented.
- TLS and mTLS material (cert / key / CA paths, in-memory cert bytes)
  have no URL form at all.
- Every log / exception / repr path becomes one `safe_url` call away
  from a leak. Defence by scanning is fragile.
- Ruled out on coverage grounds — it cannot express the required auth
  modes at all.

### Option 4: Harness-owned `SecretResolver` with transport-agnostic keys

**Description:** The Harness accepts a `secret_resolver: Callable[[str], str]`
and looks up well-known keys (`"nats.token"`, `"kafka.password"`) at
connect time, injecting them into transports by name.

**Pros:**
- Single credential surface on the Harness.
- Vault integration is one function for all transports.

**Cons:**
- Violates the post-ADR-0006 posture that transports own their own
  config. The Harness does not know what a NATS token means.
- String-keyed indirection reintroduces runtime typos (`nats.tokn`
  returns `""`, connect fails obscurely) that typed dataclasses catch.
- TLS cert bytes / paths don't fit the `str → str` resolver shape
  without a second layer; a Kafka OAUTHBEARER refresh callback does not
  fit at all.
- A single shared resolver forces every transport into the same auth
  shape; a mixed deployment (Mock + authenticated NATS) needs the
  resolver to branch on key, which is exactly the typed-descriptor
  dispatch Option 1 does at the type-system level.

### Option 5: No library-level auth; document the consumer-side pattern

**Description:** Leave the transports as they are. Ship documentation
telling consumers to subclass a transport and pre-configure the
underlying client themselves, or to monkey-patch the import.

**Pros:**
- Zero library change.

**Cons:**
- Every consumer reinvents the same wheel.
- Subclassing depends on internal implementation details (the `self._nc`
  / `self._producer` fields) that this ADR family otherwise treats as
  private. Locks the library out of refactoring.
- Redaction and lifecycle guarantees cannot be made at the library
  level, because every consumer's subclass will be different.
- Fails the stated goal of full client-library coverage *through a
  public API*.

---

## Decision

**Chosen Option:** Option 1 — typed per-transport `auth=` discriminated-union descriptor with optional resolver callable.

### Rationale

- Only Option 1 satisfies the **no-read-back** and
  **structural-redaction** goals inherited from the deleted ADR-0010
  without reintroducing a Harness-level secret store. Options 2 and 3
  are defensive at every egress (fragile); Option 4 puts the knowledge
  in the wrong place; Option 5 is a non-decision.
- The **bounded-lifetime** principle from ADR-0010 is preserved for the
  resolver branch and relaxed for the literal branch. A consumer who
  cares about lifetime uses the resolver; a consumer with a dev broker
  passes a literal for ergonomics. The relaxation is visible (the
  literal is plain in the call-site diff) and opt-in, not silent.
  §Security Considerations item 1 states the split explicitly.
- Option 1 is the only shape that expresses both the literal form and
  the resolver form through one parameter. Collapsing onto Option 2
  kwargs would duplicate every field (`token=` vs `token_resolver=`).
- The per-transport discriminated union is the right granularity because
  the auth modes really are transport-specific: NATS's NKey has no Kafka
  analogue; Kafka's OAUTHBEARER has no NATS analogue. Forcing a common
  abstract type across transports would either lose expressiveness
  (Option 4) or require open-ended `**kwargs` pass-through (Option 2).
- The API is **additive**: an omitted `auth=` preserves current
  behaviour, so no existing consumer breaks. Rabbit / Redis users who
  rely on URL-embedded credentials continue to work; the collision rule
  (URL userinfo or URL query-string credentials + `auth=`) only fires
  when both are supplied, and raises a `ConflictingAuthError` at
  construction time (not at connect).

---

## Consequences

### Positive

- Every authenticated broker the underlying clients can talk to is
  reachable through Choreo's public API.
- The safe path (no leak, bounded lifetime, redacted repr) is the default
  path — no extra work by the consumer to get it.
- OSS consumers integrate any secret store by writing a one-line
  resolver. The library stays free of vendor-specific dependencies.
- A future Kafka auth mode added upstream (e.g. a new SASL mechanism)
  is a contained change: one new variant on `KafkaAuth`, one new
  passthrough in `KafkaTransport.connect()`.
- `pickle.dumps(transport)` raising when auth is non-None blocks a
  common debug-dump path that would otherwise leak credentials into
  artefacts (cf. [ADR-0001](0001-single-session-scoped-harness.md)
  § Security Considerations item 2).

### Negative

- Four new small modules (`auth.py` per transport subpackage) plus a
  shared `transports/_auth.py` for the base resolver protocol. ~500
  lines of new surface in `packages/core`. Mitigated by keeping each
  variant a frozen dataclass with no logic.
- Every transport test suite gains a section covering auth construction,
  redaction, and lifetime. Acceptable — auth is the kind of surface
  that must be tested *because* its failures are silent.
- A subtle footgun: a consumer who inspects a resolver result for
  debugging (`print(auth())`) defeats redaction. Mitigated by the
  descriptor's own `__repr__` — `print(auth())` prints the redacted
  form, not the raw secrets. Direct field access still exposes values
  by design.
- Docs bloat: the matcher/scenario docs are now joined by a per-transport
  auth page. Offset by linking the same recipes from each transport's
  docstring.

### Neutral

- `MockTransport` accepts `auth=`, validates the descriptor is a
  concrete `<Concrete>Auth` instance (shape check only; values are not
  inspected), emits a `mock_transport_ignored_auth` WARNING once per
  Mock instance, and discards the descriptor. The `_clear_auth_fields`
  helper runs before the WARNING is built so the WARNING payload
  cannot reference any secret-bearing field. The shape validation gives
  the "write once, swap for a real transport later" property genuinely:
  a typo in the descriptor surfaces against Mock too, not only against
  the real transport.
- `safe_url()` (in [transports/base.py](../../packages/core/src/choreo/transports/base.py)) is extended to redact
  credential-shaped query-string keys (`password`, `token`, `secret`,
  `key`, case-insensitive) in addition to userinfo. Every existing
  caller continues to work; the extension is additive.
- `__reduce__` behaviour on each transport raises **unconditionally**,
  matching [ADR-0001](0001-single-session-scoped-harness.md) §Security
  Considerations item 2. A post-clear authenticated transport is
  therefore still unpickleable; a never-authenticated Mock is also
  unpickleable. This is a deliberate loss of pickle-for-diagnostics on
  Mock setups; the gain is zero divergence from the ADR-0001 posture
  and no conditional branch whose correctness depends on `_auth` state.

### Security Considerations

Primary ADR for transport authentication.

1. **Bounded lifetime — anchored to where the secret materialises.**
   The lifetime guarantee depends on *when the secret first enters
   process memory*, not on whether `auth` is a literal or a callable:
   - **Materialised inside `connect()` (strong).** When the secret is
     first assembled inside the resolver callable — e.g. an
     `AsyncAuthResolver` that awaits a Vault fetch on each invocation —
     the descriptor is instantiated inside `connect()`, consumed by
     logon, cleared via `_clear_auth_fields(descriptor)`, and
     `self._auth` is set to `None` in a `try/finally` around the logon
     call. The clear runs on both success and failure paths. This is
     the strongest lifetime bound the library offers.
   - **Materialised before `connect()` (weaker).** Applies to literal
     `auth=<Concrete>Auth(...)` forms *and* to resolvers that return a
     pre-constructed object (`auth=lambda: _CACHED_DESCRIPTOR`). In
     both cases the secret is in memory before `connect()` runs; the
     library bounds the lifetime only at the tail of `connect()` via
     the same clear. Consumers who need head-bound lifetime write a
     resolver that materialises the secret on every call.
   - **Never connected (not defended).** A transport constructed with
     a literal descriptor and then discarded without a `connect()`
     retains the descriptor on `self._auth` until garbage collection.
     The library does not call `_clear_auth_fields` on finalisation —
     `__del__` in CPython is unreliable and a guaranteed clear would
     need an explicit `close()` contract the Harness does not today
     provide. Consumers who construct transports in tests they never
     run should prefer the resolver form.
   - All branches call `_clear_auth_fields` before any logging fires,
     so no log record can reference a cleared field's value.
2. **No read-back accessor.** Transports expose no `get_auth()` /
   `current_credentials()`. The descriptor was supplied by the caller;
   the caller already has it. The library never offers a way back.
3. **Structural redaction.**
   - Every secret-bearing field on every auth dataclass is declared
     `repr=False`.
   - Every auth dataclass sets `eq=False` on the dataclass decorator.
     Without this, the dataclass-generated `__eq__` compares every
     field including `repr=False` ones, and pytest's assertion rewriting
     (`assert a == b`) prints the unequal fields — defeating `__repr__`
     redaction. `eq=False` makes instances compare by identity; a test
     that needs semantic equality uses a helper that compares variant
     tags only. `__hash__` falls back to identity hashing, which is
     correct for short-lived descriptors.
   - Each descriptor defines an explicit `__repr__` returning only the
     variant tag (e.g. `NatsAuth.user_password(<redacted>)`). No field
     values, redacted or otherwise, appear in `__repr__`.
   - `copy.deepcopy(descriptor)` is explicitly blocked by a
     `__deepcopy__` that raises `TypeError`. A test harness that wants a
     shallow copy for whatever reason is forced to construct a new
     descriptor from source data; there is no quiet way to duplicate a
     secret.
   - Every `TransportError` message containing a URL passes through
     `safe_url()`. Auth descriptors never appear in error messages.
4. **No pickling.** Each transport's `__reduce__` raises `TypeError`
   **unconditionally**, matching
   [ADR-0001](0001-single-session-scoped-harness.md) §Security
   Considerations item 2. This is a deliberate loss of
   pickle-for-diagnostics even on Mock and on post-clear transports;
   the gain is no conditional branch whose correctness depends on
   `_auth` state.
5. **Resolver invocation is bounded and failure-safe.**
   - The resolver is invoked exactly once per `connect()` call — both
     successful and failed connects count as one invocation. The
     `_has_connected` flag is set at the **head** of `connect()`
     (before the resolver is invoked), so a resolver that raises, a
     logon that fails, and a successful connect all leave the
     transport in the same "refuse reconnect" state. A consumer who
     wants to retry constructs a fresh transport via
     [ADR-0007](0007-harness-failure-recovery.md)'s rebuild path.
     Reconnect on a transport constructed with `auth=None` remains
     supported as before.
   - Sync and async resolvers are dispatched uniformly. `_resolve_auth`
     calls the resolver; if the result is a coroutine (detected via
     `asyncio.iscoroutine(result)`), the coroutine is awaited. This
     pattern accepts a bare `async def`, an `async def` wrapped in
     `functools.partial`, a `Callable[[], Awaitable[...]]`, and a
     `Callable[[], TransportAuth]` in the same branch — no call-site
     needs to know which shape it passed.
   - A resolver that raises is translated to
     `TransportError("auth resolver failed")`. The wrapping uses
     `raise TransportError(...) from None`, and the sanitiser
     **additionally sets `__suppress_context__ = True` on the
     TransportError before raising**. `from None` alone is not enough:
     a higher-up `raise HarnessError from te` re-exposes
     `te.__context__`, which still points at the original resolver
     exception. The explicit `__suppress_context__` on `te` itself
     makes `logging.exception()` and pytest's traceback formatter
     honour the suppression regardless of outer wrapping.
   - The original exception's qualified class name (not its args, not
     its `str`) is recorded on `TransportError.resolver_cause` after
     escaping via `re.sub(r'[^\w.]', '_', qualname)`. The escape
     covers exotic qualnames from dynamically-created exception types
     (e.g. `type("Bad<tag>", (Exception,), {})`) that could otherwise
     break log-formatter or XML-report escaping. The attribute is
     deliberately not in the exception message.
   - The rule "reduce to class name" is implemented by a dedicated
     helper in `transports/_auth.py` (`_sanitise_resolver_failure`),
     not by `redact_matcher_description`: that function's regex was
     designed for matcher prose and does not redact arbitrary
     exception text.
6. **No resolver retries.** If the resolver raises, `connect()` fails.
   No back-off, no re-fetch — the recovery path is
   [ADR-0007](0007-harness-failure-recovery.md)'s rebuild, which
   creates a fresh transport and a fresh resolver call.
7. **Descriptor trust boundary.** The descriptor arrives across a
   public-API boundary. A hostile or mis-implemented resolver could
   return a subclass of `<Concrete>Auth` with an overridden
   `__repr__` / `__reduce__` / `__eq__` / `__deepcopy__` that leaks
   secrets. The library defends against this with two layers:
   - **Primary defence — exact-type allowlist.** Before any use,
     `connect()` asserts
     `type(descriptor) in _ALLOWED_VARIANTS[transport_name]` (exact
     type, not `isinstance` — subclasses are rejected). A mismatch
     raises `TransportError("auth descriptor is not a known variant")`
     without stringifying the descriptor. This single check catches
     every case: statically declared subclasses, dynamically created
     subclasses (`types.new_class`, `dataclasses.make_dataclass`),
     metaclass-created types, and any other path that produces a type
     object distinct from the registered variants.
   - **Secondary defence — `__init_subclass__` guard.** The
     `_TransportAuth` base's `__init_subclass__` refuses subclasses
     whose `__module__` does not start with `"choreo.transports."` and
     end with `"_auth"`. This is a string-prefix check, not a real
     package boundary — a fork of the library, a module alias, or a
     future internal refactor that moves variants to a new module can
     satisfy or break it. It exists to surface subclass declarations
     at import time (loud, early, locatable) rather than as a
     silently-accepted logon. If the secondary defence disagrees with
     the primary, the primary is authoritative.
   - The library never calls `str(descriptor)`, `repr(descriptor)`,
     `format(descriptor)` on a user-supplied value; only the
     well-known variant tag and the module docstring name appear in
     logs and errors.
8. **URL + `auth=` collision.** On Rabbit and Redis, supplying both
   raises `ConflictingAuthError` at construction. The collision
   predicate runs against the **parsed** URL (not a regex over the
   raw string), so percent-encoded keys (`%70assword=x`) are
   normalised before matching and repeated keys
   (`?password=x&password=y`) are all inspected:

   1. `urllib.parse.urlsplit(url)` — parse scheme, netloc, query.
   2. If `parts.username` or `parts.password` is non-None, the URL
      carries userinfo credentials. An identity-only URL
      (`amqp://u@host`) counts — the consumer picks one form or the
      other, not a mix.
   3. `urllib.parse.parse_qsl(parts.query, keep_blank_values=True)` —
      parse every query-string pair; case-fold each key; test against
      the canonical `_CREDENTIAL_KEY_NAMES` set defined in
      `transports/_auth.py`.

   The canonical set `_CREDENTIAL_KEY_NAMES` is referenced (not
   duplicated) by both the collision check and the `safe_url()`
   query-string redaction. Current members:
   `{"password", "token", "secret", "key", "auth", "credential",
   "credentials", "username", "user"}` (case-insensitive).
9. **Log-key scrubbing at the structured logger.** The structured
   logger's event builder strips any key whose case-folded name ends
   with any suffix in `_CREDENTIAL_KEY_NAMES` preceded by `_` or
   matches one directly (`password`, `auth_token`, `api_secret`,
   `my_service_credentials`). This is net-new implementation in this
   ADR; the previously referenced ADR-0009 was deleted and its
   mechanism is not preserved in
   [_redact.py](../../packages/core/src/choreo/_redact.py), which
   handles matcher literals only. Implementation lives in the logger
   wrapper, not in the transports.
10. **Channel trust is the operator's responsibility.** The library
    does not enforce that a transport uses TLS or mTLS. A TLS-capable
    descriptor against a broker that *also* offers plaintext on the
    same port negotiates whatever the broker's policy selects. A
    plaintext descriptor against a broker that requires TLS fails
    loudly at logon. A plaintext descriptor against a permissive
    broker sends bytes in plaintext and the library does not notice.
    Channel trust is enforced by broker configuration, deployment
    network boundaries, and the
    [ADR-0006](0006-environment-boundary-enforcement.md) allowlist.
    A future ADR may extend the allowlist schema to express
    `require_tls` / `require_mtls` per category; this ADR does not.
11. **OAUTHBEARER retains its token provider.**
    `KafkaAuth.sasl_oauthbearer(token_provider)` stores the callable
    for the transport's lifetime because aiokafka invokes it on every
    token refresh. The bounded-lifetime rule does not apply to this
    variant — the provider IS reachable state. Residual risk:
    (a) the provider's closure captures whatever the consumer wrote
    (Vault client, env var read, cached token); (b) GC does not reach
    a live closure. Documented, not mitigated. Consumers who need
    strong lifetime on OAUTHBEARER use short-lived tokens and accept
    that the refresh callable is long-lived.
12. **Default-deny stays.** The
    [ADR-0006](0006-environment-boundary-enforcement.md) allowlist is
    unchanged. A transport may authenticate, but it may only
    authenticate **against an allowlisted endpoint**. Auth is additive
    to the endpoint guard, not a substitute.
13. **Descriptor reuse across transports is refused.**
    `_clear_auth_fields(descriptor)` sets a `_consumed` flag on the
    descriptor. The variant-allowlist check at the head of every
    transport's resolve-and-logon sequence refuses any descriptor
    where `descriptor._consumed` is True, raising
    `TransportError("auth descriptor has already been consumed by another connect()")`
    without stringifying the descriptor. This turns what would be a
    mysterious logon failure on transport #2 (zeroed fields, arcane
    broker rejection) into a loud, locatable error. A consumer who
    wants to authenticate two transports with the same credentials
    constructs two descriptors (or, better, uses a resolver that
    materialises a fresh descriptor on each call).
14. **Documented memory limitation.** CPython cannot reliably zero a
    Python `str`; `bytes` objects are immutable and cannot be zeroed
    in place either. `_clear_auth_fields` therefore:
    - Zeroes `bytearray` and `memoryview` fields in place.
    - Drops the reference for `bytes` and `str` fields (sets them to
      `None`). GC frees the underlying storage eventually; timing is
      best-effort.
    After `bytearray.clear()` the storage is deallocated on the
    Python side, but the underlying memory pages may remain resident
    until reallocated. Consumers in prod-adjacent CI are reminded
    (via the transport's `auth` module docstring) to configure
    no-core-dump flags at the OS level if this residual risk matters.

---

## Implementation

1. **New module `packages/core/src/choreo/transports/_auth.py`:**
   - `AuthResolver = Callable[[], "_TransportAuth"]` — zero-arg sync
     callable that returns the corresponding transport's auth
     descriptor.
   - `AsyncAuthResolver = Callable[[], Awaitable["_TransportAuth"]]` —
     async equivalent for Vault / Secrets Manager / Azure Key Vault
     SDKs, which are async-native.
   - `class _TransportAuth` — the abstract base the per-transport
     variants inherit from. `__init_subclass__` refuses any subclass
     whose `__module__` does not start with `"choreo.transports."` and
     end with `"_auth"` — a secondary, import-time defence only (the
     primary gate is the `_ALLOWED_VARIANTS` exact-type check at
     `connect()`, per §Security item 7). Declares `__deepcopy__` that
     raises `TypeError` and `__reduce__` that raises `TypeError`.
     Exposes `_consumed: bool` default-False attribute, set True by
     `_clear_auth_fields`.
   - `class ConflictingAuthError(TransportError)` — raised when a
     transport detects both URL-carried credentials (userinfo *or*
     credential-shaped query-string parameters) and an explicit
     `auth=` kwarg.
   - `_CREDENTIAL_KEY_NAMES: frozenset[str]` — the single canonical
     case-folded key set used by both the collision-detection
     predicate and the `safe_url()` query-string redaction. Current
     members: `{"password", "token", "secret", "key", "auth",
     "credential", "credentials", "username", "user"}`. Private-
     unstable: the set may grow over time. Consumer tests that assert
     on its contents are unsupported.
   - `_ALLOWED_VARIANTS: dict[str, frozenset[type]]` — maps each
     transport name (`"nats"`, `"kafka"`, `"rabbit"`, `"redis"`,
     `"mock"`) to the frozenset of concrete variant types that
     transport accepts. Private-unstable: new variants may be added
     in minor releases; a consumer test that pins the set is
     unsupported and will be annotated as such in the module
     docstring.
   - `_clear_auth_fields(descriptor)` helper that walks the
     descriptor's fields: zeros `bytearray` / `memoryview` fields in
     place; drops the reference (sets to `None`) for `bytes` and
     `str` fields — those types are immutable in CPython and cannot
     be zeroed in place. Sets `descriptor._consumed = True` at the
     end. Called by each transport's `connect()` before any logging
     fires, in both success and failure branches.
   - `_sanitise_resolver_failure(exc) -> str` helper that returns
     `re.sub(r'[^\w.]', '_', type(exc).__qualname__)` — only the
     qualified class name, with exotic characters escaped for log
     and report formatter safety. The transport stores this on the
     resulting `TransportError` as a separate `resolver_cause`
     attribute. **Additionally sets**
     `te.__suppress_context__ = True` on the `TransportError`
     instance before raising, so a higher-up re-wrap
     (`raise HarnessError from te`) cannot re-expose the original
     resolver exception via `te.__context__` in a walking
     traceback formatter. Dedicated helper (not the matcher-literal
     regex in [_redact.py](../../packages/core/src/choreo/_redact.py),
     which redacts only `=value` fragments in matcher prose and would
     be a no-op on typical resolver exceptions).
   - `_resolve_auth(raw) -> Awaitable[_TransportAuth]` helper that
     accepts the `auth=` kwarg shape (literal descriptor, sync
     callable, async callable), invokes the callable once, and if
     the result satisfies `asyncio.iscoroutine(result)`, awaits it.
     This single-branch dispatch handles bare `async def`, `async def`
     wrapped in `functools.partial`, `Callable[[], Awaitable[...]]`,
     and `Callable[[], TransportAuth]` uniformly — no caller needs to
     know which shape was passed in. The awaited result is validated
     against `_ALLOWED_VARIANTS[transport_name]` (exact type) and
     its `_consumed` flag (must be False).
2. **Per-transport auth module.** Each of
   [transports/nats.py](../../packages/core/src/choreo/transports/nats.py),
   [transports/kafka.py](../../packages/core/src/choreo/transports/kafka.py),
   [transports/rabbit.py](../../packages/core/src/choreo/transports/rabbit.py),
   [transports/redis.py](../../packages/core/src/choreo/transports/redis.py)
   gains a sibling `*_auth.py` file re-exported as `NatsAuth`,
   `KafkaAuth`, `RabbitAuth`, `RedisAuth` from `choreo.transports`.
   Every variant is a `@dataclass(frozen=True, eq=False, slots=True)`
   subclass of `_TransportAuth`, with secret-bearing fields declared
   `field(repr=False)`.
   - `NatsAuth.user_password(username, password)`,
     `NatsAuth.token(token)`,
     `NatsAuth.nkey(seed)`,
     `NatsAuth.credentials_file(path)`,
     `NatsAuth.tls(ca, cert=None, key=None, hostname=None)`,
     `NatsAuth.user_password_with_tls(username, password, tls)`,
     `NatsAuth.token_with_tls(token, tls)`,
     `NatsAuth.nkey_with_tls(seed, tls)`,
     `NatsAuth.credentials_file_with_tls(path, tls)`. The enumerated
     `*_with_tls` variants replace the previous `compose(*parts)` form
     because the legal pairings are narrow and known at design time;
     a varargs compose would accept illegal combinations
     (`nkey + user_password`, `credentials_file + nkey`) at runtime.
   - `KafkaAuth.sasl_plain(username, password)`,
     `KafkaAuth.sasl_scram(username, password, mechanism)` — where
     `mechanism` is `"SCRAM-SHA-256"` or `"SCRAM-SHA-512"` (validated
     at descriptor construction),
     `KafkaAuth.sasl_oauthbearer(token_provider)` — `token_provider`
     is retained for the transport's lifetime (see §Security
     Considerations item 11),
     `KafkaAuth.ssl(ca, cert=None, key=None)`,
     `KafkaAuth.sasl_ssl(sasl=..., ssl=...)` — SASL over SSL
     composite.
   - `RabbitAuth.plain(username, password)`,
     `RabbitAuth.external(cert, key, ca)` — EXTERNAL auth requires
     TLS; the descriptor carries the TLS material itself so the
     transport's co-requirement check has a single source of truth,
     `RabbitAuth.amqps(ca=..., cert=None, key=None)`.
   - `RedisAuth.password(password)`,
     `RedisAuth.acl(username, password)`,
     `RedisAuth.tls(ca, cert=None, key=None)`,
     `RedisAuth.acl_tls(username, password, ca, cert=None, key=None)`.
3. **Transport `__init__` signature change.** Each of the four real
   transports gains
   `auth: <Concrete>Auth | AuthResolver | AsyncAuthResolver | None = None`
   as a keyword-only arg (the constructors are already `*,` kw-only
   per their existing signatures). The `None` default preserves
   current behaviour. Rabbit and Redis additionally parse their URL at
   construction via `urllib.parse.urlsplit` + `parse_qsl(keep_blank_values=True)`
   and raise `ConflictingAuthError` if either the userinfo is
   non-empty or any query-string key (case-folded) is in
   `_CREDENTIAL_KEY_NAMES` **and** `auth` is non-None. The parsed-
   value approach handles percent-encoded keys (`%70assword` →
   `password`) and repeated keys (`?password=x&password=y` inspects
   every instance), which a regex over the raw string would miss.
   `safe_url()` in [transports/base.py](../../packages/core/src/choreo/transports/base.py)
   reuses the same `_CREDENTIAL_KEY_NAMES` set and the same parse-
   then-match path to redact query-string values in logged URLs.
4. **`connect()` updates (per transport).** Replace the current
   client-library call with:
   1. **Reconnect guard (set at head of `connect()`).** The
      `_has_connected` flag is set on the transport at the very head
      of `connect()` — before the resolver is invoked, before the
      variant check runs, before anything else. This means a resolver
      that raises, a variant-check rejection, a co-requirement
      failure, a logon timeout, and a successful connect all leave
      the transport in the same "refuse reconnect" state. If
      `self._auth is not None` and `self._has_connected is True` on
      entry, raise
      `TransportError("auth-bearing transports do not support reconnect; construct a fresh transport")`
      immediately. Transports constructed with `auth=None` do not set
      the flag and reconnect as before.
   2. **Resolve the descriptor.** Call
      `descriptor = await _resolve_auth(self._auth)`. `_resolve_auth`
      invokes the callable (or returns the literal) once, then
      `asyncio.iscoroutine(result)` decides whether to `await`; this
      handles sync callables, async callables, and
      `functools.partial`-wrapped shapes uniformly. A resolver
      exception is caught and re-raised as
      `TransportError("auth resolver failed") from None`, with
      `te.resolver_cause` set to the escaped qualname and
      `te.__suppress_context__ = True` so `__context__` is not walked
      by log formatters.
   3. **Variant allowlist and reuse check.** `type(descriptor)` must
      be in `_ALLOWED_VARIANTS[<this-transport>]` (exact type, not
      `isinstance`). `descriptor._consumed` must be False. If either
      fails, raise
      `TransportError("auth descriptor is not a known variant")` or
      `TransportError("auth descriptor has already been consumed by another connect()")`,
      in neither case stringifying the descriptor.
   4. **Co-requirement check (pre-logon).** Per-transport:
      - `RabbitAuth.external` requires TLS: enforced by the
        descriptor carrying the TLS material directly (no mismatch
        possible). If the transport's URL scheme is `amqp://` (not
        `amqps://`) and the descriptor is `external`, raise
        `TransportError("RabbitAuth.external requires an amqps:// URL")`.
      - `KafkaAuth.sasl_plain` against a plaintext `bootstrap_servers`
        entry emits a structured WARNING with event name
        `plain_auth_over_plaintext` (listed as a CI-log-scanner
        sentinel per §Monitoring) but does not refuse — Kafka's
        plaintext-vs-TLS selection is at the listener level and
        cannot be inferred from the bootstrap string alone. Channel
        trust is the operator's responsibility per §Security
        Considerations item 10.
      - `RedisAuth.password` against a `redis://` (not `rediss://`)
        URL emits the same `plain_auth_over_plaintext` WARNING.
   5. **Clear the instance reference to `self._auth`.** Set
      `self._auth = None` *before* the logon call so a logon-induced
      crash, hang, or signal cannot leave the original reference on
      the instance. The local `descriptor` still holds the material
      for the logon call itself.
   6. **Translate and logon.** Translate the resolved descriptor into
      the client library's native kwargs (e.g.
      `nats.connect(user=..., password=...)`,
      `AIOKafkaProducer(sasl_mechanism=..., ...)`,
      `aio_pika.connect_robust(url, ssl=..., ssl_context=...)`,
      `Redis.from_url(url, password=..., ssl=...)`). Call inside a
      `try/finally`.
   7. **Clear on exit.** In the `finally`, call
      `_clear_auth_fields(descriptor)` before any logging fires. The
      clear runs on success, logon failure, and cancellation alike,
      and sets `descriptor._consumed = True` so any subsequent reuse
      is caught at step 3.
5. **Mock transport.** `MockTransport` accepts `auth` for parity.
   When non-None, on `connect()`:
   - Runs the variant-allowlist and `_consumed` checks (same as real
     transports) so a wrong-variant or reused descriptor fails loudly
     against Mock too, not only against the real transport.
   - Calls `_clear_auth_fields(descriptor)` *before* building the
     WARNING event, so the event payload cannot reference any
     secret-bearing field even by accident.
   - Emits a `mock_transport_ignored_auth` WARNING exactly once per
     MockTransport instance (a per-instance flag is flipped on first
     emission to prevent function-scoped fixture spam when a test
     harness constructs a new MockTransport per test). The event
     payload is the variant qualname only — no field values,
     redacted or otherwise.
   - A MockTransport constructed with a literal `auth=` descriptor
     and discarded without calling `connect()` retains the
     descriptor until GC. This matches the "never connected (not
     defended)" case in §Security item 1 and is not defended at the
     library level.
6. **Repr contract.** `_TransportAuth.__repr__` is defined on the
   base as:
   ```python
   def __repr__(self) -> str:
       return f"{type(self).__qualname__}(<redacted>)"
   ```
   `__init_subclass__` raises if a subclass defines `__repr__`
   explicitly — the base's implementation is the only one permitted.
   Enforced by a framework-internal test that reflects every variant
   and asserts no field value survives the repr.
7. **Pickle and deepcopy contract.** `_TransportAuth.__reduce__` and
   `_TransportAuth.__deepcopy__` both raise `TypeError`
   unconditionally. Each transport's own `__reduce__` also raises
   unconditionally, matching
   [ADR-0001](0001-single-session-scoped-harness.md). Dedicated tests
   cover both paths.
8. **Documentation.**
   - [CLAUDE.md §Runtime configuration](../../CLAUDE.md) gains an
     "### Authentication" subsection with one recipe per transport
     and the env-var / Vault resolver pattern.
   - New file [docs/guides/authentication.md](../guides/authentication.md) covers common auth modes
     with worked examples, including async-resolver recipes for Vault
     and AWS Secrets Manager. Linked from each transport's module
     docstring.
   - The e2e compose file gains an opt-in authenticated profile per
     transport (see §Validation below).
9. **No ADR-0006 change.** The allowlist schema is not extended. A
   TLS-requiring descriptor against a non-TLS broker fails at the
   logon round-trip, loudly; a plaintext descriptor against a
   permissive broker is the operator's responsibility (§Security
   Considerations item 10). If a future consumer needs allowlist-
   level enforcement, a superseding ADR handles it.
10. **`safe_url()` extension (in
    [transports/base.py](../../packages/core/src/choreo/transports/base.py)).**
    The existing URL userinfo redaction is joined by query-string
    redaction keyed on the canonical `_CREDENTIAL_KEY_NAMES` set
    defined in `transports/_auth.py` (item 1 above). The
    implementation parses the URL with
    `urllib.parse.urlsplit` + `parse_qsl(keep_blank_values=True)`,
    case-folds each key, and rewrites any match to
    `<redacted>`; it does not apply a regex over the raw URL, which
    would miss percent-encoded keys and repeated keys. The public
    contract of `safe_url()` extends from "redact userinfo only" to
    "redact userinfo and credential-shaped query-string values";
    `transports/base.py`'s own callers (`rabbit.py`, `redis.py`)
    benefit transparently. Downstream callers that parsed the
    returned URL expecting the query string unchanged now see
    `<redacted>` values there — flagged in the CHANGELOG. Covered
    by an extension of
    [test_safe_url.py](../../packages/core/tests/test_safe_url.py).

### Migration Path

- Existing consumers (NATS / Kafka with unauthenticated brokers; Rabbit /
  Redis with URL-embedded credentials) are unaffected. The `auth=`
  parameter defaults to `None` and the existing code paths are the
  `None` branches.
- A CHANGELOG entry lists the new `auth=` parameter per transport and
  links the guide.
- No deprecation of URL-embedded credentials: Rabbit and Redis consumers
  who already use that form continue to. The `ConflictingAuthError` only
  fires if a consumer opts into both.

### Timeline

- **Phase 1 — foundation.** `transports/_auth.py` (base types
  including `_TransportAuth`, `AuthResolver`, `AsyncAuthResolver`,
  `ConflictingAuthError`, `_clear_auth_fields`,
  `_sanitise_resolver_failure`, `_resolve_auth`, the per-transport
  `_ALLOWED_VARIANTS` registry), plus `NatsAuth` variants and
  `NatsTransport.connect()` rewire, plus `MockTransport.connect()`
  rewire (Mock inherits the variant-allowlist check). Ships
  NATS-only and Mock-only contract tests; the cross-transport
  contract matrix is incrementally populated as later phases land.
- **Phase 2 — Kafka.** `KafkaAuth` + `KafkaTransport`. Depends on
  Phase 1's `_auth.py`. Extends the contract matrix with
  Kafka rows.
- **Phase 3 — RabbitMQ.** `RabbitAuth` + `RabbitTransport`. Depends
  on Phase 1. Enforces `ConflictingAuthError` on URL + `auth=`
  co-presence. Extends the contract matrix with Rabbit rows.
- **Phase 4 — Redis.** `RedisAuth` + `RedisTransport`. Depends on
  Phase 1. Enforces `ConflictingAuthError` on URL + `auth=`
  co-presence. Completes the cross-transport contract matrix.
- **Phase 5 — docs and tooling.** Authentication guide, CHANGELOG,
  compose `--auth` profile, log-scanner wiring. Depends on all
  prior phases for accurate examples.

Phases 2–4 are mutually independent and may ship in any order (or
in parallel PRs) after Phase 1. Phase 1 has no external dependency
and is independently releasable on its own; each subsequent phase
is releasable when its predecessors ship. The `§Validation §Tests`
cross-transport contract file is populated **per phase** — a Phase
1 release ships only the NATS and Mock rows, not a false claim of
full-matrix coverage.

---

## Validation

### Success Metrics

- **Coverage matrix.** Every row below reaches a connected broker in
  the e2e suite and completes a round-trip. Matrix rows that are not
  yet green are the release-gating checklist:

  | Transport | Variant | Status gate |
  |-----------|---------|-------------|
  | NATS | `user_password` | Phase 1 |
  | NATS | `token` | Phase 1 |
  | NATS | `nkey` | Phase 1 |
  | NATS | `credentials_file` | Phase 1 |
  | NATS | `tls` | Phase 1 |
  | NATS | `nkey_with_tls` | Phase 1 |
  | NATS | `credentials_file_with_tls` | Phase 1 |
  | Kafka | `sasl_plain` | Phase 2 |
  | Kafka | `sasl_scram` (SHA-256) | Phase 2 |
  | Kafka | `sasl_scram` (SHA-512) | Phase 2 |
  | Kafka | `sasl_oauthbearer` | Phase 2 |
  | Kafka | `ssl` | Phase 2 |
  | Kafka | `sasl_ssl` | Phase 2 |
  | Rabbit | `plain` | Phase 3 |
  | Rabbit | `external` | Phase 3 |
  | Rabbit | `amqps` | Phase 3 |
  | Redis | `password` | Phase 4 |
  | Redis | `acl` | Phase 4 |
  | Redis | `tls` | Phase 4 |
  | Redis | `acl_tls` | Phase 4 |

- **No credential egress:** zero occurrences of any test credential's
  literal value in captured CI logs, test reports, or exception
  tracebacks across every e2e run. Enforced by a post-run log scan
  keyed on sentinel values (e.g. `choreo-e2e-password-do-not-log`).
  **Sentinel-based scanning is a necessary but not sufficient
  check** — it catches framework-test regressions, not consumer-side
  leaks. The consumer-facing guarantees are the structured-redaction
  contract (§Security Considerations items 3, 9) plus descriptor
  `__repr__` / `__eq__` / `__deepcopy__` / `__reduce__` blocks.
- **Bounded lifetime (resolver branch):** after
  `await transport.connect()` on each real transport with a resolver,
  `transport._auth is None` and any `bytes` / `bytearray`-valued secret
  field on the returned descriptor reads as all-zero.
- **Bounded lifetime (literal branch):** after
  `await transport.connect()` on each real transport with a literal
  descriptor, the same post-conditions hold; the head-end lifetime is
  not asserted (construction-time was the secret's entry point by
  design).
- **Pickle refusal:** every transport raises on `pickle.dumps(...)`
  unconditionally (auth or no auth), and every descriptor raises on
  `pickle.dumps(...)` and `copy.deepcopy(...)`.
- **Repr safety:** every auth variant's `repr` does not contain any
  field value. Asserted by a parameterised test that reflects every
  subclass.
- **Descriptor-trust boundary:** a subclass of a variant declared
  outside `choreo.transports.*_auth` fails at module import (from
  `__init_subclass__`); if one slips in via dynamic creation, the
  transport's variant-allowlist check rejects it at `connect()`.

### Tests

The following tests land alongside the implementation. Each behaviour
gets its own test; shared setup belongs in fixtures
([CLAUDE.md §Test style](../../CLAUDE.md)).

#### Cross-transport contract tests

`packages/core/tests/test_transport_auth_contract.py` parametrised over
every transport (`mock`, `nats`, `kafka`, `rabbit`, `redis`):

- `test_a_transport_constructed_without_auth_should_not_alter_current_connect_behaviour`
- `test_a_transport_with_an_auth_descriptor_should_accept_a_literal_value`
- `test_a_transport_with_a_sync_auth_resolver_should_call_it_exactly_once_per_connect`
- `test_a_transport_with_an_async_auth_resolver_should_await_it_exactly_once_per_connect`
- `test_a_transport_auth_resolver_that_raises_should_surface_a_transport_error`
- `test_a_transport_auth_resolver_error_message_should_not_contain_the_resolver_exception_args`
- `test_a_transport_auth_resolver_error_should_suppress_the_original_exception_cause_chain`
- `test_a_transport_auth_resolver_error_should_record_the_original_exception_class_name_on_a_resolver_cause_attribute`
- `test_a_transport_given_a_descriptor_of_another_transports_variant_should_raise_a_transport_error_without_stringifying_the_descriptor`
- `test_a_transport_given_a_subclass_of_a_known_variant_should_raise_a_transport_error`
- `test_a_connected_transport_should_not_expose_its_auth_descriptor_via_any_public_accessor`
- `test_a_connected_transport_should_report_its_bytes_secret_fields_as_all_zero_after_connect`
- `test_a_transport_whose_logon_fails_should_still_clear_the_auth_descriptor_on_the_failure_path`
- `test_a_transport_constructed_with_auth_should_refuse_a_second_connect_after_disconnect`
- `test_a_transport_constructed_without_auth_should_permit_reconnect_after_disconnect`
- `test_a_transport_should_refuse_to_pickle_with_or_without_auth`
- `test_a_transport_repr_should_never_contain_any_auth_descriptor_value`
- `test_a_transport_connect_failure_should_raise_a_transport_error_that_does_not_contain_any_secret`

#### Per-transport descriptor tests

`packages/core/tests/test_nats_auth.py`,
`test_kafka_auth.py`,
`test_rabbit_auth.py`,
`test_redis_auth.py`. For each variant of each union:

- `test_a_<variant>_descriptor_should_accept_its_documented_fields`
- `test_a_<variant>_descriptor_repr_should_not_contain_any_field_value`
- `test_a_<variant>_descriptor_should_not_permit_mutation_after_construction`
- `test_a_<variant>_descriptor_should_accept_bytearray_secret_values`
- `test_a_<variant>_descriptor_should_refuse_to_pickle`
- `test_a_<variant>_descriptor_should_refuse_to_deepcopy`
- `test_two_distinct_<variant>_descriptors_with_equal_fields_should_not_compare_equal`
  (guards the `eq=False` contract that blocks pytest-assertion leakage)
- `test_a_<variant>_descriptor_class_should_refuse_subclasses_declared_outside_the_library_package`

For `KafkaAuth.sasl_scram` specifically:

- `test_a_sasl_scram_descriptor_should_reject_a_mechanism_outside_the_supported_set`
  (only `"SCRAM-SHA-256"` and `"SCRAM-SHA-512"` accepted).

For `KafkaAuth.sasl_oauthbearer` specifically (carve-out tests):

- `test_a_sasl_oauthbearer_descriptor_should_retain_the_token_provider_for_the_transports_lifetime`
- `test_a_connected_kafka_transport_with_sasl_oauthbearer_should_not_clear_the_token_provider_on_connect`
  (asserts the documented carve-out, so a future "fix" that clears
  the provider breaks the test loudly)

For `RabbitAuth.external` specifically:

- `test_a_rabbit_external_descriptor_used_with_a_non_amqps_url_should_raise_at_connect_time`
  (co-requirement check; descriptor construction does not know the
  URL, so the check lives on the transport side and the test names
  where it fires)

#### URL-versus-`auth=` collision tests

`packages/core/tests/test_rabbit_transport.py`,
`test_redis_transport.py`:

- `test_a_rabbit_transport_constructed_with_userinfo_credentials_in_the_url_and_an_auth_descriptor_should_raise_at_construction_time`
- `test_a_rabbit_transport_constructed_with_an_identity_only_url_and_an_auth_descriptor_should_raise_at_construction_time`
  (identity-only URL counts as credential-bearing; consumers pick
  one form or the other, not a mix)
- `test_a_rabbit_transport_constructed_with_a_credential_free_url_and_an_auth_descriptor_should_accept_both`
- `test_a_redis_transport_constructed_with_userinfo_credentials_in_the_url_and_an_auth_descriptor_should_raise_at_construction_time`
- `test_a_redis_transport_constructed_with_a_password_query_parameter_in_the_url_and_an_auth_descriptor_should_raise_at_construction_time`
- `test_a_redis_transport_constructed_with_a_credential_free_url_and_an_auth_descriptor_should_accept_both`
- `test_safe_url_should_redact_credential_shaped_query_string_parameters`
  (extension to [test_safe_url.py](../../packages/core/tests/test_safe_url.py) covering the new
  query-string redaction rule)

#### MockTransport parity tests

`packages/core/tests/test_mock_transport.py`:

- `test_a_mock_transport_given_an_auth_descriptor_should_log_a_warning_and_ignore_it`
- `test_a_mock_transport_given_an_auth_descriptor_should_clear_it_before_the_warning_event_is_built`
  (asserts the WARNING payload cannot reference any secret-bearing
  field, even by accident)
- `test_a_mock_transport_given_a_wrong_variant_descriptor_should_raise_the_same_way_a_real_transport_does`
  (the shape-validation property that makes "write once, swap for a
  real transport later" genuinely safe)
- `test_a_mock_transport_given_an_auth_descriptor_should_clear_it_after_connect`

#### Redaction tests

`packages/core/tests/test_auth_redaction.py`:

- `test_a_transport_error_raised_during_connect_should_route_its_message_through_safe_url`
  (existing behaviour, re-asserted against the new `auth=` path)
- `test_a_structured_log_event_with_a_credential_shaped_key_should_omit_the_value`
- `test_a_transport_error_wrapping_a_resolver_failure_should_not_carry_the_resolver_exceptions_original_args`
- `test_a_transport_error_wrapping_a_resolver_failure_should_not_expose_the_original_exceptions_cause_chain`
  (guards against `from exc` regressing to `from None`; traceback
  formatters must see no `__cause__`)
- `test_a_descriptor_subjected_to_pytest_assertion_rewriting_should_not_leak_any_field_value`
  (asserts the `eq=False` posture; uses `pytest.raises(AssertionError)`
  around `assert a == b` and scans the captured message)
- `test_a_descriptor_deepcopied_should_raise_type_error`
- `test_a_transport_instance_reference_in_a_captured_log_event_should_render_the_variant_tag_only`

#### E2E authenticated contract tests

`packages/core/tests/e2e/contract/test_transport_auth_round_trip.py`,
opt-in via an `--auth` profile on the compose stack
[docker/compose.e2e.yaml](../../docker/compose.e2e.yaml). New services:

- `nats-auth` — `-user test -pass choreo-e2e-password-do-not-log`
- `kafka-auth` — SASL PLAIN with a JAAS file mounted in
- `rabbitmq-auth` — `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`
- `redis-auth` — `--requirepass choreo-e2e-password-do-not-log`

Tests:

- `test_an_authenticated_<transport>_should_complete_a_round_trip_with_the_supplied_auth_descriptor`
- `test_an_authenticated_<transport>_should_refuse_to_connect_with_the_wrong_credentials`
- `test_an_authenticated_<transport>_should_clear_the_descriptor_after_a_successful_connect`

Each is skipped with a clear reason string when the auth-enabled
container is not up, following the existing skip pattern at
[tests/e2e/factories.py:53-63](../../packages/core/tests/e2e/factories.py#L53-L63).

#### CI log scan

`scripts/scan_ci_logs_for_credentials.py` (new): scans captured CI log
artefacts for sentinel strings (`choreo-e2e-password-do-not-log`, test
tokens, test NKey seeds). Any match fails the CI job. Wired into the
e2e workflow after `pytest -m e2e` completes.

### Monitoring

- Every transport emits a `transport_authenticated` structured log
  event at `connect()` success with `{transport: ..., auth_variant:
  ...}` — never any field values. A CI log scanner asserts the
  emission and asserts the absence of credential-shaped values.
- PR-gate: an AST-based CI check (same pattern as
  [ADR-0003](0003-threadsafe-call-soon-bridge.md)'s thread-safety
  whitelist) flags any `str(auth)`, `f"{auth}"`, `format(auth)`,
  `logger.*(... auth)`, or `struct_log.bind(auth=...)` /
  `loguru.bind(auth=...)` / `structlog.bind(auth=...)` reference to a
  descriptor in framework code outside the descriptor's own module.
  Tests that intentionally stringify (for the repr-safety test) use
  an explicit `# noqa` with a rationale comment. The check lives at
  `scripts/check_auth_stringify.py` and runs in the same CI step as
  the existing AST checks.
- Scheduled check: a nightly CI job brings up the compose `--auth`
  profile, runs the authenticated contract tests, and fails if any
  sentinel appears in the captured logs. Catches regressions in the
  log-redaction rule.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single session-scoped Harness (the transport this ADR authenticates lives inside it; `__reduce__` unconditional-raise rule inherited verbatim).
- [ADR-0003](0003-threadsafe-call-soon-bridge.md) — AST-based CI check pattern reused by the `scripts/check_auth_stringify.py` gate.
- [ADR-0006](0006-environment-boundary-enforcement.md) — Environment-boundary enforcement (unchanged; auth is additive to the endpoint allowlist, not a substitute).
- [ADR-0007](0007-harness-failure-recovery.md) — Harness failure recovery (rebuild path pulls credentials fresh via the resolver; the recovery path is the only sanctioned way to "re-auth").
- **Deleted ADR-0010 "Secret Management in the Harness"** — principles partially inherited (no read-back, redacted repr, no pickle) and rehomed at the transport layer. The "fetched, not stored" principle is preserved only on the resolver branch (see §Rationale and §Security Considerations item 1). The deleted ADR is retrievable via `git show <sha>:docs/adr/0010-secret-management-in-harness.md` at the commit preceding its removal on 2026-04-18.

---

## References

- NATS Python client authentication docs: https://nats-io.github.io/nats.py/ (authentication section)
- aiokafka SASL / SSL docs: https://aiokafka.readthedocs.io/en/stable/ (producer / consumer security)
- aio-pika connection docs: https://aio-pika.readthedocs.io/en/latest/ (TLS + EXTERNAL auth)
- redis-py asyncio TLS docs: https://redis.readthedocs.io/en/stable/
- OWASP ASVS V2 "Authentication Architectural Requirements" — baseline for credential handling in library APIs.
- HashiCorp Vault / AWS Secrets Manager / Azure Key Vault reference docs — for consumer-side resolver recipes.

---

## Notes

### Revision history (pre-Accepted)

- **2026-04-18 (initial draft).** First version of this ADR.
- **2026-04-18 (review revision, this file).** Updates from a
  three-actor review (architect / security-auditor / code-reviewer).
  Substantive changes:
  - Dropped the incorrect reliance on
    [_redact.py](../../packages/core/src/choreo/_redact.py)'s
    `redact_matcher_description` for resolver-exception redaction;
    specified a dedicated `_sanitise_resolver_failure` helper.
  - Replaced `from exc` chaining with `from None` + a separate
    `resolver_cause` attribute on `TransportError`, so CI log
    formatters do not walk the cause chain.
  - Added a descriptor-trust boundary (exact-type variant allowlist,
    `__init_subclass__` refusal of external subclasses).
  - Carved OAUTHBEARER out of the bounded-lifetime goal explicitly.
  - Split bounded lifetime into a stronger "resolver branch" and a
    weaker "literal branch" with the trade-off named.
  - `RabbitAuth.external` TLS co-requirement is enforced at
    `connect()` (post-resolver, pre-logon), not at descriptor
    construction.
  - `AsyncAuthResolver` added to support async-native secret-store
    SDKs.
  - `ConflictingAuthError` predicate now covers URL userinfo *and*
    credential-shaped query-string parameters; `safe_url()` extends
    to redact query-string too.
  - `__reduce__` raises **unconditionally** on every transport,
    aligning with ADR-0001 instead of conditioning on `_auth`.
  - `eq=False` and `__deepcopy__` blocks on every descriptor to close
    pytest-assertion-rewrite and `copy.deepcopy` leak paths.
  - Auth-bearing transports refuse a second `connect()` after
    `disconnect()`; auth-free transports reconnect as before.
  - Replaced `NatsAuth.compose(*parts)` with enumerated named
    variants (`nkey_with_tls`, `credentials_file_with_tls`, …) so
    illegal combinations are construction-time errors.
  - Mock's auth handling now does a shape check and clears before
    any WARNING is built; a test asserts the clear precedes
    logging.
  - Coverage matrix added to §Success Metrics; phase dependencies
    made explicit in §Timeline.
  - Test names recast to observable-behaviour wording per CLAUDE.md
    §Test style.
  - Option 4's weak "forces every consumer to wire a resolver" Con
    removed; replaced with the genuine transport-ownership and
    OAUTHBEARER-shape Cons.
  - Floating directory link fixed; ADR-0006 §Notes anchor added;
    deleted-ADR-0010 git retrieval command added in §Related
    Decisions.

### Open follow-ups

- **Auth policy in the allowlist.** If two or more consumers ask for
  `require_tls` / `require_mtls` enforcement at the allowlist layer, a
  superseding ADR extends the schema. Not done here. **Owner:** Platform,
  when a concrete ask appears.
- **OAUTHBEARER token-refresh contract.** Kafka's OAUTHBEARER mechanism
  expects a callable that returns a fresh token on demand. The
  `KafkaAuth.sasl_oauthbearer(token_provider)` variant accepts one; the
  library does not wrap it with caching or refresh logic. If experience
  shows that most consumers need a caching wrapper, a follow-up ADR
  introduces one. **Owner:** Platform, post-1.0.
- **HSM-backed certificates.** Certificates from a hardware security
  module have a different lifecycle (they are never "bytes in memory"
  at all). The TLS variants accept `bytes | Path | ssl.SSLContext`; an
  HSM consumer passes a pre-built `SSLContext`. The library does nothing
  HSM-specific. **Owner:** Platform + Security, when a consumer
  materialises.
- **Credential-free broker ergonomics in docs.** Ensure the quickstart
  never uses an authenticated example first; consumers new to the
  library should see the `MockTransport` path before any auth surface.
  **Owner:** Docs lead.

### Why URL-embedded credentials survive on Rabbit and Redis

It would be cleaner to deprecate the URL-embedded form and force every
consumer onto `auth=`. We don't, for two reasons:

1. The URL form is idiomatic for these protocols — `amqp://u:p@host`
   and `redis://:p@host` are what every tutorial, SDK example, and
   ops runbook shows. Breaking that costs consumers familiarity.
2. The URL form is already redacted at every egress point by
   `safe_url()`. The leak surface is already mitigated.

The collision rule is the safety valve: a consumer who opts into
explicit `auth=` gets an immediate, loud error if they also have
credentials in the URL, so there is no ambiguity about which wins.

### Why not a pytest-style "harness fixture" that wraps auth

The canonical consumer pattern ([CLAUDE.md §Downstream consumer usage](../../CLAUDE.md)) already puts transport construction in the
consumer's own `conftest.py`. A shared fixture in the library would
either be too opinionated (chooses an env-var scheme, chooses which
variant to default to) or too abstract to be useful. The resolver
callable is the right seam: a three-line consumer fixture wraps it.
