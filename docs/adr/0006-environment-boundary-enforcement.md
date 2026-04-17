# 0006. Environment-Boundary Enforcement at `Harness.connect()`

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure
**Technical Story:** [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md) — prod-connection guard requirement

---

## Context

Choreo connects to long-lived transports (LBM, NATS, Kafka, RabbitMQ) in-process. Without a hard boundary between test and production environments, a developer with a stale config, a typo in a client identity, or an environment variable copied from another machine can attach this harness to a staging or production bus. In regulated contexts, a message emitted from a test harness onto a production session can be a reportable event.

The harness must refuse to start when the target bus, resolver, or client identity falls outside an allowlist for the declared environment. The guard must be explicit, fail loudly, and fire before any socket is opened.

This ADR is required to move [ADR-0001](0001-single-session-scoped-harness.md) from Proposed to Accepted.

### Background

- [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md) item 1 calls for an explicit environment token and per-environment host allowlists.
- [context.md §8](../context.md) describes the Docker Compose CI topology: `lbmrd:15380`, Docker bridge network `lbm-test`. These are the allowlisted test targets.
- Production transport endpoints are not referenced anywhere in the harness's documentation by design — making them unknown to the codebase keeps them unusable even by accident.
- Many organisations run "UAT isolated" environments that mimic production but are physically separated; tests targeting UAT-isolated are legitimate and must be supported.

### Problem Statement

How does `Harness.connect()` verify that every configured transport endpoint (LBM resolver, NATS server, Kafka broker, message-broker identity) belongs to a safe test environment before opening any socket — and how does it respond when the target is unsafe or ambiguous?

### Goals

- `Harness.connect()` refuses to open any socket unless an explicit `environment` token is provided and it matches a small allowlist (`dev`, `ci`, `uat-isolated`).
- For the declared environment, every configured transport endpoint and client identity must appear in a per-environment allowlist (loaded from a YAML config).
- Failure mode: `Harness.connect()` raises a specific exception naming the offending field and the allowlist it violated. No socket is opened. No retry with a different config.
- Every outbound correlation ID is prefixed `TEST-<environment>-` so downstream systems can filter-and-reject test messages at their boundary if they choose.

### Non-Goals

- Network-layer isolation. The guard prevents **accidental** connection, not a determined attacker with network access. Production firewalls, VPCs, and ACLs are the primary defence and are not in scope.
- Production access control. The harness has no business knowing production endpoints; there is no "production mode". Supersedes any request for a `--allow-production` flag.
- Preventing direct socket creation. A test that imports `socket` and opens a raw connection is out of scope. The guard covers the harness's documented API.
- Full-stack UAT tests with real external backends. Those are gated through a separate allowlist with its own human-in-the-loop review; the `uat-isolated` environment name is reserved for a physically isolated UAT, not for "UAT that happens to be available".

---

## Decision Drivers

- **Regulatory risk.** A single accidental production message on a live session is materially worse than any amount of test slowness.
- **Developer safety.** A tired engineer should not be able to accidentally connect to prod regardless of what is in their environment.
- **Reversibility.** The guard is easy to add later but hard to retrofit — every call site of `Harness.connect()` would need updating. Make it mandatory from day one.
- **Auditability.** The guard's behaviour must be visible in logs and reproducible in CI. When it fires, the incident must be explainable.
- **Minimise trust in config.** A malicious or truncated YAML file should not silently widen the allowlist.

---

## Considered Options

### Option 1: Explicit environment parameter + per-environment allowlist YAML

**Description:** `Harness.connect(environment=..., config=...)` requires an `environment` parameter of type `Environment` (enum: `DEV`, `CI`, `UAT_ISOLATED`). A single YAML file `environments.yaml` declares, per environment, the allowlisted transport endpoints and client-identity patterns. `connect()` validates every field in `config` against the environment's allowlist before opening any socket. The YAML is validated at load time against a strict schema; unknown environments are rejected.

**Pros:**
- Explicit at the call site: you cannot accidentally omit the environment.
- Config in version control; changes go through PR review.
- Single source of truth for what each environment allows.
- Schema-validated config catches typos (e.g. "ci" vs "Ci") at load time.
- Adding a new allowed host is a visible PR diff.

