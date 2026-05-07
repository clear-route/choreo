"""Group L: Stage and Harness coexistence integration tests.

Covers test-plan items L1-L2.

L1 verifies the Stage feature is genuinely additive: a single test
process can construct one `Stage` (multi-transport coordinator) and
one standalone `Harness` (single-transport scenario) and run
scenarios on each without state leak. The single-Harness API has no
visible change.

L2 is a canary documenting that sharing a transport instance between
two `Harness` wrappers (one inside a `Stage`, one outside) is NOT
supported. The observable cross-talk pins the failure mode so a
future engineer cannot accidentally rely on this shape working.
"""

from __future__ import annotations

from pathlib import Path

from admiral import Harness
from admiral.matchers import field_equals
from admiral.scenario import Outcome
from admiral.stage import Stage
from admiral.transports import MockTransport

from .conftest import single_transport_bridge

_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# L1 — Stage and Harness coexist in one process
# ---------------------------------------------------------------------------


async def test_stage_and_harness_should_coexist_in_one_process(
    allowlist_yaml_path: Path,
) -> None:
    """L1. Construct a Stage with one transport AND a separate
    standalone Harness in the same process. Run a scenario on each.
    Neither leaks state into the other; both pass independently.

    The Stage and Harness are constructed with DIFFERENT
    MockTransport instances (sharing instances is L2's documented
    misuse). Both rely on the same shipped allowlist; the test
    passes a transport-name-suffixed scenario name so the two paths
    are unambiguously distinct in any debug output.
    """
    # Stage path: single-transport Stage.
    stage_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )
    stage = Stage(harnesses={"only": stage_h}, bridge=single_transport_bridge("only"))

    # Standalone Harness path: separate MockTransport instance.
    standalone_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    )

    await stage.connect()
    await standalone_h.connect()
    try:
        # Stage scenario: publish-and-expect on the only transport.
        async with stage.scenario("stage-side") as stage_scope:
            stage_handle = stage_scope.expect("topic", _PLACEHOLDER_MATCHER, on="only")
            stage_scope.publish("topic", {"status": "ok"}, on="only")
            stage_result = await stage_scope.await_all(timeout_ms=20)

        # Standalone Harness scenario: equivalent shape via the
        # type-state Scenario API (no `on=` selector — it's the
        # single-Harness path).
        async with standalone_h.scenario("standalone-side") as h_scope:
            h_handle = h_scope.expect("topic", _PLACEHOLDER_MATCHER)
            h_scope = h_scope.publish("topic", {"status": "ok"})
            h_result = await h_scope.await_all(timeout_ms=20)
    finally:
        await stage.disconnect()
        await standalone_h.disconnect()

    # Both passed independently. Neither leaked into the other.
    assert stage_result.passed
    assert stage_handle.outcome is Outcome.PASS
    assert stage_handle.transport == "only"

    assert h_result.passed
    assert h_handle.outcome is Outcome.PASS
    # Single-Harness handle has no transport.
    assert h_handle.transport is None


# ---------------------------------------------------------------------------
# L2 — sharing a transport between two harnesses produces cross-talk (canary)
# ---------------------------------------------------------------------------


async def test_two_harnesses_should_cross_talk_when_a_transport_instance_is_shared(
    allowlist_yaml_path: Path,
) -> None:
    """L2. Documents a non-supported configuration: two `Harness`
    instances pointed at the same `MockTransport` instance. The
    transport's callback registry is shared, so both harnesses'
    subscribers fire on either's publish — observable cross-talk.

    This is a CANARY test asserting the cross-talk DOES happen,
    pinning the framework's failure mode under the documented misuse
    so a future engineer cannot accidentally rely on this shape
    working.

    The harness-level abstraction does not own the transport's
    callback registry; the transport does. Two Harness wrappers over
    one MockTransport share that registry by construction. The
    framework provides no defence — the contract is "one transport,
    one harness".
    """
    # ONE shared transport, TWO harness wrappers over it. Both
    # harnesses are connected by sharing the underlying transport's
    # _connected state via the wrapper's own bookkeeping.
    shared_transport = MockTransport(
        allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"
    )
    h_a = Harness(shared_transport)
    h_b = Harness(shared_transport)

    # Connect via h_a; h_b shares the underlying transport's connected
    # state but its own `_connected` flag flips False until its own
    # connect() is called. Connect both for symmetry.
    await h_a.connect()
    await h_b.connect()
    try:
        # Subscribe one callback per harness (using harness.subscribe,
        # not scenario.expect — we want to observe the transport-level
        # delivery, not match-based resolution).
        a_received: list[bytes] = []
        b_received: list[bytes] = []
        h_a.subscribe("topic", lambda t, raw: a_received.append(raw))
        h_b.subscribe("topic", lambda t, raw: b_received.append(raw))

        # Publish ONCE via h_a. Because the transport is shared, BOTH
        # harnesses' callbacks fire — that is the cross-talk.
        h_a.publish("topic", b"hello")
    finally:
        # Avoid double-disconnect on the same underlying transport;
        # the harness wrapper's idempotency handles the second call.
        await h_a.disconnect()
        await h_b.disconnect()

    # Both lists got the same message — proof of cross-talk via
    # shared transport. This is the documented misuse failure mode.
    assert len(a_received) == 1
    assert len(b_received) == 1, (
        "expected cross-talk via shared MockTransport instance — both "
        "harnesses' subscribers should fire on either's publish. If b "
        "did not receive, the framework has gained a defence not in "
        "the documented contract; this canary should be reviewed."
    )
    assert a_received[0] == b_received[0] == b"hello"
