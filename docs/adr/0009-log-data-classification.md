# 0009. Log and Surprise-Log Data Classification and Redaction

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure + Security
**Technical Story:** [ADR-0004 §Security Considerations](0004-dispatcher-correlation-mediator.md) item 4 — "Surprise-log redaction"

---

## Context

The Dispatcher's **surprise log** records every inbound message it could not route. In UK-regulated Fixed Income, inbound payloads routinely carry MiFID II / MAR-sensitive data: ISINs, client LEIs, short codes, decision-maker identifiers, P&L, counterparty details. CI log stores (GitHub Actions, Jenkins, GitLab) are commonly accessible to far more people than production trade data ever should be. A surprise-log entry that captures a payload turns the test harness into a silent exfiltration channel — and the exfiltration happens exactly when something unexpected goes wrong, i.e. when the most useful payloads are in-flight.

The security review flagged this HIGH. The Dispatcher implementation already excludes the payload field from `SurpriseEntry` ([ADR-0004 §Implementation](0004-dispatcher-correlation-mediator.md) item 1). This ADR formalises the policy across every log sink in the framework and defines how opt-in to full capture is gated.

### Background

- [ADR-0004 §Security Considerations](0004-dispatcher-correlation-mediator.md) item 4 specifies the default: no payload in surprise log.
- UK MAR (Market Abuse Regulation) requires firms to supervise trade data and restrict access.
- MiFID II mandates specific field-level controls on LEI and short-code data.
- GDPR applies whenever a natural person's LEI or identifier appears in a payload.
- CI logs typically have looser access controls than production trade data. A sensitive field in a CI log is a governance issue even if the CI system itself is "internal".

### Problem Statement

What does the harness write into logs, and under what circumstances? How do we prevent payload content reaching CI logs by default, while still letting operators capture enough detail to debug rare issues?

### Goals

- **Default-redact, always.** Every structured log the framework emits includes only metadata unless an explicit opt-in is active.
- **Explicit opt-in, environment-gated.** Full-payload capture requires an opt-in flag AND is refused in `ci` and `uat-isolated` environments.
- **Per-topic classifier for safe fields.** Topics known to carry non-sensitive data (test fixtures, internal heartbeats) can register a whitelist of payload fields that may appear in logs.
- **Single place to change policy.** Classifier registry lives with the framework; service teams own their topic entries via PR review.
- **Auditable.** When full capture was on, when and by whom must be clear from logs and PR history.

### Non-Goals

- Encryption at rest. Handled by the CI log store / SIEM, not by this ADR.
- Classification of test fixtures. Fixtures in `tests/fixtures/` are test data, not production data — out of scope.
- Classification of log entries produced by third-party libraries (transport SDKs, client drivers). Those are managed at the logger level; this ADR covers the framework's own log events.
- Runtime field-level encryption or masking. Too heavyweight for a test harness; default-redact at source is simpler.

---

## Decision Drivers

- **Regulatory.** MiFID II, MAR, GDPR all apply; the cost of a data-exfiltration incident dwarfs any debugging convenience.
- **Default-safe.** The default path must be the safe path. A missing classifier should redact, not expose.
- **Debuggability.** Operators need enough detail to diagnose issues without opt-in being required every time.
- **Simplicity.** A policy that's too complex to follow gets bypassed.

---

## Considered Options

### Option 1: Default-redact with per-topic classifier for opt-in (chosen)

**Description:** Log entries by default include topic, correlation ID, classification, size, timestamp, source transport. No payload content. For topics known to be safe, a classifier can be registered that names specific payload fields to include in logs. Full-payload capture requires a global env-var opt-in, refused in `ci` / `uat-isolated`.

**Pros:**
- Default-safe: no classifier → no payload content.
- Opt-in is explicit and auditable (env var + PR to register classifier).
- Per-topic classifier lets the team unlock useful fields without blanket exposure.
- Maps cleanly onto the existing environment-guard model (ADR-0006).

**Cons:**
- Three levels to reason about (default redact, classifier opt-in, full capture opt-in) — more complex than "always redact".
- Classifier registry is a second source of truth alongside the extractor registry (ADR-0004).

