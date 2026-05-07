"""Group O: Stage connect/disconnect ordering integration tests.

Covers test-plan items O1-O3. The contract:

* Connect calls every harness in REGISTRATION ORDER (insertion
  order of the `harnesses` dict).
* Disconnect calls every harness in REVERSE registration order.
* Rollback (when one connect raises) disconnects in reverse-of-the-
  successful-connects, plus the failing transport — so the actual
  sequence is `[failing, ...siblings_in_reverse]`.

The plan asserts these contracts so a future change that switches
to e.g. parallel connect/disconnect (or any other ordering) shows
up as a behavioural delta.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from admiral.stage import Stage, StageConnectError

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    _RecordingMockTransport,
    mapped_bridge_for,
)


def _three_recording_harnesses(
    allowlist_yaml_path: Path,
    ledger: list[tuple[str, str]],
):
    """Build three recording harnesses keyed alpha/beta/gamma. The
    ledger captures every connect/disconnect call across all three
    in the order they fire."""

    def _mk(name: str):
        return _harness_over(
            _RecordingMockTransport(
                ledger=ledger,
                name=name,
                allowlist_path=allowlist_yaml_path,
                endpoint="mock://localhost",
            )
        )

    return {
        "alpha": _mk("alpha"),
        "beta": _mk("beta"),
        "gamma": _mk("gamma"),
    }


# ---------------------------------------------------------------------------
# O1 — connect calls harnesses in registration order
# ---------------------------------------------------------------------------


async def test_stage_connect_should_call_harnesses_in_registration_order(
    allowlist_yaml_path: Path,
) -> None:
    """O1. Three harnesses keyed alpha/beta/gamma. After
    `Stage.connect()` returns, the recorded order of `connect()`
    calls is exactly `[alpha, beta, gamma]` — the dict's insertion
    order.
    """
    ledger: list[tuple[str, str]] = []
    harnesses = _three_recording_harnesses(allowlist_yaml_path, ledger)
    stage = Stage(
        harnesses=harnesses,
        bridge=mapped_bridge_for("alpha", "beta", "gamma"),
    )

    await stage.connect()
    try:
        connects = [entry for entry in ledger if entry[1] == "connect"]
        assert connects == [
            ("alpha", "connect"),
            ("beta", "connect"),
            ("gamma", "connect"),
        ]
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# O2 — disconnect calls harnesses in reverse registration order
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_call_harnesses_in_reverse_registration_order(
    allowlist_yaml_path: Path,
) -> None:
    """O2. Same setup as O1. After `Stage.connect()` then
    `Stage.disconnect()`, the recorded order of `disconnect()` calls
    is `[gamma, beta, alpha]` — reverse of registration order.
    """
    ledger: list[tuple[str, str]] = []
    harnesses = _three_recording_harnesses(allowlist_yaml_path, ledger)
    stage = Stage(
        harnesses=harnesses,
        bridge=mapped_bridge_for("alpha", "beta", "gamma"),
    )

    await stage.connect()
    await stage.disconnect()

    disconnects = [entry for entry in ledger if entry[1] == "disconnect"]
    assert disconnects == [
        ("gamma", "disconnect"),
        ("beta", "disconnect"),
        ("alpha", "disconnect"),
    ]


# ---------------------------------------------------------------------------
# O3 — rollback disconnects failing transport then siblings in reverse
# ---------------------------------------------------------------------------


async def test_stage_connect_rollback_should_disconnect_in_reverse_of_connect(
    allowlist_yaml_path: Path,
) -> None:
    """O3. Three harnesses; the THIRD one (gamma) raises on connect.
    After the rollback, the recorded order is:

      [alpha, connect],   # alpha connects successfully
      [beta, connect],    # beta connects successfully
      # gamma's connect raises — no entry recorded for gamma's connect
      # rollback disconnects:
      [beta, disconnect], # sibling, in reverse
      [alpha, disconnect],

    Note: the failing transport (gamma) IS disconnected via
    `Harness.force_disconnect`, but the recording transport's
    `disconnect` is the parent's (super().disconnect()), which is
    short-circuited by Harness.disconnect's `if not self._connected:
    return` guard. So the gamma disconnect call DOES go through
    `force_disconnect` → `transport.disconnect()` directly. This
    test asserts the visible ordering on the SIBLINGS' ledger (alpha
    and beta), which is what the contract is about.
    """
    ledger: list[tuple[str, str]] = []
    alpha_h = _harness_over(
        _RecordingMockTransport(
            ledger=ledger,
            name="alpha",
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    beta_h = _harness_over(
        _RecordingMockTransport(
            ledger=ledger,
            name="beta",
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    gamma_h = _harness_over(
        _FailingMockTransport(
            fail_connect=RuntimeError("gamma broker unreachable"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h, "gamma": gamma_h},
        bridge=mapped_bridge_for("alpha", "beta", "gamma"),
    )

    with pytest.raises(StageConnectError):
        await stage.connect()

    # Connect entries: alpha and beta only (gamma's connect raised
    # before the recording transport's super().connect() ran).
    connects = [entry for entry in ledger if entry[1] == "connect"]
    assert connects == [("alpha", "connect"), ("beta", "connect")]

    # Disconnect entries (siblings only, in reverse): beta then alpha.
    # gamma's disconnect goes through force_disconnect; its recording
    # transport does not record because the super().connect() never
    # completed, so the parent's _connected flag is False and
    # super().disconnect() short-circuits. The contract under test is
    # the SIBLINGS' ordering.
    disconnects = [entry for entry in ledger if entry[1] == "disconnect"]
    assert disconnects == [("beta", "disconnect"), ("alpha", "disconnect")]