**Cons:**
- Requires every `Harness.connect()` call site to pass the environment. Deliberate — part of the design.
- YAML file must be kept in sync with CI / Docker Compose topology. Mitigated by a CI check that the compose file's hostnames appear in `environments.yaml` for the `ci` environment.

### Option 2: Environment inferred from env var only

**Description:** Read `HARNESS_ENV` at Harness creation. Reject if missing or not in allowlist.

**Pros:**
- Less verbose at call sites.
- Centralised configuration.

**Cons:**
- Environment variables leak across projects and shells. A developer with `HARNESS_ENV=ci` set from a previous task and a local `.lbm.cfg` pointing at prod would pass the env check and still connect to prod.
- Call-site code gives no signal about which environment is in use. Code review cannot spot the intent.
- No explicit parameter means the guard can be forgotten in new Harness instantiation code paths.
- Weaker than Option 1 on every axis except line count.

### Option 3: Hostname-pattern-based refusal

**Description:** Build a deny-list of production hostname patterns (`*.prod.example.internal`, etc.). `Harness.connect()` refuses if the resolver / transport host matches.

**Pros:**
- No allowlist maintenance.
- Passes unknown hosts by default.

**Cons:**
- Allows connection to any non-denied host, including a developer's laptop emulating prod or a new prod cluster with a new hostname pattern.
- "Default-allow" is the wrong posture for a messaging-platform test harness.
- Deny-list maintenance is still required and is less safe than allowlist maintenance.
- Ruled out on security grounds.

### Option 4: No guard; rely on network isolation

**Description:** CI runners and dev machines are on networks with no route to production. That is the only safeguard.

**Pros:**
- Nothing to build.

**Cons:**
- Not true today for every developer machine (VPN-connected laptops, office networks).
- Zero visibility when the assumption breaks.
- A single firewall rule change or DNS poisoning turns every test into a potential production event.
- Ruled out: defence in depth requires both network isolation and application-level guards.

---

## Decision

**Chosen Option:** Option 1 — Explicit environment parameter with per-environment allowlist YAML.

### Rationale

- Default-deny posture is the only acceptable default for a messaging-platform harness. Option 3's default-allow is ruled out on regulatory grounds; Option 4 is defence by optimism.
- Explicit `environment` parameter at the call site makes the intent visible in code review and prevents forgotten-guard regressions.
- Schema-validated YAML config is version-controlled, diff-reviewable, and catches typos at load time.
- The YAML can be extended (new environment, new allowlist entry) without changing the Harness code.
- Option 2 (env var) is strictly weaker — pairs well with Option 1 as a **default source** for the `environment` parameter in pytest fixtures, but does not replace it.

---

## Consequences

### Positive

- Accidental connection to production becomes structurally impossible via the documented API.
- Every `Harness.connect()` call site explicitly declares its environment — intent is visible in diffs and code review.
- Adding a new test environment (e.g. a second isolated UAT cluster) is a single YAML edit plus PR review.
- The `TEST-<environment>-` correlation ID prefix gives downstream systems a second-line filter they can use to reject test traffic at their boundary.
- When the guard fires, the exception names the field and the allowlist — diagnostic is immediate.

### Negative

- Every test fixture must supply an `environment` value. Mitigated by a `conftest.py` session fixture that reads `HARNESS_ENV` with no default and fails loudly if unset.
- `environments.yaml` is an operational file that must stay synchronised with the CI topology. A CI check validates the compose file's hostnames against the YAML's `ci` section on every PR.
- Legitimate tests against new infrastructure require a YAML change before they can run. Acceptable — deliberate friction at the environment boundary is the feature.

### Neutral

- The allowlist YAML lives at `config/environments.yaml` alongside other harness config. Not inside the `core` package — it is deployment data, not library code.
- The `Environment` enum has three values today; adding more is a minor version bump of the harness.

### Security Considerations

Primary ADR for this subject. The guard's security properties:

