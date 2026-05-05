"""Multi-transport scenario coordinator.

`Stage` wraps a named registry of `Harness` instances and a
`CorrelationBridge` so a single scenario can publish, expect, and reply
across multiple message transports. See ADR-0027 for the design.

Public surface:

    from choreo.stage import (
        Stage,
        IdentityBridge, MappedBridge, CorrelationBridge,
        StageError, StageStateError, StageConnectError,
        StageDisconnectError, MissingTransportError,
        UnknownTransportError, InvalidTransportNameError,
        BridgeAmbiguityError, BridgeTransportMismatchError,
        BridgeTranslationError,
        StageReplyState, StageReplyReport, StageScenarioResult,
    )
"""

from __future__ import annotations

# Underscored names are re-exported for in-tree test imports
# (`from choreo.stage import _SMOKE_INPUT`). Keep them as `as`-aliases so
# linters do not flag them as unused.
from ._helpers import _MAX_WIRE_ID_LEN as _MAX_WIRE_ID_LEN
from ._helpers import _SMOKE_INPUT as _SMOKE_INPUT
from ._helpers import _redact as _redact
from ._scope import StageReplyChain
from ._scope import _StageScenarioScope as _StageScenarioScope
from ._stage import Stage
from ._state import StageReplyReport, StageReplyState, StageScenarioResult
from ._state import _StageScenarioResult as _StageScenarioResult
from .bridges import CorrelationBridge, IdentityBridge, MappedBridge
from .errors import (
    BridgeAmbiguityError,
    BridgeTranslationError,
    BridgeTransportMismatchError,
    InvalidTransportNameError,
    MissingTransportError,
    StageConnectError,
    StageDisconnectError,
    StageError,
    StageStateError,
    UnknownTransportError,
)

__all__ = [
    "BridgeAmbiguityError",
    "BridgeTranslationError",
    "BridgeTransportMismatchError",
    "CorrelationBridge",
    "IdentityBridge",
    "InvalidTransportNameError",
    "MappedBridge",
    "MissingTransportError",
    "Stage",
    "StageConnectError",
    "StageDisconnectError",
    "StageError",
    "StageReplyChain",
    "StageReplyReport",
    "StageReplyState",
    "StageScenarioResult",
    "StageStateError",
    "UnknownTransportError",
]
