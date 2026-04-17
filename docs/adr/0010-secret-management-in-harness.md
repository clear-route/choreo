# 0010. Secret Management in the Harness

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure + Security
**Technical Story:** [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md) item 2 — "Credential lifecycle"

---

## Context

The Harness holds transport session credentials — passwords, certificate references, and any other secret needed to log on to a counterparty. These secrets exist in process memory for the duration of the logon handshake; keeping them any longer than necessary is a cost we do not need to pay.

[ADR-0001 §Goals](0001-single-session-scoped-harness.md) already commits to "never retain plaintext transport credentials" and lists `__repr__` redaction and `__reduce__` blocking as requirements. The security review flagged the lifecycle itself as HIGH: where do secrets come from, when do they leave memory, who can read them? This ADR is the second blocker (alongside [ADR-0007](0007-harness-failure-recovery.md)) keeping [ADR-0001](0001-single-session-scoped-harness.md) in Proposed status.

### Background

- `HarnessConfig.password` exists today as a dataclass field with `repr=False` (in `packages/core/src/core/harness.py`). The value is accepted at construction.
- `Harness.__reduce__` raises to block pickling (in `packages/core/src/core/harness.py`).
- Real-transport flows (not yet implemented) need the password at logon. Everything else can proceed without it.
- Many organisations use HashiCorp Vault / AWS Secrets Manager / Thales HSMs. This ADR stops short of choosing a vault integration — that is deployment-specific and can be added later — but leaves room for one.

### Problem Statement

How are transport credentials acquired, used, and destroyed within a Harness session, such that the window during which a credential is readable is bounded to the logon handshake and the repr / pickle / log surfaces never leak?

### Goals

- **Bounded lifetime.** Credentials exist on the Harness only for the duration of `connect()`'s logon handshake. After logon completes, the value is cleared from the Harness's reachable state.
- **No read-back accessor.** Tests (and framework internals) cannot get the password from the Harness after construction.
- **Redacted in every representation.** `__repr__`, `__getstate__`, log events, diagnostic snapshots — none of them contain the cleartext value.
- **No pickling.** Any attempt to pickle a Harness (during debugging, artefact capture, cross-process transfer) raises.
- **Fetched, not stored.** Credentials come from an environment variable or a vault lookup at connect time, not from a committed file or a long-lived config object.
- **Auditable boundary.** A reviewer reading `Harness.connect()` must be able to see exactly where the credential enters the process and exactly where it is cleared.

### Non-Goals

- Vault integration. HashiCorp Vault, AWS Secrets Manager, AzureKV — the Harness reads from a `HARNESS_PASSWORD` env var. Vault is a wrapper layer the operator adds around that env var.
- Cryptographic zeroisation. Python's GC makes true memory scrubbing best-effort only; we document this limitation rather than pretend otherwise.
- Rotation mid-session. If a credential changes during a session, the operator reconnects; we do not support live rotation.
- Per-backend secret differentiation beyond what the configuration supports. If a real-transport layer needs multiple sessions with different passwords, this ADR extends naturally; multi-session secret multiplexing is handled at the transport layer.

---

## Decision Drivers

- **Regulatory.** MiFID II / MAR supervision assumes credentials stay inside the systems that need them.
- **Principle of least privilege in time.** The same principle that says "don't give everyone access" says "don't keep access longer than needed".
- **Predictability.** A reviewer must be able to trace credential flow without surprises.
- **Operational cost.** Must not make local dev setup impossible.

---

## Considered Options

### Option 1: Env-var / vault fetch at connect; zero after logon; no read-back (chosen)

**Description:**
1. `Harness.connect()` reads `HARNESS_PASSWORD` from the environment at the moment it needs it.
2. Passes the value to the transport logon path.
3. Immediately after logon returns, sets the reference on `HarnessConfig` to `None`.
4. No method on Harness or HarnessConfig returns the credential.

**Pros:**
- Shortest possible lifetime.
- Zero accessor surface.
- Matches the "fetched, not stored" principle.
- Future vault integration is a resolver swap behind the same interface.

**Cons:**
- Credential clearing in Python is best-effort (GC timing).
- Env-var reads at connect couple Harness implementation to the env-var name; documented, not hidden.
- Re-connecting after disconnect requires a fresh env var read; the first connect cannot "remember" the value for a later reconnect.

### Option 2: Keep in memory for session; rely on `__repr__` redaction

**Description:** Credential stored on HarnessConfig for the session. Redaction handled at every egress point.

