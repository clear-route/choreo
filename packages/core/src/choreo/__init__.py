"""Choreo — public API.

The public surface is small by design:

    from choreo import Harness, BundleFn
    from choreo.transports import MockTransport            # or your own
    from choreo.codecs import JSONCodec, RawCodec, Codec
    from choreo.environment import Allowlist, load_allowlist
    from choreo.matchers import field_equals, all_of, ...
    from choreo.correlation import (
        CorrelationPolicy,
        NoCorrelationPolicy,
        DictFieldPolicy,
        Envelope,
        test_namespace,
    )

Everything else is internal."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

from .correlation import (
    CorrelationIdNotInNamespaceError,
    CorrelationPolicy,
    CorrelationPolicyError,
    DictFieldPolicy,
    Envelope,
    NoCorrelationPolicy,
    test_namespace,
)
from .harness import Harness
from .scenario import Scenario

# Bundle contract exported for consumer-side bundle type-checking.
# A bundle is a plain function that takes a Scenario and registers replies
# on it. Consumers annotating against `BundleFn` opt into mypy-strict
# compatibility with the framework's reply API.
BundleFn: TypeAlias = Callable[[Scenario], None]


__all__ = [
    "BundleFn",
    "CorrelationIdNotInNamespaceError",
    "CorrelationPolicy",
    "CorrelationPolicyError",
    "DictFieldPolicy",
    "Envelope",
    "Harness",
    "NoCorrelationPolicy",
    "Scenario",
    "test_namespace",
]