1. **Default-deny.** Any host, identity, or resolver not explicitly allowlisted is refused. No `--unsafe-allow-any` flag exists.
2. **Schema-validated config.** The YAML is validated at load time against a strict Pydantic / schema model; malformed or truncated files raise before `Harness.connect()` proceeds.
3. **No production endpoint in the codebase or repo.** Production transport hosts do not appear anywhere in the harness repo, including in `environments.yaml`. Keeping them unknown to the codebase is a defence layer.
4. **Correlation ID prefixing.** Every outbound correlation carries `TEST-<environment>-<uuid>` so downstream systems can reject test traffic at their ingress.
5. **Auditability.** Every refusal logs a structured event at WARNING with the rejected field and the allowlist consulted. CI retains these logs per its standard retention.
6. **No config override via env vars.** The YAML is the only source. `HARNESS_ENV` selects which section of the YAML applies; it does not add or remove entries.
7. **CompID hygiene.** The allowlist distinguishes `SenderCompID` and `TargetCompID` separately — a prod CompID will not be accepted as a target even if a test CompID with the same name exists.

---

## Implementation

1. New module `core.environment` with:
   - `class Environment(StrEnum)` — values `DEV = "dev"`, `CI = "ci"`, `UAT_ISOLATED = "uat-isolated"`.
   - `class EnvironmentError(Exception)` with subclasses `EnvironmentNotAllowed`, `HostNotInAllowlist`, `CompIdNotInAllowlist`, `AllowlistConfigError`.
   - `load_allowlist(path: Path) -> EnvironmentAllowlist` — schema-validated loader.
2. `Harness.__init__(config: HarnessConfig)` stores config; does nothing network-touching.
3. `Harness.connect()`:
   - Loads `environments.yaml` (path resolved from env var `HARNESS_ALLOWLIST_PATH` or a package-local default).
   - Looks up the environment section; raises `EnvironmentNotAllowed` if missing.
   - Validates every `HarnessConfig` field against the allowlist. First failure raises the specific subclass with the offending field and value.
   - Generates a correlation-ID prefix `TEST-{environment.value}-` and stores it on the Harness for later injection.
   - Only after all checks pass does it open transports.
4. `environments.yaml` lives at `config/environments.yaml`:

   ```yaml
   dev:
     lbm_resolvers: ["localhost:15380", "127.0.0.1:15380"]
     fix_acceptor_hosts: ["localhost", "127.0.0.1"]
     sender_comp_ids: ["HARNESS", "VENUE_MOCK", "CLIENT_MOCK"]
     target_comp_ids: ["ADAPTER", "FIX_ORDER_ROUTING"]
   ci:
     lbm_resolvers: ["lbmrd:15380"]
     fix_acceptor_hosts: ["service-under-test", "test-harness", "lbmrd"]
     sender_comp_ids: ["HARNESS", "VENUE_MOCK", "CLIENT_MOCK"]
     target_comp_ids: ["ADAPTER", "FIX_ORDER_ROUTING"]
   uat-isolated:
     # populated per-deployment; reviewed by Platform + Security
     lbm_resolvers: []
     fix_acceptor_hosts: []
     sender_comp_ids: []
     target_comp_ids: []
   ```

5. A `conftest.py` session fixture `bus_environment()` reads `HARNESS_ENV` and returns the enum. Missing or unknown value fails the fixture with a specific error pointing at `environments.yaml`.
6. CI check `scripts/check_environments_yaml.py` asserts that every hostname in `docker-compose.ci.yml` appears in `environments.yaml` under the `ci` section. Fails the PR if not.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (with PRD-001 skeleton): implement `Environment`, `EnvironmentError`, `load_allowlist`, and the `Harness.connect()` guard. Populate `environments.yaml` for `dev` and `ci`.
- Phase 2 (before any real-transport tests): populate `uat-isolated` with real values via PR with Platform + Security review.
- Phase 3 (with future CI PRD): wire the `scripts/check_environments_yaml.py` check into the PR pipeline.

---

## Validation

### Success Metrics

- **Accidental-connection incidents:** zero reported in the lifetime of the harness.
- **Guard coverage:** 100% of `Harness.connect()` paths pass through the allowlist check, verified by framework-internal tests that attempt to connect with an unsafe config and assert the specific exception.
- **Config drift:** zero CI failures where `docker-compose.ci.yml` adds a host that isn't in `environments.yaml` (the CI check enforces this before merge).
- **Refusal clarity:** every `EnvironmentError` names the field and the allowlist, verified by test fixtures that assert the exception message shape.

