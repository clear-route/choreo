"""Correlation ID generation — cryptographically random per ADR-0002."""
from __future__ import annotations

import secrets


_PREFIX = "TEST-"


def generate_correlation_id() -> str:
    """Return a unique, unguessable correlation ID prefixed with `TEST-`.

    Downstream systems can filter test traffic at their boundary by matching
    the prefix (ADR-0006). The suffix is 32 hex chars (~128 bits of entropy)
    — more than enough to defeat echo-of-prior-scope attacks.
    """
    return f"{_PREFIX}{secrets.token_hex(16)}"
