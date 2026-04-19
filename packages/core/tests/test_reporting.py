"""Behavioural tests for the scenario observer seam (PRD-007 §2).

The observer registry in `choreo._reporting` is the single library-surface
contact point between `core` and the future external `choreo-reporter`
package. Tests here assert behaviours observable by a reporter: emission
timing, payload content, isolation, and failure containment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(allowlist_yaml_path: Path):
    from choreo import Harness
    from choreo.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


@pytest.fixture(autouse=True)
def _clean_observer_registry():
    """Snapshot any pre-existing observers (e.g. those a surrounding
    choreo-reporter plugin registered) and restore them after the test.
    The test body sees an empty registry so its assertions about
    registration/notification are deterministic, but the outer session's
    observer is not poisoned."""
    from choreo._reporting import _observers

    snapshot = list(_observers)
    _observers.clear()
    try:
        yield
    finally:
        _observers.clear()
        _observers.extend(snapshot)


# ---------------------------------------------------------------------------
# Observer registry — pure unit behaviours
# ---------------------------------------------------------------------------


def test_registering_an_observer_twice_should_only_keep_one_copy() -> None:
    from choreo._reporting import _observers, register_observer

    def cb(result, nodeid, completed_normally):
        pass

    register_observer(cb)
    register_observer(cb)
    assert _observers.count(cb) == 1


def test_unregistering_an_unknown_observer_should_be_a_no_op() -> None:
    from choreo._reporting import unregister_observer

    def cb(result, nodeid, completed_normally):
        pass

    unregister_observer(cb)  # should not raise


# ---------------------------------------------------------------------------
# Emission — via a running scenario
# ---------------------------------------------------------------------------


async def test_a_registered_observer_should_receive_the_result_from_await_all(
    harness,
) -> None:
    from choreo._reporting import register_observer
    from choreo.matchers import field_equals

    received: list[tuple] = []

    def cb(result, nodeid, completed_normally):
        received.append((result, nodeid, completed_normally))

    register_observer(cb)

    async with harness.scenario("happy") as s:
        s.expect("t.obs", field_equals("k", "v"))
        s = s.publish("t.obs", {"k": "v"})
        await s.await_all(timeout_ms=200)

    assert len(received) == 1
    result, _nodeid, completed_normally = received[0]
    assert result.name == "happy"
    assert result.passed is True
    assert completed_normally is True


async def test_an_observer_should_receive_the_nodeid_from_the_contextvar(
    harness,
) -> None:
    from choreo._reporting import current_test_nodeid, register_observer
    from choreo.matchers import field_equals

    received_nodeid: list[str | None] = []

    def cb(result, nodeid, completed_normally):
        received_nodeid.append(nodeid)

    register_observer(cb)

    token = current_test_nodeid.set("tests/test_reporting.py::t_example[p1]")
    try:
        async with harness.scenario("ctx") as s:
            s.expect("t.ctx", field_equals("k", "v"))
            s = s.publish("t.ctx", {"k": "v"})
            await s.await_all(timeout_ms=200)
    finally:
        current_test_nodeid.reset(token)

    assert received_nodeid == ["tests/test_reporting.py::t_example[p1]"]


async def test_an_observer_should_receive_none_nodeid_when_no_test_context_is_set(
    harness,
) -> None:
    from choreo._reporting import current_test_nodeid, register_observer
    from choreo.matchers import field_equals

    received_nodeid: list[str | None] = []

    def cb(result, nodeid, completed_normally):
        received_nodeid.append(nodeid)

    register_observer(cb)

    # An upstream plugin (e.g. choreo-reporter when installed) may have
    # set the contextvar for the outer test. Reset it to exercise the
    # no-context branch of `_emit`.
    token = current_test_nodeid.set(None)
    try:
        async with harness.scenario("no-ctx") as s:
            s.expect("t.noctx", field_equals("k", "v"))
            s = s.publish("t.noctx", {"k": "v"})
            await s.await_all(timeout_ms=200)
    finally:
        current_test_nodeid.reset(token)

    assert received_nodeid == [None]


async def test_an_observer_that_raises_should_not_break_the_scenario(
    harness,
) -> None:
    from choreo._reporting import register_observer
    from choreo.matchers import field_equals

    def bad_cb(result, nodeid, completed_normally):
        raise RuntimeError("observer blew up")

    register_observer(bad_cb)

    # The scenario must complete normally despite the broken observer.
    with pytest.warns(RuntimeWarning, match="observer"):
        async with harness.scenario("survives-bad-observer") as s:
            s.expect("t.bad", field_equals("k", "v"))
            s = s.publish("t.bad", {"k": "v"})
            result = await s.await_all(timeout_ms=200)

    result.assert_passed()


async def test_unregistering_an_observer_should_stop_future_notifications(
    harness,
) -> None:
    from choreo._reporting import register_observer, unregister_observer
    from choreo.matchers import field_equals

    received: list = []

    def cb(result, nodeid, completed_normally):
        received.append(result)

    register_observer(cb)

    async with harness.scenario("first") as s:
        s.expect("t.first", field_equals("k", "v"))
        s = s.publish("t.first", {"k": "v"})
        await s.await_all(timeout_ms=200)

    unregister_observer(cb)

    async with harness.scenario("second") as s:
        s.expect("t.second", field_equals("k", "v"))
        s = s.publish("t.second", {"k": "v"})
        await s.await_all(timeout_ms=200)

    names = [r.name for r in received]
    assert names == ["first"]


# ---------------------------------------------------------------------------
# Partial emission when the scope raises
# ---------------------------------------------------------------------------


async def test_a_scope_that_raises_before_await_all_should_emit_a_partial(
    harness,
) -> None:
    """When the test body raises inside the scope before `await_all` runs,
    the reporter would otherwise see nothing. A partial `ScenarioResult`
    is emitted with `completed_normally=False` so the report can still
    show what happened (PRD-007 Decision #24)."""
    from choreo._reporting import register_observer
    from choreo.matchers import field_equals

    received: list[tuple] = []

    def cb(result, nodeid, completed_normally):
        received.append((result, completed_normally))

    register_observer(cb)

    with pytest.raises(RuntimeError, match="deliberate"):
        async with harness.scenario("blows-up") as s:
            s.expect("t.blows", field_equals("k", "v"))
            raise RuntimeError("deliberate")

    assert len(received) == 1
    result, completed_normally = received[0]
    assert completed_normally is False
    assert result.name == "blows-up"
    assert result.passed is False


async def test_a_scope_that_raises_after_await_all_should_not_emit_a_second_time(
    harness,
) -> None:
    """If `await_all` already ran, the observer was already notified. A
    subsequent raise in the scope body must not double-emit."""
    from choreo._reporting import register_observer
    from choreo.matchers import field_equals

    received: list = []

    def cb(result, nodeid, completed_normally):
        received.append(completed_normally)

    register_observer(cb)

    with pytest.raises(RuntimeError, match="post-await"):
        async with harness.scenario("once-only") as s:
            s.expect("t.once", field_equals("k", "v"))
            s = s.publish("t.once", {"k": "v"})
            await s.await_all(timeout_ms=200)
            raise RuntimeError("post-await")

    assert received == [True]


# ---------------------------------------------------------------------------
# Contextvar isolation across concurrent scenarios
# ---------------------------------------------------------------------------


async def test_concurrent_scenarios_should_each_see_their_own_nodeid(
    harness,
) -> None:
    """Two scenarios run under different contextvar values; each observer
    call carries the nodeid that was active when that scenario's
    `await_all` fired, not the other scenario's."""
    from choreo._reporting import current_test_nodeid, register_observer
    from choreo.matchers import field_equals

    captured: list[tuple[str, str | None]] = []

    def cb(result, nodeid, completed_normally):
        captured.append((result.name, nodeid))

    register_observer(cb)

    async def run_under(nodeid: str, scenario_name: str, topic: str) -> None:
        token = current_test_nodeid.set(nodeid)
        try:
            async with harness.scenario(scenario_name) as s:
                s.expect(topic, field_equals("k", "v"))
                s = s.publish(topic, {"k": "v"})
                await s.await_all(timeout_ms=200)
        finally:
            current_test_nodeid.reset(token)

    await asyncio.gather(
        run_under("nodeid-A", "scn-A", "t.iso.a"),
        run_under("nodeid-B", "scn-B", "t.iso.b"),
    )

    captured.sort()
    assert captured == [("scn-A", "nodeid-A"), ("scn-B", "nodeid-B")]


# ---------------------------------------------------------------------------
# Handle data captured for the reporter
# ---------------------------------------------------------------------------


async def test_a_matched_handle_should_expose_the_matchers_expected_shape(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("expected-shape") as s:
        handle = s.expect("t.shape", field_equals("qty", 1000))
        s = s.publish("t.shape", {"qty": 1000})
        await s.await_all(timeout_ms=200)

    assert handle.matcher_expected == {"qty": 1000}


async def test_a_rejected_handle_should_capture_the_last_rejected_payload(
    harness,
) -> None:
    """For FAIL outcomes, the reporter needs the payload that the matcher
    rejected so it can render the actual-side of the expected-vs-actual
    diff. Previously only the reason was retained."""
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("rejection-payload") as s:
        handle = s.expect("t.rej", field_equals("status", "ACCEPTED"))
        s = s.publish("t.rej", {"status": "REJECTED", "reason": "risk"})
        s = s.publish("t.rej", {"status": "PENDING", "reason": "hold"})
        await s.await_all(timeout_ms=30)

    assert handle.outcome is Outcome.FAIL
    # The last mismatched payload is retained in full (the transport adds a
    # correlation_id alongside the author's fields; the reporter sees it).
    payload = handle.last_mismatch_payload
    assert isinstance(payload, dict)
    assert payload["status"] == "PENDING"
    assert payload["reason"] == "hold"


async def test_a_handle_with_no_attempts_should_have_null_last_mismatch_payload(
    harness,
) -> None:
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("silent") as s:
        handle = s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("somewhere.else", b"x")
        await s.await_all(timeout_ms=20)

    assert handle.outcome is Outcome.TIMEOUT
    assert handle.last_mismatch_payload is None