### Option 2: Full capture, redact on log shipping

**Description:** Capture full payloads into logs; a log-shipping middleware redacts sensitive fields on the way to the CI log store.

**Pros:**
- No in-process redaction code.
- Full payloads available in local development.

**Cons:**
- Redaction happens at the wrong layer. Any framework crash / direct log dump / operator tail exposes payloads.
- Requires infrastructure complexity (shipping rules, per-field regex).
- Fails the default-safe test.
- Ruled out.

### Option 3: No logs at all

**Description:** Don't log anything that could carry sensitive data.

**Pros:**
- Trivially safe.

**Cons:**
- No debuggability. An unmatched-inbound bug becomes unreproducible.
- Ruled out.

### Option 4: Classify at the source — extractor registry declares sensitivity

**Description:** Extend the ADR-0004 extractor registry so each extractor also declares what fields are safe. Reuse that metadata for logging.

**Pros:**
- Single source of truth with ADR-0004.

**Cons:**
- Conflates two concerns (routing correlation extraction vs log classification).
- Makes the extractor registry harder to reason about.
- An extractor might need the `symbol` field to route; that doesn't mean `symbol` is safe to log.

---

## Decision

**Chosen Option:** Option 1 — Default-redact with per-topic classifier for opt-in and env-var-gated full capture.

### Rationale

- Default-safe is non-negotiable for regulated data.
- Per-topic classifier gives operators the debugging detail they need without a blanket exposure.
- Env-var opt-in for full capture, refused in CI environments, matches the existing ADR-0006 environment-guard posture (opt-in explicit, CI default-deny).
- Option 2 fails the default-safe principle. Option 3 is too strict. Option 4 conflates concerns.

---

## Consequences

### Positive

- Framework logs by default contain no payload content. No audit risk from default operation.
- Topics with safe fields (heartbeats, session status) can be logged in full without repo-wide policy changes.
- Full capture remains possible for rare deep debugging (local dev, explicit investigation) via a single env var.
- CI logs scanned for sensitive patterns should return zero hits — that's a testable invariant.

### Negative

- Three levels of log behaviour — operators must understand which one they're in. Doc + onboarding cost.
- Classifier registry requires discipline: every new topic the framework subscribes to needs a classifier registered (or it stays redacted). PR review catches omissions.
- Full-capture mode is a footgun; must be fenced by CI checks.

### Neutral

- Log volume stays roughly the same — metadata is small, classifier-selected fields are small.
- Structured logs (JSON) are unchanged in shape; fields are present-or-absent based on classifier.
- Existing `SurpriseEntry` from [ADR-0004 §Implementation](0004-dispatcher-correlation-mediator.md) already matches the default-redact format.

### Security Considerations

Primary ADR for this subject.

- **Default-redact covers the base case.** A misconfigured framework logs zero payload content. A missing classifier logs zero payload content.
- **Env var to enable full capture:** `HARNESS_UNSAFE_FULL_CAPTURE=1`. Name chosen to be obviously dangerous.
- **CI refuses full capture:** `Harness.connect()` asserts `not (env == CI and unsafe_full_capture)`. Violation raises `EnvironmentError` with a specific message naming the env var and the environment.
- **UAT-isolated also refuses full capture.** UAT-isolated is by definition production-like; same posture as CI.
- **Only `dev` permits full capture.** A developer on their own machine debugging a local issue.
- **Classifier registry is allowlist, not denylist.** Unknown fields are dropped; only named fields appear in logs.
- **Classifier changes require Platform + Security review** via CODEOWNERS on the classifier file.
- **Structured log events for audit:** enabling full capture emits `{event: "unsafe_full_capture_enabled", environment: "dev", timestamp: "…"}` — visible in operator logs so the mode is never silent.
- **Retention policy inherits from CI.** This ADR does not re-specify log retention; CI log store rules apply. But: if CI retention is relaxed in future, this ADR's default-redact means no regulatory exposure from the framework's side.
- **Exceptions in log formatters must not leak content.** If a classifier raises on a malformed payload, the log entry records the exception type and class only, not the stringified payload.

---

## Implementation

