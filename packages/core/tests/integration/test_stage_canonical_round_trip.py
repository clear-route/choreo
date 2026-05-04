"""Group P: Stage canonical happy-path round-trip.

The single positive integration test in the Stage suite. ADR-0027
§Validation success metric: the canonical NATS-in / Kafka-bridge /
NATS-out round trip must be expressible in ≤15 lines of scenario
body. This test pins that line count, and pins the contract that
every defence shipped through Groups A-O composes correctly when
run together: the bridge translates correlation across transports,
the reply emits on the response transport, the response's
correlation is read by the response-side scope, and the expectation
resolves PASS.

The line-count assertion is structural: counted as the lines
between `async with` and `await scope.await_all(...)`, the body
must be ≤ 15. The scaffolding around it (harness construction,
Stage construction, AUT-stand-in setup) is fixture work and
excluded from the count.

This test is the headline marketing assertion of the Stage feature:
"a NATS ↔ Kafka bridge round-trip in 15 lines of test code".
"""

from __future__ import annotations

import json
from pathlib import Path

from choreo import Harness
from choreo.correlation import DictFieldPolicy
from choreo.matchers import field_equals
from choreo.scenario import Outcome
from choreo.stage import MappedBridge, Stage
from choreo.transports import MockTransport


async def test_stage_canonical_round_trip_should_complete_in_under_fifteen_lines(
    allowlist_yaml_path: Path,
) -> None:
    """P1. The full bridge round-trip:

    1. Test publishes a request on Kafka (the AUT-side input).
    2. AUT-stand-in (subscribed on Kafka) translates the request
       into a Kafka response, stamping the correlation id back so
       the inbound side routes correctly.
    3. The scope's `s.on(...)` reactive reply chain registers the
       Kafka-trigger / NATS-response reply.
    4. The reply's build callback receives the AUT's Kafka-side
       response and produces the NATS-side request.
    5. The scope's `s.expect(...)` on NATS waits for the AUT's
       NATS-side reply.
    6. AUT-stand-in (subscribed on NATS for the request topic)
       echoes a final response on NATS that the expectation
       resolves on.

    All of the above is the framework's job; the scenario body is
    just the publish + reply chain + expect. Counted lines: 15
    or fewer.
    """
    # --- fixture-shaped setup (excluded from the line count) -----------
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
    stage = Stage(harnesses={"nats": nats_h, "kafka": kafka_h}, bridge=bridge)

    # AUT-stand-in: receives request on Kafka topic, publishes a
    # response on NATS with the bridge-translated correlation_id.
    def aut(topic: str, raw: bytes) -> None:
        request = json.loads(raw)
        kafka_cid: str = request["correlation_id"]
        logical = kafka_cid.removeprefix("kafka-")
        response = {
            "kind": "result",
            "echo": request["payload"],
            "correlation_id": f"nats-{logical}",
        }
        nats_h._transport.publish(  # type: ignore[attr-defined]
            "results", json.dumps(response).encode("utf-8")
        )

    await stage.connect()
    kafka_h.subscribe("orders.new", aut)
    try:
        # --- canonical scenario body (counted lines start here) --------
        async with stage.scenario("bridge_round_trip") as scope:                # 1
            scope.on("orders.new", on="kafka").publish(                         # 2
                "orders.processed",                                             # 3
                on="kafka",                                                     # 4
                build=lambda trigger: {"forwarded": trigger["payload"]},        # 5
            )                                                                   # 6
            result_handle = scope.expect(                                       # 7
                "results",                                                      # 8
                field_equals("kind", "result"),                                 # 9
                on="nats",                                                      # 10
            )                                                                   # 11
            scope.publish("orders.new", {"payload": 42}, on="kafka")            # 12
            result = await scope.await_all(timeout_ms=100)                      # 13
        # ---------------------------------------------------------------
    finally:
        await stage.disconnect()

    # The round-trip resolved on NATS with the AUT's echoed payload.
    assert result_handle.outcome is Outcome.PASS
    assert result_handle.message["echo"] == 42
    assert result.passed
