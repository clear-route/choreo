# 0008. UM SDK Trust Boundary — Pinning, Provenance, Verification

**Status:** Accepted
**Date:** 2026-04-17
**Deciders:** Platform / Test Infrastructure + Security
**Technical Story:** [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md) item 3 — "UM SDK trust boundary"

---

## Context

The harness runs the Informatica Ultra Messaging (UM) SDK Python binding in-process with network access, its own daemon threads, and direct access to the asyncio loop (via the LoopPoster bridge). That binding is a vendor binary — we do not build it, we fetch it.

If the binary we run is not the one the vendor shipped — compromised in transit, substituted in a public package index, replaced in an artefact cache — a test harness operating in a regulated context becomes a code-execution vector with full credentials and network access. The security review flagged this as HIGH; it is one of the blockers before the harness can run against anything beyond a fully-isolated mock bus.

### Background

- The UM SDK is closed-source. Building from source is not an option.
- Vendor distributes wheels via their portal; public PyPI is not a sanctioned distribution channel.
- The repo already pins Python dependencies in `pyproject.toml`; extending that discipline to the SDK is a mechanical change plus a policy commitment.
- [ADR-0001 §Security Considerations](0001-single-session-scoped-harness.md) item 3 named this as a future ADR.

### Problem Statement

How do we ensure the UM SDK binary installed into the harness environment is the one the vendor shipped, from a trusted source, and how do we make provenance visible at every install?

### Goals

- **Pinned version + hash** verified at install time. A different binary at the same version number fails the install, not silently succeeds.
- **Single source of truth** for the binary: an internal artefact repository, not public PyPI, not the vendor portal directly, not a developer's laptop.
- **Reproducible installs.** The same commit hash produces the same installed SDK bytes, everywhere — CI runners, dev machines, UAT containers.
- **Startup visibility.** Every Harness startup records the SDK version + hash for CI log scanning.
- **Deliberate upgrades.** SDK version bumps are PRs, not automated dependency updates.

### Non-Goals

- Building the SDK from source (closed-source product).
- Vendoring the SDK binary in the repo (licence restrictions, binary size, history bloat).
- Protection against a malicious insider with artefact-repo write access. Separate problem handled by artefact-repo access control and audit logging.
- Software Bill of Materials (SBOM) beyond the SDK itself. Other dependencies are pinned via the existing `pyproject.toml` + lock-file discipline.

---

## Decision Drivers

- **Supply-chain integrity.** A regulated-context test harness running a substituted binary is materially worse than any amount of install complexity.
- **Reproducibility.** Two developers on the same commit must have the same binary.
- **Auditability.** When an incident happens, we must be able to answer "what version, what hash, where from, when" from CI logs.
- **Operational cost.** Whatever we do must not make fresh dev-environment setup painful enough that developers bypass it.

---

## Considered Options

### Option 1: Pin version + hash; internal artefact repo only; verify at install (chosen)

**Description:** Declare the exact UM SDK version in `pyproject.toml`. Record its hash in a hash-pinned requirements file. Install via `pip install --require-hashes` from an internal artefact repository (JFrog Artifactory, Nexus, or similar). Startup logs version + hash.

**Pros:**
- Hash verification is a bright-line integrity check; a compromised artefact repo replacing the binary fails the install loudly.
- `--require-hashes` is a stdlib-supported pip feature; no bespoke tooling.
- Internal artefact repo is already the standard for other vendor binaries at the client.
- Version bumps require a PR that updates both version and hash — two changes, both visible.

**Cons:**
- Requires an internal artefact repo with UM SDK wheels mirrored. Operational setup.
- Hash updates on every SDK bump are friction (intentional friction).
- First-time dev setup needs credentials for the artefact repo.

### Option 2: Pin version only; fetch from PyPI; rely on PyPI TLS

**Description:** `pip install "um-sdk==X.Y.Z"` from PyPI with no hash.

**Pros:**
- Simplest.
- No artefact repo to maintain.

**Cons:**
- PyPI index poisoning, dependency-confusion attacks, account takeovers are all real. TLS protects the wire, not the index.
- The vendor does not distribute via PyPI officially.
- Ruled out on supply-chain grounds.

### Option 3: Vendor the binary in the repo

**Description:** Commit the SDK wheel into `vendor/um_sdk/`.

**Pros:**
- No external dependency at install time.
- Immediate reproducibility.

**Cons:**
- Licence almost certainly forbids redistribution. Legal risk.
- Binary size bloats git history; large-file extensions (LFS) complicate clone.
- Upgrades still require a PR that pulls a new binary — same cost as Option 1 with worse legal footing.
- Ruled out.

### Option 4: Trust the network; no pinning

**Description:** Install latest available; hope for the best.

**Pros:**
- None.

**Cons:**
- Zero integrity story.
- Different binaries on different machines from the same commit.
- Ruled out.

---

## Decision

**Chosen Option:** Option 1 — Pin version + hash; internal artefact repo only; verify at install.

### Rationale

- Integrity is a regulatory and operational requirement for a regulated-context test harness. Options 2 and 4 have no integrity story.
- Option 3 is the right integrity story with the wrong licence footing.
- Option 1 uses stdlib pip features (`--require-hashes`), the client's existing artefact repo, and a deliberate-upgrade workflow that matches how the team already handles other vendor dependencies.

---

## Consequences

### Positive

- Hash mismatch = failed install = human intervention; supply-chain substitution cannot pass silently.
- Reproducible installs across dev / CI / UAT.
- SDK provenance is visible in every Harness startup log.
- Upgrades are visible PRs, reviewed by Platform + Security.

