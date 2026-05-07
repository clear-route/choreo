"""Group D: Stage disconnect aggregation. Negative-behaviour integration tests.

Covers test-plan items D1-D4 from
`docs/test-plans/0027-stage-integration-tests.md` — the contract that
`Stage.disconnect()` is best-effort across every connected harness, and
that any disconnect-time failures are surfaced as a PEP 654
`ExceptionGroup` so consumers can use `except*` to walk individual
errors.

The grouping mechanism is what closed R11 in the comprehensive review:
the previous draft used a custom errors-tuple that did not interoperate
with `except*` or `traceback.format_exception`. Subclassing
`ExceptionGroup` gets us both for free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    _RecordingMockTransport,
    mapped_bridge_for,
    single_transport_bridge,
)

# ---------------------------------------------------------------------------
# D1 — single-transport disconnect failure surfaces as ExceptionGroup
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_raise_exception_group_when_one_transport_fails(
    allowlist_yaml_path: Path,
) -> None:
    """D1. Even a single failure surfaces as a `StageDisconnectError`
    that IS an `ExceptionGroup` — uniform shape across single and
    multi-error cases. The `.exceptions` tuple is length 1 and contains
    the original exception. `try/except* StageDisconnectError` walks it
    correctly.

    Covers R11 (ExceptionGroup); ADR-0027 §Validation
    "StageDisconnectError is an ExceptionGroup".
    """
    from admiral import Harness
    from admiral.stage import Stage, StageDisconnectError
    from admiral.transports import MockTransport

    only_h = _harness_over(
        _FailingMockTransport(
            fail_disconnect=RuntimeError("only disconnect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    other_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )

    stage = Stage(
        harnesses={"only": only_h, "other": other_h},
        bridge=mapped_bridge_for("only", "other"),
    )
    await stage.connect()

    with pytest.raises(StageDisconnectError) as excinfo:
        await stage.disconnect()

    assert isinstance(excinfo.value, ExceptionGroup)
    assert len(excinfo.value.exceptions) == 1
    inner = excinfo.value.exceptions[0]
    assert isinstance(inner, RuntimeError)
    assert "only disconnect failed" in str(inner)


# ---------------------------------------------------------------------------
# D2 — multi-transport disconnect failures are grouped
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_raise_exception_group_when_multiple_transports_fail(
    allowlist_yaml_path: Path,
) -> None:
    """D2. Two failing disconnects surface as one ExceptionGroup whose
    `.exceptions` carries both originals. `except*` walks them in the
    user's code without manual unpacking.

    Covers R11.
    """
    from admiral.stage import Stage, StageDisconnectError

    alpha_h = _harness_over(
        _FailingMockTransport(
            fail_disconnect=RuntimeError("alpha disconnect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    beta_h = _harness_over(
        _FailingMockTransport(
            fail_disconnect=RuntimeError("beta disconnect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h},
        bridge=mapped_bridge_for("alpha", "beta"),
    )
    await stage.connect()

    with pytest.raises(StageDisconnectError) as excinfo:
        await stage.disconnect()

    messages = {str(exc) for exc in excinfo.value.exceptions}
    assert messages == {"alpha disconnect failed", "beta disconnect failed"}

    # The except* idiom works on the typed group.
    walked: list[str] = []
    try:
        raise excinfo.value
    except* RuntimeError as eg:
        for exc in eg.exceptions:
            walked.append(str(exc))
    assert sorted(walked) == sorted(messages)


# ---------------------------------------------------------------------------
# D3 — disconnect attempts every transport, even when one fails
# ---------------------------------------------------------------------------


async def test_stage_disconnect_should_attempt_every_transport_even_when_one_fails(
    allowlist_yaml_path: Path,
) -> None:
    """D3. With three transports where the middle one's disconnect raises,
    the other two must still have their disconnect() called, and in
    reverse registration order. The middle one's failure does not abort
    the loop.

    Covers R11; ADR-0027 §Implementation "disconnect best-effort across
    all transports".
    """
    from admiral.stage import Stage, StageDisconnectError

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
        _FailingMockTransport(
            fail_disconnect=RuntimeError("beta disconnect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    gamma_h = _harness_over(
        _RecordingMockTransport(
            ledger=ledger,
            name="gamma",
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h, "gamma": gamma_h},
        bridge=mapped_bridge_for("alpha", "beta", "gamma"),
    )
    await stage.connect()

    with pytest.raises(StageDisconnectError):
        await stage.disconnect()

    # Filter for disconnect entries only; assert reverse-registration
    # order across alpha and gamma. Beta's recording transport is the
    # failing one, so it does not appear in the ledger via that mechanism.
    disconnects = [entry for entry in ledger if entry[1] == "disconnect"]
    assert disconnects == [("gamma", "disconnect"), ("alpha", "disconnect")]


# ---------------------------------------------------------------------------
# D4 — state transitions to DISCONNECTED even after failure
# ---------------------------------------------------------------------------


def _failing_disconnect_stage(allowlist_yaml_path: Path):
    """Local helper: a single-transport Stage whose only transport's
    disconnect always raises. Used by D4a / D4b to remove construction
    boilerplate when the failure mode is the same."""
    from admiral.stage import Stage

    only_h = _harness_over(
        _FailingMockTransport(
            fail_disconnect=RuntimeError("disconnect always fails"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    return Stage(
        harnesses={"only": only_h},
        bridge=single_transport_bridge("only"),
    )


async def test_stage_disconnect_should_be_idempotent_after_a_disconnect_failure(
    allowlist_yaml_path: Path,
) -> None:
    """D4a. A disconnect that raises still leaves the Stage in a defined
    terminal state — a subsequent disconnect is a clean no-op. The
    `finally: await stage.disconnect()` pattern stays safe even after
    the first attempt blew up.

    Covers R7.
    """
    from admiral.stage import Stage, StageDisconnectError

    stage = _failing_disconnect_stage(allowlist_yaml_path)
    assert isinstance(stage, Stage)
    await stage.connect()

    with pytest.raises(StageDisconnectError):
        await stage.disconnect()

    # The whole assertion: second disconnect does not raise.
    await stage.disconnect()


async def test_stage_connect_should_be_rejected_after_a_disconnect_failure(
    allowlist_yaml_path: Path,
) -> None:
    """D4b. A failed disconnect transitions the Stage to DISCONNECTED
    (not stuck CONNECTED). Subsequent `connect()` raises StageStateError
    naming the `disconnected` state, distinguishing it from B3's
    pre-connect case.

    Covers R7; the lifecycle invariant that disconnect failure does
    NOT leave the Stage retry-eligible.
    """
    from admiral.stage import StageDisconnectError, StageStateError

    stage = _failing_disconnect_stage(allowlist_yaml_path)
    await stage.connect()

    with pytest.raises(StageDisconnectError):
        await stage.disconnect()

    with pytest.raises(StageStateError, match="disconnected"):
        await stage.connect()
