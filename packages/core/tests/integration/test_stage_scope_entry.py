"""Group E: Stage scenario scope entry. Negative-behaviour integration tests.

Covers test-plan items E1-E5 from
`docs/test-plans/0027-stage-integration-tests.md` — the bridge protocol
enforcement that fires inside `_StageScenarioScope.__aenter__`. Eager
child minting (per ADR-0027 §Implementation, R8 in the comprehensive
review) gives bridge translation errors a deterministic firing point at
scope entry rather than racing the test body, and re-exercises the
bridge's per-transport distinctness against the actual logical id.

Group E ships the scope skeleton; the DSL methods (`expect`, `publish`,
`on`) and per-child subscription state land with Groups F and G.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from .conftest import (
    _PerTransportRaisingBridge,
    _RaisingBridge,
    _SmokeTestEscapeBridge,
    mapped_bridge_for,
)

# ---------------------------------------------------------------------------
# E1 — fresh() raises during scope entry
# ---------------------------------------------------------------------------


async def test_stage_scenario_should_raise_translation_error_when_fresh_raises(
    two_harnesses: dict[str, Any],
) -> None:
    """E1. The first thing `__aenter__` does is `await bridge.fresh()`.
    A consumer bridge that raises here surfaces as a typed
    BridgeTranslationError naming the `fresh` method, with the original
    exception on `.original` (mirroring ADR-0019's CorrelationPolicyError
    shape) so the consumer does not have to walk __cause__.

    Covers R19; ADR-0027 §Validation "BridgeTranslationError wraps
    consumer-bridge exceptions".
    """
    from admiral.stage import BridgeTranslationError, Stage

    injected = RuntimeError("fresh said no")
    bridge = _RaisingBridge(raise_on="fresh", exc=injected)

    # Construction succeeds because `fresh` is not exercised at startup
    # (only `to_wire` is, in the smoke test). The failure surfaces at
    # scope entry.
    stage = Stage(harnesses=two_harnesses, bridge=bridge)
    await stage.connect()
    try:
        with pytest.raises(BridgeTranslationError) as excinfo:
            async with stage.scenario("x"):
                pass
    finally:
        await stage.disconnect()

    assert excinfo.value.method == "fresh"
    assert excinfo.value.transport is None
    assert excinfo.value.original is injected
    assert excinfo.value.bridge_class == "_RaisingBridge"


# ---------------------------------------------------------------------------
# E2 — to_wire raises during eager mint, on a specific transport
# ---------------------------------------------------------------------------


async def test_stage_scenario_should_raise_translation_error_when_to_wire_raises_during_eager_mint(
    two_harnesses: dict[str, Any],
) -> None:
    """E2. Eager minting (R8) calls `to_wire` once per registered
    transport at scope entry. A bridge that succeeds for the first
    transport but raises for the second surfaces a BridgeTranslationError
    naming the failing transport — the failure point is deterministic at
    `__aenter__`, not racing the test body's first DSL call.

    Covers R8 (eager mint), R19 (.original).
    """
    from admiral.stage import BridgeTranslationError, Stage

    bridge = _PerTransportRaisingBridge(raises_for="kafka")
    stage = Stage(harnesses=two_harnesses, bridge=bridge)
    await stage.connect()
    try:
        with pytest.raises(BridgeTranslationError) as excinfo:
            async with stage.scenario("x"):
                pass
    finally:
        await stage.disconnect()

    assert excinfo.value.method == "to_wire"
    assert excinfo.value.transport == "kafka"
    assert isinstance(excinfo.value.original, RuntimeError)


# ---------------------------------------------------------------------------
# E3 — per-scope re-validation catches the smoke-test escape
# ---------------------------------------------------------------------------


async def test_stage_scenario_should_raise_ambiguity_error_when_real_logical_id_collides(
    two_harnesses: dict[str, Any],
) -> None:
    """E3. The Stage's two-pass distinctness check: a bridge that returns
    distinct values for the synthetic input passes startup smoke-test,
    but if it collides on the real `bridge.fresh()` value at scope
    entry, the per-scope re-validation catches it. The scope never
    enters; subsequent scenarios may be opened without contamination.

    Covers R6 (smoke-test claim weakened — the second pass is what
    actually defends against in-flight collisions).
    """
    from admiral.stage import BridgeAmbiguityError, Stage

    bridge = _SmokeTestEscapeBridge()
    stage = Stage(harnesses=two_harnesses, bridge=bridge)
    await stage.connect()
    try:
        with pytest.raises(BridgeAmbiguityError) as excinfo:
            async with stage.scenario("x"):
                pass
    finally:
        await stage.disconnect()

    # The colliding pair is named on the typed attribute (sorted).
    assert excinfo.value.transports == ("kafka", "nats")


# ---------------------------------------------------------------------------
# E4 — partial-mint cleanup leaves the Stage usable for a fresh scope
# ---------------------------------------------------------------------------


async def test_stage_scenario_should_clean_up_partially_minted_state_when_mint_fails(
    allowlist_yaml_path: Path,
) -> None:
    """E4. With three transports where the third's `to_wire` raises, the
    first two children are minted before the failure. The scope's
    cleanup must discard the partial state so that opening a *fresh*
    scenario subsequently works against the same Stage with a working
    bridge.

    Group E ships scope skeleton only; subscriber-leak observation lands
    with Group G's teardown isolation. The observable here is:
    "subsequent scope works" — the Stage is not left in a state that
    poisons future scopes.

    Covers R8 (eager mint cleanup path); ADR-0027 §Implementation
    `_StageScenarioScope._teardown` on `__aenter__` failure.
    """
    from admiral import Harness
    from admiral.stage import BridgeTranslationError, Stage
    from admiral.transports import MockTransport

    alpha_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )
    beta_h = Harness(MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"))
    gamma_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )

    # Failing bridge: gamma's to_wire raises during eager mint.
    failing_bridge = _PerTransportRaisingBridge(raises_for="gamma")

    stage = Stage(
        harnesses={"alpha": alpha_h, "beta": beta_h, "gamma": gamma_h},
        bridge=failing_bridge,
    )
    await stage.connect()
    try:
        # First scope: eager mint fails for gamma after alpha and beta minted.
        with pytest.raises(BridgeTranslationError):
            async with stage.scenario("first-fails"):
                pass

        # Second scope on the SAME Stage with a working bridge: the
        # construction-time bridge is locked in, so we cannot swap. The
        # observable that matters is that the Stage is still in
        # CONNECTED state and another scenario() call goes through the
        # state guard cleanly (and predictably hits the same mint
        # failure, not some confused state error).
        with pytest.raises(BridgeTranslationError) as excinfo:
            async with stage.scenario("second-also-fails"):
                pass

        # The second failure is the SAME failure mode (same transport
        # named), proving the Stage was not left in a confused state by
        # the first scope's partial mint.
        assert excinfo.value.method == "to_wire"
        assert excinfo.value.transport == "gamma"
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# E5 — scope is not re-entrant
# ---------------------------------------------------------------------------


async def test_stage_scenario_scope_should_not_be_re_entrant(
    two_harnesses: dict[str, Any],
) -> None:
    """E5. A `_StageScenarioScope` is one-shot. Calling `__aenter__`
    twice on the same instance is a programming error — the scope's
    state machine assumes one entry per instance, and re-entry would
    mint duplicate children, double-call `bridge.fresh()`, and leave
    teardown ambiguous about which entry to undo.

    Covers ADR-0027 §Validation "StageScenarioScope not re-entrant".
    """
    from admiral.stage import Stage, StageStateError

    stage = Stage(harnesses=two_harnesses, bridge=mapped_bridge_for("nats", "kafka"))
    await stage.connect()
    try:
        scope = stage.scenario("x")
        await scope.__aenter__()
        try:
            with pytest.raises(StageStateError, match="not re-entrant"):
                await scope.__aenter__()
        finally:
            await scope.__aexit__(None, None, None)
    finally:
        await stage.disconnect()