**Pros:**
- Simple to reconnect.
- No re-fetch logic.

**Cons:**
- Longer lifetime → larger attack surface.
- Defensive redaction everywhere is error-prone; a future diagnostic feature that forgets to apply it is a leak.
- Fails the "bounded lifetime" goal.

### Option 3: Plain config with no protection

**Description:** Treat the password like any other string field.

**Pros:**
- None.

**Cons:**
- Ruled out on every regulatory and security ground.

### Option 4: External secret provider with a callable

**Description:** HarnessConfig takes a callable `password_provider: Callable[[], str]`. Harness calls it at connect.

**Pros:**
- Pluggable; easy to wire vault integrations.
- Harness never sees the secret until logon.

**Cons:**
- More complex for the default case (env var).
- Caller has to write the provider function.

---

## Decision

**Chosen Option:** Option 1 — env-var / vault fetch at connect, zero after logon, no read-back. Option 4 is a natural extension later (the env-var read becomes the default provider).

### Rationale

- Bounds the lifetime without adding complexity to the default case.
- Zero read-back surface means no accidental leakage in a future helper method.
- Future vault integration slots in via Option 4 as an extension, not a rewrite.
- Option 2 trades safety for reconnect convenience, which we do not need (suites are session-scoped; reconnect-mid-session is a [ADR-0007 Harness failure recovery](0007-harness-failure-recovery.md) concern, and that ADR rebuilds the Harness fresh, pulling credentials fresh).

---

## Consequences

### Positive

- Credential lifetime bounded to the logon handshake.
- No method surface from which to exfiltrate secrets.
- Redaction is enforced at source (never on Harness), not as a defensive afterthought at every log / repr point.
- Rebuild path in [ADR-0007](0007-harness-failure-recovery.md) pulls fresh credentials, so a recovered Harness does not reuse stale in-memory values.
- Future vault integration is a drop-in replacement for the env-var read.

### Negative

- Reconnect after an intentional disconnect requires re-populating the env var; rare in practice but worth noting.
- Python GC does not guarantee zeroisation of string memory. A process dumper could still find the secret in a memory image shortly after logon. Documented limitation.
- Local dev requires the env var set; onboarding docs must flag this.

### Neutral

- `HarnessConfig.password` becomes mutable (cleared to `None` after connect). Dataclass semantics allow this; the field is `repr=False` already.
- `Harness.connect()` grows a small but security-critical block that reads + clears the credential. Must be reviewed with that lens.
- Tests that check `config.password` before connect see the original value (if they set it); after `await harness.connect()`, `config.password is None`.

### Security Considerations

Primary ADR for this subject.

- **Lifetime:** from the beginning of `Harness.connect()` to the first line after successful logon. Bounded to milliseconds when running against an in-memory mock (password irrelevant) and to the logon round-trip when running against a real transport.
- **Memory zeroing limits:** CPython does not provide a safe way to overwrite a string in place. Setting the reference to `None` drops the last strong reference; GC frees the bytes when it runs. A short window exists where the value could be recovered from a core dump. We document this, do not pretend otherwise, and rely on OS-level controls (no-core-dump flags in prod-adjacent CI).
- **No read-back:** `Harness` exposes no `get_password()` or equivalent. `HarnessConfig.password` is a field, not a property; reading after clear returns `None`.
- **`__repr__`:** `field(default=None, repr=False)` on HarnessConfig omits it from the dataclass repr. `Harness.__repr__` does not include the config at all, only its non-sensitive fields.
- **`__reduce__`:** `Harness.__reduce__` raises TypeError unconditionally (in code today). Prevents pickling into debug artefacts.
- **`__getstate__`:** not implemented; `__reduce__` is the authoritative block.
- **Log events:** the structured logger never writes `password` or similar named fields, per [ADR-0009](0009-log-data-classification.md).
- **Diagnostic snapshots** (from [ADR-0007](0007-harness-failure-recovery.md)): deliberately omit any HarnessConfig fields named `*_password`, `*_secret`, `*_token`, `*_key`.
- **CI never sets a prod password.** Only `dev` and `ci` environments have defined identities; their passwords are test-only and registered in the environment allowlist file. A prod password cannot land in CI via this ADR's mechanism because no prod endpoint is allowlisted ([ADR-0006](0006-environment-boundary-enforcement.md)).
- **Secondary credentials** (TLS client cert, SDK licence): same policy. Cert paths in config, cert content fetched and used immediately, reference dropped post-logon.