### Negative

- Fresh dev-environment setup requires artefact-repo credentials. Mitigated by clear onboarding docs.
- SDK version bumps take longer (PR + review + hash update) than `pip install -U`. Intentional.
- Artefact repo becomes a single point of failure for installs. Mitigated by the repo's own HA (client responsibility) plus a secondary mirror (see Notes).

### Neutral

- `requirements.txt` or equivalent gains a new section pinning the SDK. Visible in every PR that touches installs.
- Dockerfile gains a hash-verify step; adds a few lines, no runtime cost.
- CI gains a dependency-check job that runs weekly and notifies when the pinned version is more than N days old.

### Security Considerations

Primary ADR for this subject.

- **Hash is the bright line.** Version alone is not enough; a compromised artefact repo could replace the bytes at the same version number. Hash verification catches this.
- **No dynamic fetches at runtime.** The SDK binary is installed once, at container build time (CI) or at `pip install` time (dev). The running Harness never fetches code.
- **Licence file under version control.** `config/um-licence.lic` (or equivalent) is checked in; missing-licence startup failure is loud.
- **Startup log line.** `Harness.connect()` logs the SDK version, hash, and licence fingerprint before opening any socket. CI log scanner flags any unexpected hash as a regression against this ADR.
- **Artefact-repo access is role-gated.** Only CI and named dev accounts have read access; writes require Platform + Security approval. Enforced at the repo level, not by this ADR.
- **Secondary mirror.** Optional. If the primary artefact repo is unreachable, CI falls back to a read-only mirror with the same hash-verified content. Mirror is one-way-synced from primary.

---

## Implementation

1. **`pyproject.toml`:** pin exact version — `um-sdk == X.Y.Z` (no range). Mark optional-dep group `lbm` with this pin.
2. **`requirements.lbm.txt`** (hash-pinned, committed): `um-sdk==X.Y.Z --hash=sha256:<hash>` + any transitive dependencies, also hash-pinned.
3. **Install command:** `pip install --require-hashes -r requirements.lbm.txt --index-url <internal-artefact-url>`. Never `--extra-index-url` — that allows PyPI fall-through.
4. **Dockerfile:** single `RUN` that installs with `--require-hashes`; no subsequent `pip install` of anything in the SDK family.
5. **Startup log:** `Harness.connect()` logs one structured event:
   ```
   {event: "um_sdk_loaded", version: "X.Y.Z", hash: "sha256:…",
    licence_fingerprint: "…", timestamp: "…"}
   ```
6. **CI job `verify-um-sdk-provenance`:** runs on every PR, asserts:
   - `requirements.lbm.txt` has a hash for every package.
   - The pinned UM SDK version matches `pyproject.toml`.
   - The index URL is the internal artefact repo, not PyPI.
7. **Scheduled CI job `um-sdk-freshness`:** runs weekly; warns (not fails) if the pinned version is more than 90 days behind the latest available in the artefact repo. Prompts a deliberate upgrade PR.

### Migration Path

Not applicable — greenfield.

### Timeline

- Phase 1 (before any real-bus integration): pin the version, install from artefact repo, add the CI verification job.
- Phase 2 (first PR that imports the SDK for real): add the startup log event.
- Phase 3 (operational): secondary mirror, freshness job, annual review of the pinned version.

---

## Validation

### Success Metrics

- **Zero hash-mismatch incidents in CI.** Any failure is investigated as a possible supply-chain event.
- **Every Harness startup log includes the SDK provenance event.** Verified by log-scanner rule.
- **Fresh dev environment works from README instructions.** Annual onboarding audit.
- **SDK version bumps are PRs with two approving reviewers** (Platform + Security via CODEOWNERS).

### Monitoring

- CI log scanner: flag any `um_sdk_loaded` event with an unexpected hash as a regression.
- CI log scanner: flag any install failure with `--require-hashes` error as a possible supply-chain event; Platform + Security paged.
- Artefact repo access logs archived separately; reviewed on request by Security.

---

## Related Decisions

- [ADR-0001](0001-single-session-scoped-harness.md) — Single Harness (this ADR unblocks it).
- [ADR-0006](0006-environment-boundary-enforcement.md) — Environment boundary (complementary: boundary prevents wrong host; this ADR prevents wrong binary).
- [ADR-0010](0010-secret-management-in-harness.md) — Credential lifecycle (the licence-fingerprint idea here mirrors the secret-redaction idea there).

---

## References

- pip docs: [`--require-hashes`](https://pip.pypa.io/en/stable/topics/secure-installs/#secure-installs)
- [framework-design.md §7](../framework-design.md) — Python engine choices (the SDK pin complements the library pin story).
- Client's internal artefact-repo onboarding docs (environment-dependent).

---

## Notes

- **Open follow-up — SBOM beyond the SDK.** Lock-file hash-pinning is in place for Python dependencies via the existing pyproject workflow. A formal SBOM export (CycloneDX or SPDX) for regulatory reporting is a separate piece of work. **Owner:** Platform.
- **Open follow-up — licence rotation.** The UM licence file expires annually. An automation that notifies 60 days before expiry is a small script; worth writing. **Owner:** Platform.
- **Open follow-up — secondary mirror.** Mentioned as optional in Consequences. Whether we need it depends on how reliable the primary artefact repo is in practice. Defer decision until we have 3 months of operational data. **Owner:** Platform.
- **Hash rotation.** If the vendor ever re-issues a wheel at the same version with a different hash, we treat it as a new version and bump. We do not quietly update the hash in place.

**Last Updated:** 2026-04-17
