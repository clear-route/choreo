"""Stage error hierarchy.

Each subclass inherits from `StageError` (the catch-all marker) AND a
standard taxon (`LookupError` / `ValueError` / `RuntimeError` /
`ExceptionGroup`) so consumers can also `except ValueError` for
typo-style mistakes uniformly. See ADR-0027 §Implementation Error types.
"""

from __future__ import annotations

import re

# Transport name regex (PRD-012 §1.4-§1.5, PRD-013 §1.1). Stage validates
# at __init__ to fail closed before consumer-supplied names can flow into
# the timeline / results.json / Phase 2 renderer.
_TRANSPORT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class StageError(Exception):
    """Mixin marker for every Stage-emitted exception."""


class StageStateError(StageError, RuntimeError):
    """Stage method called in the wrong lifecycle state.

    Examples: connect() called twice; connect() called after disconnect();
    scenario() called before connect(). The message names the current
    state so the caller can distinguish the cases.
    """


class MissingTransportError(StageError, ValueError):
    """A Stage scenario DSL method was called without an `on=` selector.

    Inherits from `ValueError` (caller-error taxon) so consumers can
    `except ValueError` for typo-and-omission-style mistakes uniformly.
    """


class UnknownTransportError(StageError, LookupError):
    """A Stage scenario DSL method's `on=` selector named a transport
    not registered on the parent Stage.

    Inherits from `LookupError` (the standard taxon for name-not-found)
    so consumers can `except LookupError` for transport-name typos.
    """


class InvalidTransportNameError(StageError, ValueError):
    """A transport name registered on `Stage(harnesses=...)` does not
    match the schema regex `^[a-zA-Z0-9_-]{1,64}$` (PRD-012 §1.4-§1.5).

    Stage fails closed at construction so consumer-supplied names cannot
    propagate into the timeline / results.json / Phase 2 renderer where
    they would otherwise become an injection surface.
    """


class StageConnectError(StageError, RuntimeError):
    """Stage.connect() aborted on a transport failure.

    The failing transport AND every already-connected sibling were
    disconnected (best-effort) before this exception propagated. The
    Stage stays in NEW state; construct a new Stage to retry.
    """

    def __init__(self, *, failing_transport: str, bridge_class: str) -> None:
        super().__init__(
            f"Stage.connect aborted: transport {failing_transport!r} failed "
            f"(bridge: {bridge_class}); the failing transport AND every "
            f"already-connected transport were disconnected"
        )
        self.failing_transport = failing_transport
        self.bridge_class = bridge_class


class StageDisconnectError(StageError, ExceptionGroup):
    """PEP 654 ExceptionGroup for one or more disconnect failures.

    Single-failure disconnect produces a group of length 1 — the surface
    is uniform regardless of how many transports raised, so consumer
    error-handling code does not branch on count.
    """

    def __new__(cls, message: str, errors: list[BaseException]) -> StageDisconnectError:
        return super().__new__(cls, message, errors)


class BridgeAmbiguityError(StageError, ValueError):
    """Raised at Stage.__init__ (synthetic input) or scope entry (real
    logical id) when the bridge maps two transports to identical wire ids.

    The colliding transport names are on `.transports`. The wire id is
    redacted in the message string; consumers needing the full value
    must implement a bridge whose `__cause__` carries it explicitly.
    """

    def __init__(self, message: str, *, transports: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        # Sort so consumers can assert against `.transports` without
        # depending on dict iteration order.
        self.transports = tuple(sorted(transports))


class BridgeTransportMismatchError(StageError, ValueError):
    """Raised at Stage.__init__ when a bridge advertising
    `configured_transports` does not match the registered harness set
    exactly. Surfaces the mismatch as a typed error rather than as a
    KeyError-wrapped BridgeTranslationError at first use.
    """

    def __init__(
        self,
        *,
        bridge_class: str,
        bridge_transports: tuple[str, ...],
        registered_transports: tuple[str, ...],
    ) -> None:
        super().__init__(
            f"{bridge_class} configured for transports {bridge_transports} "
            f"but Stage registered {registered_transports}; the sets must "
            f"match"
        )
        self.bridge_class = bridge_class
        self.bridge_transports = bridge_transports
        self.registered_transports = registered_transports


class BridgeTranslationError(StageError, RuntimeError):
    """Wraps any exception raised by bridge.fresh / to_wire / from_wire,
    or a type-validation failure on the bridge's return value.

    Mirrors ADR-0019's `CorrelationPolicyError` shape: bridge_class,
    method, transport, and the original exception are on named
    attributes. The message names the original exception's CLASS only —
    never its `str()` — because bridge-side exceptions can carry
    sensitive identifiers (mirrors `BridgeAmbiguityError`'s redaction
    posture for wire ids).
    """

    def __init__(
        self,
        *,
        bridge_class: str,
        method: str,
        transport: str | None,
        original: BaseException,
    ) -> None:
        location = f" for transport {transport!r}" if transport else ""
        super().__init__(
            f"{bridge_class}.{method} raised "
            f"{type(original).__name__}{location}; "
            f"see .original for the wrapped exception"
        )
        self.bridge_class = bridge_class
        self.method = method
        self.transport = transport
        self.original = original