---

## Implementation

1. **`HarnessConfig.password`:** unchanged — already `field(default=None, repr=False)` and excluded from custom `Harness.__repr__`.
2. **`Harness.connect()`** gains a credential block:
   ```
   password = self._config.password
   if password is None:
       password = os.environ.get("HARNESS_PASSWORD")
   try:
       await self._establish_transport_session(password)
   finally:
       password = None
       self._config.password = None
   ```
3. **Extension point:** the credential source is a `SecretResolver` callable. Default is env-var; vault integrations register their own resolver. Resolver returns `str | None`.
4. **Logging:** framework logger's default format strips any key matching `*_password`, `*_secret`, `*_token`, `*_key` from structured events. Enforced in the log event builder.
5. **Diagnostic snapshot** ([ADR-0007](0007-harness-failure-recovery.md)) reuses the same key allowlist-strip.
6. **Unit tests added:**
   - `test_the_harness_config_password_should_be_none_after_connect` — verify the clear.
   - `test_the_harness_repr_should_not_mention_password` — already exists; tighten.
   - `test_structured_log_events_should_strip_password_keys` — once the log-event builder lands.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (when the first real-transport flow lands): credential block in `Harness.connect()` + unit tests for the clear.
- Phase 2 (when structured logging lands): log-event stripping rule.
- Phase 3 (if / when vault integration is needed): swap env-var resolver for a pluggable `SecretResolver`.

---

## Validation

### Success Metrics

- **Post-connect clear:** `assert config.password is None` after a successful connect. Framework-internal test.
- **Pickle refusal:** `pickle.dumps(harness)` raises. Already tested.
- **Repr refusal:** `"s3cret" not in repr(harness)`. Already tested.
- **Log scan (from [ADR-0009](0009-log-data-classification.md)):** sensitive-field regex (`*_password`, `*_secret`) hits zero times in CI logs.
- **Zero leaked credentials** in any snapshot artefact from [ADR-0007](0007-harness-failure-recovery.md).

### Monitoring

- Scheduled CI job grep: scan captured log events for the literal patterns `"password"`, `"secret"`, `"password":"`, etc. Any match is an incident.
- PR-gate check: framework files matching `packages/core/src/core/**/*.py` must not contain any hardcoded `"password"` or `"secret"` literal values (except in tests where the value is deliberately a sentinel like `"s3cret-do-not-log"`).

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single Harness (this ADR unblocks it, alongside [ADR-0007](0007-harness-failure-recovery.md)).
- [ADR-0006](0006-environment-boundary-enforcement.md) — Environment boundary (no prod identities → no prod passwords).
- [ADR-0007](0007-harness-failure-recovery.md) — Recovery (rebuilds fetch credentials fresh).
- [ADR-0008](0008-um-sdk-trust-boundary.md) — SDK trust boundary (the SDK cannot exfiltrate what is no longer in memory).
- [ADR-0009](0009-log-data-classification.md) — Log classification (this ADR's log-key allowlist is enforced there).

---

## References

- Python docs: dataclasses field metadata — https://docs.python.org/3/library/dataclasses.html
- Python docs: pickle security — https://docs.python.org/3/library/pickle.html#restricting-globals
- HashiCorp Vault / AWS Secrets Manager reference architectures (environment-dependent).
- OWASP ASVS V2 "Authentication Architectural Requirements" — baseline for authentication-credential handling.

---

## Notes

- **Open follow-up — vault integration.** `SecretResolver` extension point is defined but not implemented. Most clients' operational environment will want HashiCorp Vault or equivalent. Needs a separate small ADR (supersede extension) when the concrete integration is chosen. **Owner:** Platform + Security, when real-transport flows land.
- **Open follow-up — credential rotation.** A session-scoped Harness cannot handle a credential rotation mid-session. For tests that exceed a rotation window (rare, hour-long integration runs), the Harness's [ADR-0007](0007-harness-failure-recovery.md) rebuild path suffices: rebuild pulls fresh credentials. **Owner:** Platform.
- **Open follow-up — HSM-backed credentials.** Certificates from a hardware security module have different lifecycle than password strings. Defer decision until a concrete HSM use case appears. **Owner:** Platform + Security.
- **Python memory zeroisation** is a known limitation, not a bug. For a test harness running against non-prod endpoints, it's an acceptable residual risk. For any harness that touches prod-adjacent systems, the operator must also configure the process to disable core dumps and limit memory snapshots.

**Last Updated:** 2026-04-17
