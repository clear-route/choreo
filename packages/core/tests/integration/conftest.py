"""Shared fixtures for Stage integration tests.

The Stage coordinator is a multi-transport facade over `Harness`. These
fixtures build the minimum collaborators each test group needs:

* `two_harnesses` returns a `{name: Harness}` mapping of two `MockTransport`-
  backed harnesses, ready to be passed to `Stage(harnesses=...)`. Most tests
  use this directly. Tests that need a custom transport (failing-connect,
  resource-leaking, droppable) construct their own.
* Test-only bridges and transport doubles live next to the test groups that
  use them: the Group A construction tests carry their bridges in this
  module; later groups extend this conftest as the test plan walks through
  them.

See `docs/test-plans/0027-stage-integration-tests.md` for the test plan
this file implements, and ADR-0027 for the design under test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from admiral.transports import MockTransport

# ---------------------------------------------------------------------------
# Harness construction helper
# ---------------------------------------------------------------------------


def _mock_harness(allowlist_yaml_path: Path, endpoint: str = "mock://localhost"):
    """Build a Harness over a MockTransport with the shipped allowlist.

    Mirrors the helper in `tests/test_harness.py`; duplicated here to keep
    the integration suite self-contained and to give us room to vary the
    transport without touching the unit-suite helper.
    """
    from admiral import Harness

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint=endpoint,
    )
    return Harness(transport)


def _harness_over(transport: Any):
    """Build a Harness over a caller-supplied Transport.

    Used by tests that need a custom transport double (failing-connect,
    resource-leaking, recording, droppable). The Harness construction is
    otherwise identical to `_mock_harness`.
    """
    from admiral import Harness

    return Harness(transport)


@pytest.fixture
def two_harnesses(allowlist_yaml_path: Path) -> dict[str, Any]:
    """Return `{"nats": Harness, "kafka": Harness}` over two MockTransports.

    Names match the canonical bridge example in PRD-011 / ADR-0027.
    Neither harness is connected; tests connect via Stage.connect().

    Both MockTransports register `mock://localhost` because that is the
    one mock endpoint the shipped `config/allowlist.yaml` permits. The
    endpoint string is only used for the allowlist check; MockTransport
    is in-memory and the two harness instances are independent regardless.
    """
    return {
        "nats": _mock_harness(allowlist_yaml_path),
        "kafka": _mock_harness(allowlist_yaml_path),
    }


@pytest.fixture
async def connected_stage(two_harnesses: dict[str, Any]):
    """Yield a connected `Stage` over the canonical {nats, kafka} pair.

    Used by tests that exercise full Stage scenarios end-to-end and do
    not need bespoke harness configuration. The Stage is connected
    before yield and disconnected on teardown. Bridge is the canonical
    `mapped_bridge_for("nats", "kafka")`.
    """
    from admiral.stage import Stage

    stage = Stage(harnesses=two_harnesses, bridge=mapped_bridge_for("nats", "kafka"))
    await stage.connect()
    try:
        yield stage
    finally:
        await stage.disconnect()


@pytest.fixture
async def open_scope(two_harnesses: dict[str, Any]):
    """Yield an open Stage scenario scope. Used by every Group F+
    test that exercises the DSL surface (`expect`, `publish`, `on`)
    on a scope whose lifecycle is not the subject under test.

    Bridge is the canonical {nats, kafka} pair; scope name is fixed
    because it is not asserted on. Tests that need a custom bridge
    or transport set construct their own Stage inline.
    """
    from admiral.stage import Stage

    stage = Stage(harnesses=two_harnesses, bridge=mapped_bridge_for("nats", "kafka"))
    await stage.connect()
    try:
        async with stage.scenario("dsl-test") as scope:
            yield scope
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# Bridge doubles for Group A (construction)
# ---------------------------------------------------------------------------
#
# These are structural duck-types of `CorrelationBridge`. They do not
# import the Protocol because the protocol does not need to be satisfied
# nominally — `runtime_checkable` Protocols match by shape. Each double
# implements just enough of the surface to drive its target failure mode.


class _CollidingBridge:
    """Returns the same wire id for every transport. Drives Group A scenario
    A2 (BridgeAmbiguityError at startup smoke test)."""

    async def fresh(self) -> str:
        return "logical-001"

    def to_wire(self, logical: Any, transport: str) -> str:
        return "same-wire-id-for-every-transport"

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


class _RaisingBridge:
    """Configurable: raise from the named bridge method. Drives Group A
    scenario A5 (to_wire raise during startup smoke test) and several
    Group E scenarios (fresh / to_wire raises at scope entry)."""

    def __init__(self, *, raise_on: str, exc: BaseException | None = None) -> None:
        if raise_on not in {"fresh", "to_wire", "from_wire"}:
            raise ValueError(f"unknown method to raise on: {raise_on!r}")
        self._raise_on = raise_on
        self._exc = exc or RuntimeError(f"{raise_on} test-injected failure")

    async def fresh(self) -> str:
        if self._raise_on == "fresh":
            raise self._exc
        return "logical-fresh"

    def to_wire(self, logical: Any, transport: str) -> str:
        if self._raise_on == "to_wire":
            raise self._exc
        return f"wire-{transport}-{logical}"

    def from_wire(self, wire: str, transport: str) -> Any | None:
        if self._raise_on == "from_wire":
            raise self._exc
        return None


class _TypeBrokenBridge:
    """to_wire returns a value of the caller's choosing. Drives Group A
    return-type validation scenarios (A6, A7, A8, A9, N1, N2)."""

    def __init__(self, *, returns: Any) -> None:
        self._returns = returns

    async def fresh(self) -> str:
        return "logical-typebroken"

    def to_wire(self, logical: Any, transport: str) -> Any:
        # Deliberately returning Any; the Stage's _call_to_wire validator
        # is what we are testing.
        return self._returns

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


class _SmokeTestEscapeBridge:
    """Returns distinct values for the Stage's startup smoke-test
    synthetic input but the same value for any other input.

    Drives Group E scenario E3: the per-scope re-validation must catch
    bridges that pass startup distinctness but collide on the real
    `bridge.fresh()` value. Imports `_SMOKE_INPUT` from `admiral.stage`
    so the synthetic-recognition logic stays pinned to the production
    constant — no silent drift if the production synthetic rotates.
    """

    async def fresh(self) -> str:
        return "real-logical-id-collides"

    def to_wire(self, logical: Any, transport: str) -> str:
        from admiral.stage import _SMOKE_INPUT

        if logical == _SMOKE_INPUT:
            return f"smoke-{transport}"  # distinct per transport
        return "always-colliding-wire"  # same for every transport

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


class _PerTransportRaisingBridge:
    """Bridge whose `to_wire` raises only when called for one specific
    target transport AND the input is not the Stage's startup synthetic.

    Drives Group E scenarios where the failure point must be the eager
    mint at `__aenter__`, not the construction-time smoke test. Imports
    `_SMOKE_INPUT` from `admiral.stage` so the smoke-test path stays
    pinned to the production constant.
    """

    def __init__(self, *, raises_for: str, exc: BaseException | None = None) -> None:
        self._raises_for = raises_for
        self._exc = exc or RuntimeError(
            f"to_wire test-injected failure for transport {raises_for!r}"
        )

    async def fresh(self) -> str:
        return "logical-per-transport"

    def to_wire(self, logical: Any, transport: str) -> str:
        from admiral.stage import _SMOKE_INPUT

        if logical == _SMOKE_INPUT:
            # Construction-time smoke test: return distinct values so
            # Stage.__init__ passes. The failure must surface at scope
            # entry, not during construction.
            return f"smoke-{transport}"
        if transport == self._raises_for:
            raise self._exc
        return f"{transport}-{logical}"

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


# ---------------------------------------------------------------------------
# Convenience: a known-good MappedBridge factory used by tests that need a
# real bridge to set up their negative scenario (e.g. A3 needs a misconfigured
# MappedBridge; comparator tests need a working one to contrast against).
# ---------------------------------------------------------------------------


def make_mapped_bridge(
    forwards: dict[str, Callable[[Any], str]] | None = None,
):
    """Construct a MappedBridge with sensible default forwards for the
    canonical {nats, kafka} pair. Tests override `forwards` to drive
    transport-set mismatch scenarios."""
    from admiral.stage import MappedBridge

    if forwards is None:
        forwards = {
            "nats": lambda logical: f"nats-{logical}",
            "kafka": lambda logical: f"kafka-evt-{logical}",
        }
    return MappedBridge(forwards=forwards)


def single_transport_bridge(name: str):
    """Construct a MappedBridge for a single named transport. The forward
    function returns a name-prefixed wire id so distinctness is trivially
    satisfied; tests asserting failure modes do not need to vary it."""
    from admiral.stage import MappedBridge

    return MappedBridge(forwards={name: lambda logical: f"{name}-{logical}"})


def mapped_bridge_for(*names: str):
    """Construct a MappedBridge spanning a caller-supplied set of
    transport names. Each transport gets a name-prefixed forward
    function so the smoke-test distinctness check passes uniformly.
    Use this when the test cares about Stage behaviour across multiple
    transports but does not need bespoke bridge translation logic."""
    from admiral.stage import MappedBridge

    return MappedBridge(
        forwards={name: (lambda logical, n=name: f"{n}-{logical}") for name in names}
    )


# ---------------------------------------------------------------------------
# Transport doubles for Group C (connect rollback) and beyond
# ---------------------------------------------------------------------------
#
# These subclass `MockTransport` so that subscribe/publish/allowlist
# behaviour stays intact; only the lifecycle methods are overridden to
# inject failures or record calls.


class _FailingMockTransport(MockTransport):
    """MockTransport whose `connect`, `disconnect`, `unsubscribe`,
    and/or `publish` raise on demand.

    Each `fail_*` parameter is an exception instance or None. Set the
    parameters at construction OR mutate them post-hoc via `drop()`
    (test-only API) for the broker-drop simulation.

    Used across:

    * Group C C1-C4 — `fail_connect` / `fail_disconnect` for rollback paths
    * Group G G1+ — `fail_unsubscribe` for teardown isolation
    * Group H — `drop()` for mid-scenario broker drop (sets
      `fail_publish` and `fail_unsubscribe` to simulate a half-dead
      broker; clears in-flight callbacks)

    Simulation fidelity: `drop()` is a clean-cut model of broker
    failure — synchronous severance of in-flight callbacks. A real
    NATS/Kafka/Rabbit drop is messier: callbacks may continue to fire
    on stale state until the underlying client realises it has been
    dropped. Tests using `drop()` exercise the deterministic worst
    case (publish raises, unsubscribe raises) rather than the
    flakier real-world middle.
    """

    def __init__(
        self,
        *,
        fail_connect: BaseException | None = None,
        fail_disconnect: BaseException | None = None,
        fail_unsubscribe: BaseException | None = None,
        fail_publish: BaseException | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._fail_connect = fail_connect
        self._fail_disconnect = fail_disconnect
        self._fail_unsubscribe = fail_unsubscribe
        self._fail_publish = fail_publish

    def drop(self) -> None:
        """Simulate a mid-scenario broker drop.

        Subsequent `publish` raises, subsequent `unsubscribe` raises,
        and the in-flight callback registry is cleared so no further
        delivered messages reach scope subscribers. Test-only API.
        """
        self._fail_publish = RuntimeError("transport dropped: cannot publish")
        self._fail_unsubscribe = RuntimeError("transport dropped: cannot unsubscribe")
        self._callbacks.clear()

    async def connect(self) -> None:
        if self._fail_connect is not None:
            # Skip the parent's allowlist / auth setup — the test cares
            # about the failure, not about partial-up resources.
            raise self._fail_connect
        await super().connect()

    async def disconnect(self) -> None:
        if self._fail_disconnect is not None:
            raise self._fail_disconnect
        await super().disconnect()

    def unsubscribe(self, topic: str, callback: Any) -> None:
        if self._fail_unsubscribe is not None:
            raise self._fail_unsubscribe
        super().unsubscribe(topic, callback)

    def publish(self, topic: str, payload: bytes, *, on_sent: Any = None) -> None:
        if self._fail_publish is not None:
            raise self._fail_publish
        super().publish(topic, payload, on_sent=on_sent)


class _ResourceLeakingMockTransport(MockTransport):
    """MockTransport whose `connect` opens a side-effect resource on a
    shared sentinel BEFORE raising; `disconnect` releases it.

    Used by Group C scenario C2 to assert that the rollback path runs
    `disconnect` on the failing transport (not just the already-up
    siblings). The sentinel counter going back to zero after a failed
    Stage.connect is the observable that proves the rollback reached the
    failing transport.
    """

    def __init__(
        self,
        *,
        sentinel: list[int],
        fail_connect: BaseException,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._sentinel = sentinel
        self._fail_connect = fail_connect
        self._held = False

    async def connect(self) -> None:
        # Acquire the resource BEFORE raising, mirroring a real transport
        # that opens a socket / spawns a task and only then encounters
        # the failure that aborts connect.
        self._sentinel[0] += 1
        self._held = True
        raise self._fail_connect

    async def disconnect(self) -> None:
        if self._held:
            self._sentinel[0] -= 1
            self._held = False
        # Do NOT call super().disconnect() — connect never completed, so
        # there is no parent state to tear down.


class _RecordingMockTransport(MockTransport):
    """MockTransport that records every connect / disconnect call.

    Used by Group C / O ordering and rollback tests where the assertion
    is about which methods were called and in what order.
    """

    def __init__(self, *, ledger: list[tuple[str, str]], name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ledger = ledger
        self._name = name

    async def connect(self) -> None:
        self._ledger.append((self._name, "connect"))
        await super().connect()

    async def disconnect(self) -> None:
        self._ledger.append((self._name, "disconnect"))
        await super().disconnect()
