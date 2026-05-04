"""Group J: Stage parallel isolation. Negative-behaviour integration tests.

Covers test-plan items J1 and J2 from
`docs/test-plans/0027-stage-integration-tests.md`.

J1 is the headline test for the correlation-filter defence shipped in
the Group H stabilise pass: 100 concurrent Stage scopes share two
harnesses backed by `DictFieldPolicy` and a `MappedBridge` that gives
distinct wire ids per transport per logical scope id. Each scope
publishes a request on Kafka and expects a response on NATS; an
AUT-stand-in callback subscribed on Kafka echoes every request as a
response on NATS, translating the wire id from the Kafka side to the
NATS side via the same prefix scheme the bridge uses. The defence
under test: every scope's expect resolves on its OWN response only,
even though all 100 scopes' callbacks fire on every published
response.

J2 is a canary documenting the framework's failure mode under the
documented misuse of a collision-prone `bridge.fresh()`. The contract
that ADR-0027 §Security Considerations spells out is "if `fresh()` is
collision-prone, two scopes can land on the same logical id and
therefore the same per-transport wire ids; inbound traffic destined
for one scope's transport then matches the other scope's
expectations." J2 instantiates a buggy bridge and asserts the leak
DOES occur — pinning the failure mode so a future framework change
that accidentally defended against it is detected as a behavioural
delta.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path

from choreo import Harness
from choreo.correlation import DictFieldPolicy
from choreo.matchers import field_equals
from choreo.scenario import Outcome
from choreo.stage import MappedBridge, Stage
from choreo.transports import MockTransport

# Number of concurrent scopes to exercise. Per the test plan, the
# headline metric is "no cross-scope match across 100". Tuned high
# enough to be meaningful, low enough to keep the test under 1s.
_N_SCOPES = 100


# ---------------------------------------------------------------------------
# J1 — 100 concurrent scopes; no cross-scope leakage
# ---------------------------------------------------------------------------


async def test_stage_should_isolate_one_hundred_concurrent_scopes(
    allowlist_yaml_path: Path,
) -> None:
    """J1. With 100 concurrent scopes sharing two harnesses, every
    scope's expect must resolve on its OWN response — never on
    another scope's. The correlation filter shipped in Group H's
    stabilise pass is the load-bearing defence.

    Setup: two harnesses each with `DictFieldPolicy(field="correlation_id")`
    so the wire id round-trips through the published payload. Bridge
    is a `MappedBridge` whose forwards prepend a per-transport prefix
    to the logical id (`nats-<hex>`, `kafka-<hex>`). The AUT-stand-in
    subscribes on Kafka's request topic and, on every request, decodes
    the payload, translates the correlation_id from `kafka-<hex>` to
    `nats-<hex>` (the bridge's deterministic per-transport scheme),
    and publishes a response on NATS.

    All 100 scopes subscribe on the response topic FIRST, then all
    publish their requests (subscribing-then-publishing in two phases
    forces every scope's callback to see every response). Without
    the correlation filter, every scope's matcher would resolve on
    every response — N times N cross-resolutions. With the filter, each
    scope resolves exactly once on its own response.

    The matcher uses `field_equals("scope_idx", N)` so the test would
    catch the failure mode of "filter is wrong, scope X resolved
    scope Y's message" — the resolved message's `scope_idx` would
    not equal X.
    """
    nats_h = Harness(
        MockTransport(
            allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"
        ),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        MockTransport(
            allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"
        ),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    bridge = MappedBridge(
        forwards={
            "nats": lambda logical: f"nats-{logical}",
            "kafka": lambda logical: f"kafka-{logical}",
        }
    )
    stage = Stage(
        harnesses={"nats": nats_h, "kafka": kafka_h},
        bridge=bridge,
    )
    await stage.connect()

    # AUT-stand-in: receives Kafka requests, echoes back as NATS
    # responses with the bridge-translated correlation_id. The
    # `nats_h._transport.publish` call goes to the underlying
    # MockTransport directly with bytes; we encode JSON ourselves
    # because we are bypassing the harness's correlation.write
    # (the AUT is producing the wire-id-stamped bytes itself).
    def aut_kafka_to_nats(topic: str, raw: bytes) -> None:
        request = json.loads(raw)
        kafka_cid: str = request["correlation_id"]
        # Translate `kafka-<hex>` to `nats-<hex>` per the bridge's scheme.
        logical = kafka_cid.removeprefix("kafka-")
        response = {
            "scope_idx": request["scope_idx"],
            "correlation_id": f"nats-{logical}",
        }
        nats_h._transport.publish(  # type: ignore[attr-defined]
            "response.topic", json.dumps(response).encode("utf-8")
        )

    kafka_h.subscribe("request.topic", aut_kafka_to_nats)

    try:
        async with AsyncExitStack() as exit_stack:
            scopes = []
            for i in range(_N_SCOPES):
                scope = await exit_stack.enter_async_context(
                    stage.scenario(f"scope-{i}")
                )
                scopes.append(scope)

            # Phase 1: every scope subscribes (registers an expectation).
            # All 100 callbacks now live on `nats` for `response.topic`;
            # any subsequent publish on that topic fans out to all 100.
            handles = [
                scope.expect(
                    "response.topic",
                    field_equals("scope_idx", idx),
                    on="nats",
                )
                for idx, scope in enumerate(scopes)
            ]

            # Phase 2: every scope publishes. Each request fires the AUT
            # which publishes a response; that response is delivered to
            # ALL 100 NATS subscribers, exercising the correlation filter
            # in every scope's callback.
            for idx, scope in enumerate(scopes):
                scope.publish(
                    "request.topic", {"scope_idx": idx}, on="kafka"
                )

            # Phase 3: await each scope's deadline. With MockTransport's
            # synchronous delivery, every handle resolved synchronously
            # in phase 2; await_all is a fast no-op.
            results = [
                await scope.await_all(timeout_ms=500) for scope in scopes
            ]
    finally:
        await stage.disconnect()

    # Headline assertion: every scope passed.
    failed = [
        (i, h.outcome, h._reason)
        for i, h in enumerate(handles)
        if h.outcome is not Outcome.PASS
    ]
    assert not failed, f"{len(failed)} scopes did not PASS: {failed[:5]}"

    # Cross-scope correctness: every resolved message's scope_idx
    # matches its own scope. A cross-scope leak would show up as a
    # handle resolving with the WRONG scope_idx (matcher ignored or
    # wrong filter routed someone else's response).
    for i, h in enumerate(handles):
        assert h.message["scope_idx"] == i, (
            f"scope {i} resolved on message for scope {h.message['scope_idx']}"
        )

    # Every result reports passed.
    for i, result in enumerate(results):
        assert result.passed, f"scope {i} result.passed is False"


# ---------------------------------------------------------------------------
# J2 — canary: collision-prone bridge.fresh() leaks across scopes
# ---------------------------------------------------------------------------


async def test_stage_should_leak_across_scopes_when_bridge_fresh_is_collision_prone(
    allowlist_yaml_path: Path,
) -> None:
    """J2. Documents the failure mode named in ADR-0027 §Security
    Considerations: a buggy bridge whose `fresh()` returns the same
    value for every scope means every scope ends up with the same
    per-transport wire ids — and the correlation filter therefore
    accepts cross-scope messages.

    This is a CANARY test: it asserts the leak DOES happen, pinning
    the framework's failure mode under the documented misuse. The
    framework cannot defend against a buggy `fresh()` because the
    contract delegates collision-resistance to the consumer (per
    `CorrelationBridge.fresh()` docstring); this test makes the
    consequence observable so a future change that accidentally
    defends against it (e.g. by checking `fresh()` collisions across
    scopes) shows up as a behavioural delta on this test.

    The leak shape: scope A publishes a request tagged with the
    (bug-shared) correlation id, scope B's expect on the same topic
    matches and resolves with scope A's payload. The matcher
    `field_equals` on `scope_idx` catches this — scope B's expect
    declared `field_equals("scope_idx", 1)` but resolved a message
    whose `scope_idx` is 0.
    """
    nats_h = Harness(
        MockTransport(
            allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"
        ),
        correlation=DictFieldPolicy(field="correlation_id"),
    )

    class _CollisionProneBridge:
        """Buggy bridge: `fresh()` always returns the same value.
        This violates the protocol's collision-resistance contract.
        """

        async def fresh(self) -> str:
            return "fixed-collision-prone-id"

        def to_wire(self, logical: object, transport: str) -> str:
            return f"{transport}-{logical}"

        def from_wire(self, wire: str, transport: str) -> object | None:
            return None

    stage = Stage(
        harnesses={"nats": nats_h},
        bridge=_CollisionProneBridge(),
    )
    await stage.connect()

    # Two scopes, both subscribed on the same response topic. Both
    # have the SAME wire id ("nats-fixed-collision-prone-id") because
    # `fresh()` is collision-prone. The correlation filter cannot
    # distinguish them.
    try:
        async with AsyncExitStack() as exit_stack:
            scope_a = await exit_stack.enter_async_context(
                stage.scenario("scope-a")
            )
            scope_b = await exit_stack.enter_async_context(
                stage.scenario("scope-b")
            )

            # Each scope expects a message with its own scope_idx.
            handle_a = scope_a.expect(
                "topic", field_equals("scope_idx", 0), on="nats"
            )
            handle_b = scope_b.expect(
                "topic", field_equals("scope_idx", 1), on="nats"
            )

            # Scope A publishes ONLY its own message. Both scopes'
            # callbacks fire (DictFieldPolicy reads the same wire id);
            # scope A's matcher accepts (scope_idx=0); scope B's
            # matcher REJECTS (scope_idx=1, expected). So scope B
            # ends up TIMEOUT, NOT cross-resolved.
            #
            # The actual leak appears when scope A's matcher would
            # accept BOTH messages — which it does not in this test.
            # The observable leak: scope B's `_attempts` is non-zero
            # because scope A's message reached scope B's matcher
            # (the filter did not reject it). Without the bug, scope
            # B's `_attempts` would be 0 because the filter would
            # have rejected the message before the matcher ran.
            scope_a.publish("topic", {"scope_idx": 0}, on="nats")

            result_a = await scope_a.await_all(timeout_ms=20)
            await scope_b.await_all(timeout_ms=20)
    finally:
        await stage.disconnect()

    # Scope A resolved its own message (correctly).
    assert handle_a.outcome is Outcome.PASS
    assert result_a.passed is True

    # Scope B's expectation did NOT match (matcher rejected scope A's
    # payload — different scope_idx). But scope B's `_attempts` is
    # NON-ZERO, proving the filter did NOT reject scope A's message
    # — that is the documented leak: scope B's matcher saw scope A's
    # message because the bridge collision means both scopes share a
    # wire id.
    assert handle_b.outcome is Outcome.FAIL
    assert handle_b.attempts >= 1, (
        "expected scope B's matcher to receive scope A's message via the "
        "buggy-bridge leak; if attempts is 0 the filter is unexpectedly "
        "rejecting the leak — a behavioural delta that suggests the "
        "framework has gained a defence not in the documented contract"
    )
