"""The `Stage` coordinator class.

Holds an ordered named registry of Harness instances and a bridge that
translates a logical scope id into per-transport wire ids. Drives the
harnesses through their public surface — single-transport callers see
nothing new. 
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from admiral.harness import Harness

from ._helpers import (
    _MAX_WIRE_ID_LEN,
    _SMOKE_INPUT,
    _check_distinctness,
)
from ._scope import _StageScenarioScope
from ._state import _StageState
from .bridges import CorrelationBridge
from .errors import (
    _TRANSPORT_NAME_PATTERN,
    BridgeTranslationError,
    BridgeTransportMismatchError,
    InvalidTransportNameError,
    StageConnectError,
    StageDisconnectError,
    StageStateError,
)

log = logging.getLogger("admiral.stage")


class Stage:
    """Coordinator for multi-transport scenarios.

    State machine:
        NEW -> CONNECTED via connect(); StageConnectError on failure
            (state stays NEW; rolled-back transports are torn down).
        CONNECTED -> DISCONNECTED via disconnect(); idempotent thereafter.
        Re-use is not supported: connect() on a DISCONNECTED stage
        raises StageStateError. Construct a new Stage to reconnect.

    The registered harness set is fixed at construction. The Stage does
    not expose a runtime register/deregister API; consumer code that
    needs a different transport set constructs a new Stage. Internal
    callers (the per-scope guard, the connect/disconnect loop) treat
    `_harnesses` as read-only.
    """

    def __init__(
        self,
        harnesses: Mapping[str, Harness],
        bridge: CorrelationBridge,
    ) -> None:
        if not harnesses:
            raise ValueError("Stage requires at least one harness")
        # Fail closed on transport names that do not match the schema
        # regex BEFORE any further setup. Consumer-supplied names flow
        # into the timeline, results.json, and (Phase 2) the HTML
        # renderer; rejecting at construction keeps the framework
        # boundary the only place this constraint is enforced.
        for name in harnesses:
            if not isinstance(name, str) or not _TRANSPORT_NAME_PATTERN.match(name):
                raise InvalidTransportNameError(
                    f"transport name {name!r} does not match {_TRANSPORT_NAME_PATTERN.pattern}"
                )
        # `dict(...)` preserves insertion order (Python 3.7+) so
        # downstream rollback / disconnect routines see deterministic
        # registration order.
        self._harnesses: dict[str, Harness] = dict(harnesses)
        self._bridge = bridge
        self._state: _StageState = _StageState.NEW
        self._connected: list[str] = []
        self._validate_bridge_transport_set()
        self._validate_bridge_distinctness()
        log.info(
            "stage_initialised",
            extra={
                "bridge_class": type(self._bridge).__name__,
                "transports": tuple(self._harnesses),
            },
        )

    # -- validation ------------------------------------------------------

    def _validate_bridge_transport_set(self) -> None:
        """If the bridge advertises a `configured_transports` set
        (e.g. MappedBridge), check it matches the registered harnesses
        exactly. Surfaces a typed error rather than a generic KeyError
        at first use.

        The advertised value is coerced to `frozenset` before compare
        so bridges returning a list, tuple, set, or any iterable of
        names work consistently — the contract is "the set of names
        must match", not "the exact container type must be frozenset".
        """
        configured = getattr(self._bridge, "configured_transports", None)
        if configured is None:
            return
        configured_set = frozenset(configured)
        registered = frozenset(self._harnesses)
        if configured_set != registered:
            raise BridgeTransportMismatchError(
                bridge_class=type(self._bridge).__name__,
                bridge_transports=tuple(sorted(configured_set)),
                registered_transports=tuple(sorted(registered)),
            )

    def _validate_bridge_distinctness(self) -> None:
        """Smoke-test the bridge: reject configurations that produce
        the same wire id for distinct transports against a synthetic
        input.

        This is a startup smoke test, NOT a correctness proof. A bridge
        that returns distinct values for the synthetic input but
        collides on real logical ids will pass this check;
        `_StageScenarioScope._mint_all_children` re-runs the same check
        against the actual logical id at scope entry to catch in-flight
        collisions. 
        """
        # Exhaust the generator — we do not retain the (name, wire)
        # pairs at construction; the per-scope mint is the one that
        # uses them.
        for _ in _check_distinctness(
            transports=self._harnesses,
            call_to_wire=lambda name: self._call_to_wire(_SMOKE_INPUT, name),
            bridge_class_name=type(self._bridge).__name__,
            context_label=(
                f"during startup smoke-test (synthetic input length "
                f"{len(_SMOKE_INPUT)}). The bridge must return distinct "
                f"wire ids per transport."
            ),
        ):
            pass

    def _bridge_error(
        self,
        method: str,
        transport: str | None,
        original: BaseException,
    ) -> BridgeTranslationError:
        """Factory for BridgeTranslationError carrying this Stage's
        bridge class. Shared by every bridge call site (`to_wire`,
        `fresh`, and any future `from_wire` diagnostic path) so the
        error shape stays uniform across the trust boundary."""
        return BridgeTranslationError(
            bridge_class=type(self._bridge).__name__,
            method=method,
            transport=transport,
            original=original,
        )

    def _call_to_wire(self, logical: Any, transport: str) -> str:
        """Wrap to_wire: type-check the return, surface failures as
        BridgeTranslationError. Single chokepoint so every call site
        uses the same validation."""
        try:
            wire = self._bridge.to_wire(logical, transport)
        except Exception as exc:
            raise self._bridge_error("to_wire", transport, exc) from exc
        if not isinstance(wire, str) or not wire:
            raise self._bridge_error(
                "to_wire",
                transport,
                TypeError(f"to_wire must return a non-empty str, got {type(wire).__name__}"),
            )
        if len(wire) > _MAX_WIRE_ID_LEN:
            raise self._bridge_error(
                "to_wire",
                transport,
                ValueError(
                    f"to_wire returned a wire id of length {len(wire)}; limit is {_MAX_WIRE_ID_LEN}"
                ),
            )
        return wire

    # -- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        """Connect every registered harness in registration order.

        State transitions: NEW -> CONNECTED on success. On any harness's
        connect() raising, the Stage rolls back: it disconnects the
        failing transport (its connect may have opened resources before
        raising) and every already-connected sibling in reverse order,
        swallowing rollback failures into structured WARNING logs. The
        Stage stays in NEW state; the caller must construct a fresh
        Stage to retry.
        """
        if self._state is not _StageState.NEW:
            raise StageStateError(
                f"Stage.connect() requires state {_StageState.NEW.value!r}, "
                f"got {self._state.value!r}; construct a new Stage to reconnect"
            )
        for name, harness in self._harnesses.items():
            try:
                await harness.connect()
            except Exception as exc:
                await self._rollback(
                    connected_so_far=list(self._connected),
                    failing=(name, harness),
                )
                raise StageConnectError(
                    failing_transport=name,
                    bridge_class=type(self._bridge).__name__,
                ) from exc
            self._connected.append(name)
        self._state = _StageState.CONNECTED

    async def _rollback(
        self,
        *,
        connected_so_far: list[str],
        failing: tuple[str, Harness],
    ) -> None:
        """Disconnect the failing harness (force-mode) then every
        already-connected sibling in reverse registration order. Every
        disconnect is wrapped: rollback failures are logged at WARNING
        and never raised. The rollback path itself is total — once
        entered, it always returns.

        State stays at NEW after rollback so the StageConnectError the
        caller sees corresponds to a stage that is not in any
        in-between state. Re-attempt requires a fresh Stage.

        The failing harness uses `force_disconnect()` so the transport's
        own disconnect runs even though the harness never reached the
        connected state (its connect() raised). Siblings use the regular
        `disconnect()` because they did complete their connect handshake.
        """
        failing_name, failing_harness = failing
        try:
            await failing_harness.force_disconnect()
        except Exception:
            log.warning(
                "stage_rollback_failing_transport_disconnect_failed",
                extra={"transport": failing_name},
                exc_info=True,
            )
        for name in reversed(connected_so_far):
            try:
                await self._harnesses[name].disconnect()
            except Exception:
                log.warning(
                    "stage_rollback_sibling_disconnect_failed",
                    extra={"transport": name},
                    exc_info=True,
                )
        self._connected.clear()

    async def disconnect(self) -> None:
        """Disconnect every connected harness in reverse registration
        order.

        Idempotent: safe to call from a `finally` block whether
        `connect()` succeeded, partially succeeded, or never ran. After
        this call the Stage is in DISCONNECTED state and cannot be
        reconnected.

        Best-effort across all transports: if any harness's disconnect()
        raises, the loop continues. Collected errors are raised as a
        `StageDisconnectError` (PEP 654 ExceptionGroup) so consumers can
        use `except*` to walk individual failures. State is set to
        DISCONNECTED before the raise so the Stage is in a defined
        terminal state regardless of how disconnect ended.
        """
        if self._state is _StageState.DISCONNECTED:
            return
        errors: list[BaseException] = []
        for name in reversed(self._connected):
            try:
                await self._harnesses[name].disconnect()
            except Exception as exc:  # noqa: BLE001 — collected into group
                errors.append(exc)
        self._connected.clear()
        self._state = _StageState.DISCONNECTED
        if errors:
            raise StageDisconnectError("stage disconnect raised on one or more transports", errors)

    def scenario(self, name: str) -> _StageScenarioScope:
        """Open a multi-transport scenario scope.

        Stage state is intentionally NOT mutated by opening or closing
        a scope: a Stage stays CONNECTED while N scopes are live. Each
        scope owns its own per-transport children and lifecycle (eager
        mint at __aenter__, isolated teardown at __aexit__). Parallel
        scenarios on one Stage are supported by construction; the Stage
        does not gate them via state.

        If a future requirement needs "disconnect cannot run while a
        scope is open", that gating belongs in Stage.disconnect (a
        live-scope counter, separate from the lifecycle state machine),
        NOT in a fourth state.
        """
        if self._state is not _StageState.CONNECTED:
            raise StageStateError(
                f"Stage.scenario() requires state "
                f"{_StageState.CONNECTED.value!r}, got {self._state.value!r}"
            )
        return _StageScenarioScope(name=name, stage=self)
