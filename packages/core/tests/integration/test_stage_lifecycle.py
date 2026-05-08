"""Group B: Stage lifecycle state machine. Negative-behaviour integration tests.

Covers test-plan items B1-B6 from
`docs/test-plans/0027-stage-integration-tests.md` — the explicit state
machine the Stage uses to reject re-use, double-connect, and out-of-order
calls.

Re-use is intentionally not supported: a disconnected Stage cannot be
reconnected. Per  §Implementation Stage docstring, the user
constructs a fresh Stage instead. These tests pin that contract.

Lifecycle here exercises only the success-path connect / disconnect
sequence; rollback on connect failure is Group C, disconnect aggregation
on multi-error is Group D.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import _harness_over, _RecordingMockTransport, make_mapped_bridge

# ---------------------------------------------------------------------------
# B1 — connect() called twice
# ---------------------------------------------------------------------------


async def test_stage_connect_should_raise_state_error_when_called_twice(
    two_harnesses: dict[str, Any],
) -> None:
    """B1. The state machine forbids double-connect. The error message
    names the current state so the consumer knows whether to call
    disconnect first or construct a fresh Stage.

    Covers R7 (explicit state machine);
     §Validation "State machine rejects re-use".
    """
    from admiral.stage import Stage, StageStateError

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())
    await stage.connect()
    try:
        with pytest.raises(StageStateError, match="connected"):
            await stage.connect()
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# B2 — connect() after disconnect()
# ---------------------------------------------------------------------------


async def test_stage_connect_should_raise_state_error_when_called_after_disconnect(
    two_harnesses: dict[str, Any],
) -> None:
    """B2. Reconnect is not supported; consumers must construct a fresh
    Stage. The error message names the current state so the consumer can
    distinguish this from B1's double-connect.

    Covers R7.
    """
    from admiral.stage import Stage, StageStateError

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())
    await stage.connect()
    await stage.disconnect()

    with pytest.raises(StageStateError, match="disconnected"):
        await stage.connect()


# ---------------------------------------------------------------------------
# B3 — scenario() called before connect()
# ---------------------------------------------------------------------------


def test_stage_scenario_should_raise_state_error_when_called_before_connect(
    two_harnesses: dict[str, Any],
) -> None:
    """B3. Opening a scenario requires the Stage to be connected. The
    error message names `new` so the consumer knows the Stage was never
    connected, distinguishing this from B4's post-disconnect case.

    Covers R7;
     §Validation "stage.scenario() rejects pre-connect".
    """
    from admiral.stage import Stage, StageStateError

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())

    with pytest.raises(StageStateError, match="new"):
        stage.scenario("any-name")


# ---------------------------------------------------------------------------
# B4 — scenario() called after disconnect()
# ---------------------------------------------------------------------------


async def test_stage_scenario_should_raise_state_error_when_called_after_disconnect(
    two_harnesses: dict[str, Any],
) -> None:
    """B4. After disconnect the Stage is terminal; opening a new
    scenario fails for the same reason connect() does (B2).

    Covers R7.
    """
    from admiral.stage import Stage, StageStateError

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())
    await stage.connect()
    await stage.disconnect()

    with pytest.raises(StageStateError, match="disconnected"):
        stage.scenario("any-name")


# ---------------------------------------------------------------------------
# B5a — disconnect() called twice does not raise (idempotent surface)
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_not_raise_when_called_a_second_time(
    two_harnesses: dict[str, Any],
) -> None:
    """B5a. A `finally: await stage.disconnect()` block must be safe
    even when the stage is already disconnected. The second call returns
    without raising — that is the surface contract a finally-block
    relies on.

    Covers R7;
     §Validation "disconnect() is idempotent".
    """
    from admiral.stage import Stage

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())
    await stage.connect()
    await stage.disconnect()

    # The whole assertion: no raise.
    await stage.disconnect()


# ---------------------------------------------------------------------------
# B5b — disconnect() second call does not re-invoke harness disconnect
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_not_re_invoke_harness_disconnect_on_second_call(
    allowlist_yaml_path: Path,
) -> None:
    """B5b. Beyond not raising (B5a), the second `disconnect()` must
    not re-enter each harness's disconnect — that would be wasted work
    and would fire any per-disconnect side-effects twice. Observed via
    a recording transport's call ledger.

    Covers R7; complements B5a with the call-count observable.
    """
    from admiral.stage import Stage

    ledger: list[tuple[str, str]] = []
    nats_h = _harness_over(
        _RecordingMockTransport(
            ledger=ledger,
            name="nats",
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    kafka_h = _harness_over(
        _RecordingMockTransport(
            ledger=ledger,
            name="kafka",
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    stage = Stage(
        harnesses={"nats": nats_h, "kafka": kafka_h},
        bridge=make_mapped_bridge(),
    )
    await stage.connect()
    await stage.disconnect()
    await stage.disconnect()  # second call: must be a no-op

    disconnects = [entry for entry in ledger if entry[1] == "disconnect"]
    # Exactly two disconnect calls total — one per harness, on the
    # FIRST disconnect. The second disconnect did not reach the harnesses.
    assert disconnects == [("kafka", "disconnect"), ("nats", "disconnect")]


# ---------------------------------------------------------------------------
# B6 — disconnect() called when never connected
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_be_idempotent_when_never_connected(
    two_harnesses: dict[str, Any],
) -> None:
    """B6. A test that constructs a Stage but errors before reaching
    `await stage.connect()` will still hit `finally: await
    stage.disconnect()`. That call must be a no-op: harnesses were never
    connected and must not be touched.

    Covers R7.
    """
    from admiral.stage import Stage

    stage = Stage(harnesses=two_harnesses, bridge=make_mapped_bridge())

    # Never called connect(). Disconnect must be a no-op.
    await stage.disconnect()

    for h in two_harnesses.values():
        assert not h.is_connected()