### Monitoring

- CI log scanner flags every `EnvironmentNotAllowed`, `HostNotInAllowlist`, `IdentityNotInAllowlist` at WARNING. Any occurrence in a passing run is a signal of misconfiguration.
- `scripts/check_environments_yaml.py` runs on every PR; failure blocks merge.
- `environments.yaml` changes require two approving reviewers from Platform / Security (enforced via CODEOWNERS).

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single session-scoped Harness (consumer of this guard; this ADR unblocks 0001's move to Accepted).
- Future **ADR-0008** — Transport SDK trust boundary (also gates real-bus integration).
- Future **ADR-0010** — Secret management in the Harness (complementary: guard prevents wrong connection; secrets ADR prevents credential leakage once connected).

---

## References

- [PRD-001 §Goals](../prd/PRD-001-framework-foundations.md) — framework foundations
- [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md)
- [context.md §8](../context.md) — CI Docker Compose topology
- FCA / FCA MAR guidance on inadvertent market actions — general background for why this matters
- WireMock "fault-tolerant default-deny" pattern — analogous approach for HTTP integration testing

---

## Notes

### Correction — library no longer owns the environment concept

The original ADR named an `Environment` enum with three values (`DEV`, `CI`,
`UAT_ISOLATED`) and a multi-section allowlist YAML keyed by those names. The
enum leaked into every construction call site:

```python
# The old shape, now removed
config = HarnessConfig(environment=Environment.CI, lbm_resolver=..., ...)
```

"Which environment is this" is a deployment concern. The consumer — a separate
repo that installs this library — picks it by choosing which allowlist file to
load. The library itself has no business enumerating deployment names or
embedding them in its construction API.

**What changed on 2026-04-17:**

- `Environment` enum removed from the library.
- `HarnessConfig.environment` field removed. Config is `lbm_resolver` +
  `allowlist_path` + optional `fix_*` only.
- Allowlist YAML is now flat (no per-environment sections):

  ```yaml
  lbm_resolvers:       [...]
  fix_acceptor_hosts:  [...]
  sender_comp_ids:     [...]
  target_comp_ids:     [...]
  ```

- One YAML file per deployment. Consumers pick which to load.
- Correlation prefix is now `TEST-` (no environment suffix). The library's
  `correlation_prefix()` returns that literal string.

**What the safety story looks like now:**

- The allowlist guard is unchanged in spirit: `Harness.connect()` validates
  every configured host and CompID against the loaded allowlist, refusing
  with a specific exception if any value is absent. Default-deny.
- Production endpoints still never appear in any allowlist the library ships
  or tests with. Keeping them unknown to the codebase keeps them unusable by
  accident.
- The `TEST-` prefix on every correlation ID still gives downstream systems
  a filter on their ingress. The `<env>` segment was a convenience for
  grepping CI logs; if a consumer wants that, they can extend the prefix in
  their own fixture.

**What didn't change:**

- No production mode exists.
- The guard still fires before any socket is opened.
- Allowlist YAML changes still require Platform + Security review.
- MAR / MiFID II / GDPR posture is unchanged — the safety story is the same,
  the library is just less opinionated about deployment labels.

### Open follow-ups

- **CODEOWNERS for `config/allowlist.yaml` and any derivative files** in
  consumer repos. Needs to be wired up at each deployment. Owner: Platform.
- **Integration with ADR-0008 (SDK trust boundary).** When real-bus
  integration lands, the allowlist may need to also validate UM licence-file
  paths. Owner: Platform / Security.
- **Production readiness.** This ADR does not make the harness safe for
  production deployment under any circumstances. "No production mode" is a
  deliberate design invariant. If a future business need requires it, a
  superseding ADR and substantial additional review are required; this is
  not a config-file change.

The `TEST-` correlation prefix is intentionally visible in plain text for
downstream filtering. It is not a secret and is not used for authentication.
Its purpose is to let SUTs and downstream services recognise test traffic at
their boundary and refuse it — a second line of defence if the first (this
ADR) ever fails.
