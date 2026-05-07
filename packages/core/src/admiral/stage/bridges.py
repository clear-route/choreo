"""CorrelationBridge protocol and reference implementations.

A bridge is consumer code: it maps a logical scope id to per-transport
wire ids. The Stage calls `fresh()` once per scope and
`to_wire(logical, transport)` once per registered transport per scope at
scope entry. The bridge is NOT invoked on the inbound message hot path;
wire-level comparison is used there. `from_wire()` is invoked only for
diagnostics. See ADR-0027 §Security Considerations for the trust-boundary
discussion.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CorrelationBridge(Protocol):
    """Maps a logical scope id to and from per-transport wire ids.

    Implementations must:
      * be deterministic on `to_wire` for the duration of a scope
      * make `to_wire` return distinct, non-empty `str` values for
        distinct registered transports given the same logical id
      * keep `to_wire` synchronous and fast (it runs inline on the test
        event loop; a slow implementation stalls every concurrent scope)
      * keep wire id length within the Stage's per-call cap
        (`_MAX_WIRE_ID_LEN`); the Stage rejects longer values
      * be stateless across scopes; the bridge instance is shared by
        every scope opened against the Stage
      * make `fresh()` collision-resistant per process

    The shipped `IdentityBridge` and `MappedBridge` honour all the above.
    """

    async def fresh(self) -> Any: ...

    def to_wire(self, logical: Any, transport: str) -> str: ...

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


class IdentityBridge:
    """Bridge for the homogeneous case: every transport sees the same
    wire id.

    This bridge is rejected by `Stage.__init__` whenever more than one
    transport is registered, because `to_wire` returns the same value
    for every transport (which trips `BridgeAmbiguityError`). Useful
    only for framework-internal tests of single-transport Stages and
    parallel-isolation tests where multiple Stages each carry one
    transport. Production code wanting per-transport translation must
    use `MappedBridge` or a custom implementation.
    """

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        return str(logical)

    def from_wire(self, wire: str, transport: str) -> str:
        return wire


@dataclass(frozen=True)
class _MapEntry:
    forward: Callable[[Any], str]
    inverse: Callable[[str], Any] | None = None


class MappedBridge:
    """Bridge with explicit per-transport forward functions.

    forwards: transport name -> function that turns a logical id into
        the wire id for that transport. Functions must be deterministic,
        synchronous, and return a non-empty `str`.
    inverses: optional transport name -> function that turns a wire id
        back into the logical id (diagnostics only). Missing inverses
        are silently treated as "no diagnostic available".

    Stage validation: if the registered harness set does not match
    `configured_transports`, the Stage surfaces a
    `BridgeTransportMismatchError` at `__init__` rather than surfacing
    the underlying KeyError at first use.
    """

    def __init__(
        self,
        forwards: Mapping[str, Callable[[Any], str]],
        inverses: Mapping[str, Callable[[str], Any]] | None = None,
    ) -> None:
        inv = inverses or {}
        self._entries: dict[str, _MapEntry] = {
            name: _MapEntry(forward=forwards[name], inverse=inv.get(name)) for name in forwards
        }

    @property
    def configured_transports(self) -> frozenset[str]:
        """Public view used by Stage to detect transport-set mismatches
        before any to_wire() call runs."""
        return frozenset(self._entries)

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        entry = self._entries[transport]
        return str(entry.forward(logical))

    def from_wire(self, wire: str, transport: str) -> Any | None:
        entry = self._entries.get(transport)
        if entry is None or entry.inverse is None:
            return None
        return entry.inverse(wire)
