"""Group K: Stage Result and Handle shape integration tests.

Covers test-plan items K1-K4. The contract under test is the
user-facing observability of cross-transport scenarios:

* `result.by_transport` is a per-transport view of `handles`, present
  on Stage results, empty on single-Harness results — no surprising
  empty-string key.
* `Handle.transport` reflects the `on=` selector for Stage handles
  and is `None` for single-Harness handles.
* `Handle`'s repr surfaces the transport name so failure diagnostics
  in pytest verbose output are unambiguous about which side a
  failing handle belongs to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from admiral import Harness
from admiral.matchers import field_equals
from admiral.scenario import Outcome
from admiral.stage import Stage
from admiral.transports import MockTransport

from .conftest import mapped_bridge_for, single_transport_bridge

_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# K1 — by_transport view: Stage populates per-transport, single-Harness empty
# ---------------------------------------------------------------------------


async def test_stage_scenario_result_by_transport_should_contain_only_real_transport_names(
    two_harnesses: dict[str, Harness],
) -> None:
    """K1. A Stage scenario's `result.by_transport` carries one entry
    per touched transport, keyed by the transport name. Handles whose
    `transport` is None (single-Harness scenarios cannot produce
    these via Stage code paths, but a hypothetical mixed list would)
    are omitted from the view — `result.by_transport.get("any-name")
    is None` for absent transports rather than an empty-string key.

    The assertion bracket: a Stage with two transports, each with one
    expectation, produces `by_transport` with exactly those two keys
    and one handle each. Both handles' `transport` matches the key
    they hash under.
    """
    stage = Stage(harnesses=two_harnesses, bridge=mapped_bridge_for("nats", "kafka"))
    await stage.connect()
    try:
        async with stage.scenario("k1") as scope:
            scope.expect("topic.n", _PLACEHOLDER_MATCHER, on="nats")
            scope.expect("topic.k", _PLACEHOLDER_MATCHER, on="kafka")
            result = await scope.await_all(timeout_ms=20)
    finally:
        await stage.disconnect()

    # Two transports touched, two keys.
    assert set(result.by_transport.keys()) == {"nats", "kafka"}
    # One handle each, transport field matches the key.
    assert len(result.by_transport["nats"]) == 1
    assert result.by_transport["nats"][0].transport == "nats"
    assert len(result.by_transport["kafka"]) == 1
    assert result.by_transport["kafka"][0].transport == "kafka"
    # No surprise key.
    assert result.by_transport.get("") is None
    assert result.by_transport.get("ghost") is None


# ---------------------------------------------------------------------------
# K2 — Handle.transport matches the on= selector immediately on return
# ---------------------------------------------------------------------------


async def test_stage_handle_transport_should_match_the_on_selector(
    two_harnesses: dict[str, Harness],
) -> None:
    """K2. The `Handle.transport` attribute reflects the `on=` value
    AT THE MOMENT `expect()` returns — set inside the constructor,
    not via post-hoc mutation. (F7 verifies the same property in a
    different shape; K2 generalises to multi-transport.)
    """
    stage = Stage(harnesses=two_harnesses, bridge=mapped_bridge_for("nats", "kafka"))
    await stage.connect()
    try:
        async with stage.scenario("k2") as scope:
            h_nats = scope.expect("topic.n", _PLACEHOLDER_MATCHER, on="nats")
            h_kafka = scope.expect("topic.k", _PLACEHOLDER_MATCHER, on="kafka")
            # Read immediately — no await, no scheduling, no race.
            assert h_nats.transport == "nats"
            assert h_kafka.transport == "kafka"
            await scope.await_all(timeout_ms=10)
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# K3 — single-Harness handle has transport=None
# ---------------------------------------------------------------------------


async def test_single_harness_handle_transport_should_be_none(
    allowlist_yaml_path: Path,
) -> None:
    """K3. Handles produced by `Harness.scenario(...).expect(...)`
    (i.e. without Stage involvement) have `Handle.transport is None`.
    The framework never sets transport for single-Harness handles —
    the field exists for Stage's per-transport routing only.
    """
    harness = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )
    await harness.connect()
    try:
        async with harness.scenario("k3") as scope:
            handle = scope.expect("topic", _PLACEHOLDER_MATCHER)
            # Read immediately; no need to advance the scope to the
            # triggered state — `transport` is set at construction.
            assert handle.transport is None
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# K4 — Handle repr includes transport name for Stage handles
# ---------------------------------------------------------------------------


async def test_stage_handle_repr_should_include_transport_name(
    allowlist_yaml_path: Path,
) -> None:
    """K4. `repr(handle)` for a Stage handle includes the transport
    name. Failure diagnostics in pytest verbose output and in CI logs
    name which side of a multi-transport scenario a failing handle
    belongs to without the consumer having to dereference fields.
    """
    only_h = Harness(MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"))
    stage = Stage(harnesses={"only": only_h}, bridge=single_transport_bridge("only"))
    await stage.connect()
    try:
        async with stage.scenario("k4") as scope:
            handle = scope.expect("topic", _PLACEHOLDER_MATCHER, on="only")
            assert "only" in repr(handle)
            await scope.await_all(timeout_ms=10)
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# K5 — Stage result carries the scope's logical id as correlation_id
# ---------------------------------------------------------------------------


async def test_a_stage_scenario_result_should_expose_the_scope_logical_id_as_correlation_id(
    two_harnesses: dict[str, Harness],
) -> None:
    """K5. The reporter populates
    `scenario.correlation_id` for Stage scenarios from
    `result.correlation_id`. The value is the logical id minted by
    `bridge.fresh()` at scope entry — the same id that maps to per-
    transport wire ids via the bridge.

    Default `MappedBridge.fresh()` uses `secrets.token_hex(16)` (32 hex
    chars). The test asserts shape, not the specific value.
    """
    stage = Stage(
        harnesses=two_harnesses,
        bridge=mapped_bridge_for("nats", "kafka"),
    )
    await stage.connect()
    try:
        async with stage.scenario("k5") as scope:
            scope.expect("topic", _PLACEHOLDER_MATCHER, on="nats")
            result = await scope.await_all(timeout_ms=10)
    finally:
        await stage.disconnect()

    assert result.correlation_id is not None
    assert isinstance(result.correlation_id, str)
    # Default secrets.token_hex(16) → 32 hex chars; the bridge's
    # synthetic-smoke-test id ("STAGE-VALIDATION-...") is rejected
    # by Stage as it would short-circuit collision detection. So
    # the value here is whatever bridge.fresh() returned at __aenter__.
    assert len(result.correlation_id) >= 16


# Keep `pytest` and `Outcome` imports used somewhere if the test file
# is otherwise tight; remove if unused. (Currently both are used by
# imports above; smoke check via lint catches drift.)
_ = pytest
_ = Outcome
