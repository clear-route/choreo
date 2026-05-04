"""Report-boundary redaction (PRD-012 §1.5.1).

The framework's in-process `_redact()` (head=8, tail=4, length
annotation; defined in `choreo.stage`) is designed for short-lived
error messages. The on-disk `results.json` boundary uses the
hash-based redaction here: SHA-256 truncated to 16 hex chars,
prefixed `sha256:`. The two are deliberately decoupled: error
messages need to be human-debuggable in the moment; archived
reports need to resist greppable correlation across months of
retention (Chronicle archive).

Single source of truth: the choreo-reporter imports
`redact_correlation_id` from this module rather than
re-implementing.

`REDACTION_VERSION` is surfaced in `run.redactions.redaction_version`
in emitted reports so consumers can detect algorithm changes. A
future tightening of the algorithm bumps the version string.

Naming: this module redacts per-transport correlation ids — the
strings the bridge stamps onto outbound messages so inbound
correlation matching works. The framework also calls these "wire
ids" internally (the bridge's `to_wire` returns one); the
report-boundary terminology is `correlation_id` for consistency
with `Handle.correlation_id`. The legacy `redact_wire_id` alias
is preserved for two minor versions.
"""

from __future__ import annotations

import hashlib

REDACTION_VERSION = "v1"
"""Algorithm version. Bump when the algorithm changes."""

_TRUNCATION_HEX_CHARS = 16
"""Number of hex chars retained from the SHA-256 digest.

16 hex chars = 64 bits. Sufficient to disambiguate within a single
test run (a few thousand correlation ids); insufficient to enable
archive-grep correlation against an external dictionary.
"""


def redact_correlation_id(correlation_id: str) -> str:
    """Hash-redact a per-transport correlation id for inclusion in
    `results.json`.

    Returns a string of the form `sha256:<16 hex chars>`. The
    `sha256:` prefix is a grep target so consumers can identify
    redacted values; the hex tail is the truncated digest of the
    UTF-8 bytes of `correlation_id`.

    Not reversible: an adversary with read access to the archived
    report cannot recover the original id without a brute-force
    search through the key space. For per-run correlation ids
    derived from `secrets.token_hex(16)` this is computationally
    infeasible.

    Deterministic: equal inputs produce equal outputs, so consumers
    can correlate across handles/scenarios within a run via hash
    equality.
    """
    digest = hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:_TRUNCATION_HEX_CHARS]}"


# Legacy alias preserved for two minor versions per the project's
# rename policy. New code should use `redact_correlation_id`.
redact_wire_id = redact_correlation_id
