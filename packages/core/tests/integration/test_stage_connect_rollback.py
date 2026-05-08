"""Group C: Stage connect rollback. Negative-behaviour integration tests.

Covers test-plan items C1-C6 from
`docs/test-plans/0027-stage-integration-tests.md` — the rollback path
that fires when any harness's `connect()` raises mid-way through
`Stage.connect()`.

The contract under test is the one closed by R4 in the comprehensive
review: the rollback must disconnect every harness up to AND INCLUDING
the failing one (the failing harness's connect() may have opened
resources before raising), in reverse registration order, swallowing any
disconnect-time failures with structured WARNING logs so the rollback
itself never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    _RecordingMockTransport,
    _ResourceLeakingMockTransport,
    mapped_bridge_for,
    single_transport_bridge,
)

# ---------------------------------------------------------------------------
# C1 — failing connect rolls back every connected sibling
# ---------------------------------------------------------------------------


async def test_stage_connect_should_disconnect_already_connected_siblings_when_a_later_transport_fails(
    allowlist_yaml_path: Path,
) -> None:
    """C1. The first two transports connect cleanly; the third raises.
    Stage.connect() raises StageConnectError and BOTH already-connected
    transports report `is_connected() is False` after the raise.

    Covers  §Validation "Connect rollback cleanly leaves no
    transport up".
    """
    from admiral import Harness
    from admiral.stage import Stage, StageConnectError
    from admiral.transports import MockTransport

    alpha_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )
    beta_h = Harness(MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"))
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

    with pytest.raises(StageConnectError) as excinfo:
        await stage.connect()

    assert excinfo.value.failing_transport == "gamma"
    assert not alpha_h.is_connected()
    assert not beta_h.is_connected()
    assert not gamma_h.is_connected()


# ---------------------------------------------------------------------------
# C2 — failing connect's own resources are released
# ---------------------------------------------------------------------------


async def test_stage_connect_should_disconnect_the_failing_transport_itself(
    allowlist_yaml_path: Path,
) -> None:
    """C2. The R4 fix: the failing transport's connect() may have opened
    resources before raising. The rollback path must run disconnect() on
    the failing transport too. Observed via a sentinel counter the
    `_ResourceLeakingMockTransport` increments on connect entry and
    decrements on disconnect.

    Covers R4;  §Validation "Connect rollback disconnects the
    failing transport too".
    """
    from admiral import Harness
    from admiral.stage import Stage, StageConnectError
    from admiral.transports import MockTransport

    sentinel = [0]  # mutable so the transport can mutate from inside connect/disconnect

    alpha_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )
    beta_h = _harness_over(
        _ResourceLeakingMockTransport(
            sentinel=sentinel,
            fail_connect=RuntimeError("beta broker unreachable after socket open"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h},
        bridge=mapped_bridge_for("alpha", "beta"),
    )

    with pytest.raises(StageConnectError):
        await stage.connect()

    # The sentinel goes back to zero only if the failing transport's
    # disconnect() was actually called by the rollback path. (Sibling
    # rollback is C1's behaviour; this test focuses on the failing-
    # transport path, R4.)
    assert sentinel[0] == 0, (
        f"resource leak: sentinel={sentinel[0]} after failed connect; "
        f"the failing transport's disconnect() did not run"
    )


# ---------------------------------------------------------------------------
# C3 — sibling rollback disconnect failure is swallowed and logged
# ---------------------------------------------------------------------------


async def test_stage_connect_should_surface_the_original_connect_error_when_rollback_disconnect_also_fails(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C3. The rollback iterates already-connected siblings and calls
    disconnect on each. If a sibling's disconnect itself raises, the
    rollback must keep going and the original connect failure must be
    the one that surfaces. The disconnect failure is recorded as a
    structured WARNING for audit.

    Covers R15 (rollback isolation);  §Monitoring structured-log
    assertions.
    """
    from admiral.stage import Stage, StageConnectError

    alpha_h = _harness_over(
        _FailingMockTransport(
            # Connects fine; disconnect raises during rollback.
            fail_disconnect=RuntimeError("alpha disconnect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    beta_h = _harness_over(
        _FailingMockTransport(
            fail_connect=RuntimeError("beta connect failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h},
        bridge=mapped_bridge_for("alpha", "beta"),
    )

    with caplog.at_level(logging.WARNING, logger="admiral.stage"):
        with pytest.raises(StageConnectError) as excinfo:
            await stage.connect()

    # The exception that surfaces is the original connect failure (named
    # via the typed attribute, not via __cause__ which is an
    # implementation-mechanism detail).
    assert excinfo.value.failing_transport == "beta"

    # The rollback's disconnect failure is recorded as a structured WARNING
    # with the transport name on the LogRecord.
    sibling_warnings = [
        r for r in caplog.records if r.getMessage() == "stage_rollback_sibling_disconnect_failed"
    ]
    assert len(sibling_warnings) == 1
    assert sibling_warnings[0].transport == "alpha"


# ---------------------------------------------------------------------------
# C4 — failing transport's own disconnect failure is swallowed and logged
# ---------------------------------------------------------------------------


async def test_stage_connect_should_log_warning_when_failing_transport_disconnect_itself_raises(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C4. The other half of R4: when the failing transport itself also
    fails to disconnect during rollback, the original connect failure
    still surfaces and the rollback's disconnect failure is recorded
    against the FAILING transport (distinct event name from C3's
    sibling event).

    Covers R4 + R15.
    """
    from admiral.stage import Stage, StageConnectError

    only_h = _harness_over(
        _FailingMockTransport(
            fail_connect=RuntimeError("connect failure"),
            fail_disconnect=RuntimeError("disconnect also failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"only": only_h},
        bridge=single_transport_bridge("only"),
    )

    with caplog.at_level(logging.WARNING, logger="admiral.stage"):
        with pytest.raises(StageConnectError):
            await stage.connect()

    failing_warnings = [
        r
        for r in caplog.records
        if r.getMessage() == "stage_rollback_failing_transport_disconnect_failed"
    ]
    assert len(failing_warnings) == 1
    assert failing_warnings[0].transport == "only"


# ---------------------------------------------------------------------------
# C5 — state stays NEW after rollback (asserted via behaviour)
# ---------------------------------------------------------------------------


async def test_stage_connect_should_leave_state_as_new_after_rollback(
    allowlist_yaml_path: Path,
) -> None:
    """C5. After a failed connect + rollback, the state is NEW, not
    CONNECTED or DISCONNECTED. Asserted indirectly via the StageStateError
    message a subsequent scenario() call produces — its `match=` names
    the state, distinguishing this case from B3/B4.

    Covers R7 + the rollback->NEW state machine property.
    """
    from admiral.stage import Stage, StageConnectError, StageStateError

    only_h = _harness_over(
        _FailingMockTransport(
            fail_connect=RuntimeError("boom"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )

    stage = Stage(
        harnesses={"only": only_h},
        bridge=single_transport_bridge("only"),
    )

    with pytest.raises(StageConnectError):
        await stage.connect()

    # State is NEW (not DISCONNECTED), so scenario() complains about
    # being in the `new` state — the same diagnostic B3 produces.
    with pytest.raises(StageStateError, match="new"):
        stage.scenario("any-name")


# ---------------------------------------------------------------------------
# C6 — no transports attempted after the first failure
# ---------------------------------------------------------------------------


async def test_stage_connect_should_attempt_no_further_transports_after_first_failure(
    allowlist_yaml_path: Path,
) -> None:
    """C6. The connect loop is fail-fast. After the second transport
    raises, the third's connect() is never called.

    Covers  §Implementation "fail-fast" guarantee.
    """
    from admiral.stage import Stage, StageConnectError

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
            fail_connect=RuntimeError("beta connect failed"),
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

    with pytest.raises(StageConnectError):
        await stage.connect()

    # Gamma never appears in the ledger: not connect, not disconnect.
    gamma_calls = [entry for entry in ledger if entry[0] == "gamma"]
    assert gamma_calls == [], f"gamma should not have been touched: {ledger}"

    # Alpha was connected then rolled back.
    alpha_calls = [entry for entry in ledger if entry[0] == "alpha"]
    assert alpha_calls == [("alpha", "connect"), ("alpha", "disconnect")]
