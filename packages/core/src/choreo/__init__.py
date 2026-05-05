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

Multi-transport scenarios (ADR-0027 / PRD-011) — opt-in:

    from choreo import Stage, MappedBridge, IdentityBridge, CorrelationBridge
    from choreo import (
        StageError,
        StageStateError,
        StageConnectError,
        StageDisconnectError,
        MissingTransportError,
        UnknownTransportError,
        BridgeAmbiguityError,
        BridgeTransportMismatchError,
        BridgeTranslationError,
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
from .stage import (
    BridgeAmbiguityError,
    BridgeTranslationError,
    BridgeTransportMismatchError,
    CorrelationBridge,
    IdentityBridge,
    MappedBridge,
    MissingTransportError,
    Stage,
    StageConnectError,
    StageDisconnectError,
    StageError,
    StageReplyReport,
    StageReplyState,
    StageScenarioResult,
    StageStateError,
    UnknownTransportError,
)

# Bundle contract exported for consumer-side bundle type-checking.
# A bundle is a plain function that takes a Scenario and registers replies
# on it. Consumers annotating against `BundleFn` opt into mypy-strict
# compatibility with the framework's reply API.
BundleFn: TypeAlias = Callable[[Scenario], None]


__all__ = [
    "BridgeAmbiguityError",
    "BridgeTranslationError",
    "BridgeTransportMismatchError",
    "BundleFn",
    "CorrelationBridge",
    "CorrelationIdNotInNamespaceError",
    "CorrelationPolicy",
    "CorrelationPolicyError",
    "DictFieldPolicy",
    "Envelope",
    "Harness",
    "IdentityBridge",
    "MappedBridge",
    "MissingTransportError",
    "NoCorrelationPolicy",
    "Scenario",
    "Stage",
    "StageConnectError",
    "StageDisconnectError",
    "StageError",
    "StageReplyReport",
    "StageReplyState",
    "StageScenarioResult",
    "StageStateError",
    "UnknownTransportError",
    "test_namespace",
]
