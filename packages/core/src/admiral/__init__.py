"""Admiral — public API.

The public surface is small by design:

    from admiral import Harness, BundleFn
    from admiral.transports import MockTransport            # or your own
    from admiral.codecs import JSONCodec, RawCodec, Codec
    from admiral.environment import Allowlist, load_allowlist
    from admiral.matchers import field_equals, all_of, ...
    from admiral.correlation import (
        CorrelationPolicy,
        NoCorrelationPolicy,
        DictFieldPolicy,
        Envelope,
        test_namespace,
    )

Multi-transport scenarios — opt-in:

    from admiral import Stage, MappedBridge, IdentityBridge, CorrelationBridge
    from admiral import (
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