1. **`core._internal.log_classifier` module** introduces:
   - `class LogClassifier(Protocol)`: `classify(topic, payload) -> dict[str, Any]` returning the safe-to-log fields.
   - `MetadataOnlyClassifier` (default): always returns `{}`.
   - `register_classifier(topic, classifier)`: overrides for specific topics.
2. **`SurpriseEntry` unchanged** — already conforms to the default-redact shape.
3. **Structured logger wrapper:** every framework log call routes through `log_event(topic, payload, **metadata)` which applies the registered classifier before emitting.
4. **Env-var check:** `Harness.connect()` reads `HARNESS_UNSAFE_FULL_CAPTURE`. If truthy and `environment in (CI, UAT_ISOLATED)`, raises `EnvironmentError`. If truthy and `environment == DEV`, enables full-capture mode and emits the audit event.
5. **CI job `verify-no-full-capture-in-ci`:** scans GitHub Actions / GitLab / Jenkins workflow files for `HARNESS_UNSAFE_FULL_CAPTURE=1`. Fails the PR if found.
6. **Log content scan job (scheduled):** runs weekly; greps CI logs for ISIN patterns (`[A-Z]{2}[A-Z0-9]{9}[0-9]`), LEI patterns (20-char alphanumeric), and a configurable list of client short codes. Any match is a regression against this ADR.

### Migration Path

Not applicable — greenfield. The Dispatcher's `SurpriseEntry` already matches the default.

### Timeline

- Phase 1 (before first real-bus integration): `LogClassifier` protocol + default `MetadataOnlyClassifier` + env-var check.
- Phase 2 (per-service): service teams register classifiers for their topics; PRs reviewed by Platform + Security.
- Phase 3 (operational): CI log scan job; annual audit of registered classifiers.

---

## Validation

### Success Metrics

- **Zero sensitive-pattern hits in weekly CI log scan.** ISIN / LEI / client-short-code patterns should never appear.
- **100% of framework log events pass through `log_event()`.** Enforced by an AST CI check that bans direct `logger.info/warning/error` calls in framework modules in favour of the classifier-aware wrapper.
- **Full-capture env var is never set in any CI workflow file.** Enforced by `verify-no-full-capture-in-ci`.
- **Every classifier registration is a visible PR diff** reviewed by CODEOWNERS.

### Monitoring

- CI log scan job results archived monthly; trend reviewed by Security.
- Any `unsafe_full_capture_enabled` event reaching a non-`dev` log store is an immediate incident.
- Counter: surprise-log entries per session. A spike indicates either a routing bug or a schema drift; both are investigable without payload content.

---

## Related Decisions

- [ADR-0004](0004-dispatcher-correlation-mediator.md) — Dispatcher + SurpriseEntry (this ADR formalises what that ADR implemented).
- [ADR-0006](0006-environment-boundary-enforcement.md) — Environment guard (same env-gated opt-in pattern).
- [ADR-0007](0007-harness-failure-recovery.md) — Recovery diagnostics (snapshots also follow this classification).
- [ADR-0010](0010-secret-management-in-harness.md) — Credentials (related but separate surface).

---

## References

- MiFID II RTS 22 (transaction reporting) — field-level requirements.
- MAR (EU 596/2014) — supervision and access control.
- GDPR Art 32 — data protection controls.
- [framework-design.md §10](../framework-design.md) — Cross-cutting patterns, including the observability hooks this ADR uses.

---

## Notes

- **Open follow-up — classifier format.** Should classifiers return dicts (field → value) or a redacted payload (string)? Dict is more structured for JSON logging; string is simpler. Default: dict. Revisit if operational experience shows a different need. **Owner:** Platform.
- **Open follow-up — what constitutes a "safe" field per topic.** The policy of WHICH fields are safe per topic sits with the service team that owns the topic, not with framework authors. Needs a documented review process and a template classifier. **Owner:** Platform + Security + affected service teams.
- **Open follow-up — GDPR data subject rights.** If a client requests deletion of their identifiers from logs (GDPR Art 17), and that identifier happens to appear in a CI log (because of a pre-classifier deployment, or a classifier bug), what's the deletion process? Tracked separately with Legal / DPO. **Owner:** Legal / DPO.

**Last Updated:** 2026-04-17
