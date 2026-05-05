"""06 — Multi-transport bridge: vendor adapter into the energy domain.

Some services sit at the seam between two messaging worlds. An energy
platform's field devices speak proprietary wire formats on a vendor's
Kafka feed; the platform's own services speak a canonical shape on its
internal NATS bus. A small adapter consumes the vendor format, normalises
it, and republishes onto the domain bus. Testing that adapter means
asserting on what comes out of one transport in response to what went
into another, in the same scenario, under one deadline.

The same `Stage` machinery covers a related case: the AUT is an
orchestrator that fans out commands to many connected devices (batteries,
wind turbines, EV chargers) and the test needs to assert that *each*
device received the right message. The third test in this file shows
that pattern.

Choreo's `Stage` wraps a named registry of `Harness` instances and a
`CorrelationBridge` that translates a per-scope id across transports so
the two sides can be filtered independently.

Run it:

    pytest examples/06-multi-transport-bridge/
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from choreo import DictFieldPolicy, Harness, MappedBridge, Stage
from choreo.matchers import contains_fields, field_equals
from choreo.transports import MockTransport

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


# ---------------------------------------------------------------------------
# The system under test — vendor Kafka in, canonical NATS out
# ---------------------------------------------------------------------------


PublishFn = Callable[[str, bytes], None]


class MeterAdapter:
    """Normalises a vendor's proprietary meter telemetry into the
    platform's canonical domain shape.

    A real adapter would talk to a real NATS client; here the
    `publish_to_domain` callable is wired to the test's MockTransport
    so the example is self-contained. The class itself has no
    knowledge of Choreo — that's what the consumer's adapter code
    would look like.

    Vendor shape    (Kafka): {"deviceId": "...", "kWh": ..., "tariff": "..."}
    Canonical shape (NATS):  {"meter_id": "...", "energy_kwh": ..., "tariff_band": "..."}
    """

    def __init__(self, publish_to_domain: PublishFn) -> None:
        self._publish = publish_to_domain

    def on_vendor_message(self, raw: bytes) -> None:
        vendor = json.loads(raw)
        canonical = {
            "meter_id": vendor["deviceId"],
            "energy_kwh": vendor["kWh"],
            "tariff_band": vendor["tariff"],
            # Round-trip the bridge correlation id so the test's NATS-side
            # scope filter accepts the message. A real adapter would derive
            # this from whatever correlation header it propagates.
            "correlation_id": vendor["correlation_id"].replace(
                "kafka-", "nats-"
            ),
        }
        self._publish("meters.ingested", json.dumps(canonical).encode("utf-8"))


class RoundTripMeterAdapter(MeterAdapter):
    """Same normalisation as `MeterAdapter`, but also waits for a
    domain-side ack on `meters.acked` and confirms back to the vendor
    on `vendor.confirmed` — the full vendor → domain → vendor loop.

    The reply path is wired to `publish_to_vendor`. The adapter
    subscribes to the domain ack via the harness it was given; for
    correlation it translates `nats-{id}` back into `kafka-{id}`."""

    def __init__(
        self,
        publish_to_domain: PublishFn,
        publish_to_vendor: PublishFn,
    ) -> None:
        super().__init__(publish_to_domain)
        self._publish_vendor = publish_to_vendor

    def on_domain_ack(self, raw: bytes) -> None:
        ack = json.loads(raw)
        confirmation = {
            "deviceId": ack["meter_id"],
            "status": ack["status"],
            "correlation_id": ack["correlation_id"].replace("nats-", "kafka-"),
        }
        self._publish_vendor(
            "vendor.confirmed", json.dumps(confirmation).encode("utf-8")
        )


class DispatchOrchestrator:
    """Receives a market dispatch signal on Kafka and fans out per-device
    setpoint commands on NATS — one message per registered device, on
    the device's own command topic.

    Devices "log on" by calling `register_device()`. In production this
    would be triggered by a registration handshake on the device bus;
    here the test calls it directly so the example stays focused on
    the fan-out behaviour.

    The setpoint distribution is a simple proportional split by
    capacity — what matters for the test is that each device receives
    its own correctly-shaped command, not the dispatch maths.
    """

    def __init__(self, publish_to_devices: PublishFn) -> None:
        self._publish = publish_to_devices
        self._devices: dict[str, float] = {}

    def register_device(self, device_id: str, capacity_mw: float) -> None:
        self._devices[device_id] = capacity_mw

    def on_market_signal(self, raw: bytes) -> None:
        signal = json.loads(raw)
        target_mw = signal["target_mw"]
        total_capacity = sum(self._devices.values())
        nats_cid = signal["correlation_id"].replace("kafka-", "nats-")
        for device_id, capacity in self._devices.items():
            command = {
                "device_id": device_id,
                "setpoint_mw": round(target_mw * (capacity / total_capacity), 2),
                "correlation_id": nats_cid,
            }
            self._publish(
                f"devices.{device_id}.command",
                json.dumps(command).encode("utf-8"),
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_an_external_vendor_reading_should_appear_normalised_on_the_meters_domain_topic() -> None:
    """Fire-and-forget normalisation. The test publishes a vendor-shape
    reading on Kafka; the adapter normalises it and publishes the
    canonical shape on NATS; the scenario expects the domain message
    under one deadline."""

    nats_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )

    # The bridge translates one logical scope id into per-transport
    # wire ids — `kafka-{id}` and `nats-{id}` are the simplest shape.
    bridge = MappedBridge(
        forwards={
            "kafka": lambda logical: f"kafka-{logical}",
            "nats": lambda logical: f"nats-{logical}",
        }
    )

    stage = Stage(
        harnesses={"kafka": kafka_h, "nats": nats_h},
        bridge=bridge,
    )

    await stage.connect()

    # Wire the SUT outside the scenario. The SUT is part of the system,
    # not the test scaffolding — it only knows about a `publish_to_domain`
    # callable. Here that callable is the test's NATS MockTransport.
    sut = MeterAdapter(
        publish_to_domain=nats_h._transport.publish,  # type: ignore[attr-defined]
    )
    kafka_h.subscribe(
        "vendor.reading", lambda topic, raw: sut.on_vendor_message(raw)
    )

    try:
        async with stage.scenario("normalise") as s:
            # Assert the domain side sees the normalised shape.
            domain = s.expect(
                "meters.ingested",
                contains_fields(
                    {
                        "meter_id": "INV-77",
                        "energy_kwh": 12.5,
                        "tariff_band": "PEAK",
                    }
                ),
                on="nats",
            )

            # Stand in for the vendor's device feed.
            s.publish(
                "vendor.reading",
                {"deviceId": "INV-77", "kWh": 12.5, "tariff": "PEAK"},
                on="kafka",
            )

            result = await s.await_all(timeout_ms=200)
    finally:
        await stage.disconnect()

    result.assert_passed()
    assert domain.was_fulfilled()
    # Handles record which transport satisfied them — useful when an
    # assertion needs to know "this resolved on NATS, not Kafka".
    assert domain.transport == "nats"
    # Per-transport view of the result. The Kafka side had no
    # expectations registered, so it's empty.
    assert len(result.by_transport["nats"]) == 1
    assert result.by_transport.get("kafka", ()) == ()


async def test_an_external_reading_should_be_confirmed_after_a_domain_round_trip() -> None:
    """The adapter calls into the domain on NATS and waits for an ack
    before confirming back to the vendor on Kafka. The test stands in
    for the domain ingest service via `s.on(...).publish(...)` — the
    reactive reply chain registers the ack on NATS and the entire
    vendor → domain → vendor loop happens under one scenario deadline."""

    nats_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )

    bridge = MappedBridge(
        forwards={
            "kafka": lambda logical: f"kafka-{logical}",
            "nats": lambda logical: f"nats-{logical}",
        }
    )
    stage = Stage(
        harnesses={"kafka": kafka_h, "nats": nats_h},
        bridge=bridge,
    )

    await stage.connect()

    sut = RoundTripMeterAdapter(
        publish_to_domain=nats_h._transport.publish,  # type: ignore[attr-defined]
        publish_to_vendor=kafka_h._transport.publish,  # type: ignore[attr-defined]
    )
    kafka_h.subscribe(
        "vendor.reading", lambda topic, raw: sut.on_vendor_message(raw)
    )
    nats_h.subscribe(
        "meters.acked", lambda topic, raw: sut.on_domain_ack(raw)
    )

    try:
        async with stage.scenario("round-trip") as s:
            # Stand in for the domain ingest service: when the adapter
            # publishes `meters.ingested` on NATS, reply with
            # `meters.acked` on the same transport. The build callback
            # receives the decoded trigger payload.
            s.on("meters.ingested", on="nats").publish(
                "meters.acked",
                on="nats",
                build=lambda ingested: {
                    "meter_id": ingested["meter_id"],
                    "status": "ACCEPTED",
                },
            )

            # Assert the vendor side sees the confirmation reply, with
            # the original `deviceId` (camelCase) preserved.
            confirmed = s.expect(
                "vendor.confirmed",
                field_equals("status", "ACCEPTED"),
                on="kafka",
            )

            # Kick off the round-trip.
            s.publish(
                "vendor.reading",
                {"deviceId": "INV-42", "kWh": 0.7, "tariff": "OFFPEAK"},
                on="kafka",
            )

            result = await s.await_all(timeout_ms=500)
    finally:
        await stage.disconnect()

    result.assert_passed()
    assert confirmed.was_fulfilled()
    assert confirmed.message["deviceId"] == "INV-42"

    # The reply chain has its own lifecycle report — useful when the
    # round-trip stalls and you need to know whether the domain stand-in
    # ever fired.
    reply_report = result.replies[0]
    assert reply_report.state.name == "FIRED"
    assert reply_report.match_count == 1
    assert reply_report.trigger_transport == "nats"
    assert reply_report.response_transport == "nats"


async def test_a_dispatch_signal_should_be_fanned_out_to_each_registered_device() -> None:
    """Multiple devices "log on" to the orchestrator — a battery pair
    and a wind turbine. When a market dispatch signal arrives on
    Kafka, the orchestrator must send each device its own setpoint
    command on its own NATS topic, sized proportionally to capacity.

    The test stands in for every connected device by registering one
    expectation per device topic; all must resolve under one scenario
    deadline. This is the canonical "did the AUT talk to each
    connected service correctly?" assertion."""

    nats_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        MockTransport(allowlist_path=ALLOWLIST, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )

    bridge = MappedBridge(
        forwards={
            "kafka": lambda logical: f"kafka-{logical}",
            "nats": lambda logical: f"nats-{logical}",
        }
    )
    stage = Stage(
        harnesses={"kafka": kafka_h, "nats": nats_h},
        bridge=bridge,
    )

    await stage.connect()

    sut = DispatchOrchestrator(
        publish_to_devices=nats_h._transport.publish,  # type: ignore[attr-defined]
    )
    # Three devices "log on" before the dispatch signal arrives.
    # In production each would send a registration message; here the
    # test wires the registry directly so the assertion stays on the
    # fan-out, not the handshake.
    sut.register_device("battery-1", capacity_mw=5)
    sut.register_device("battery-2", capacity_mw=3)
    sut.register_device("wind-1", capacity_mw=2)

    kafka_h.subscribe(
        "market.dispatch", lambda topic, raw: sut.on_market_signal(raw)
    )

    try:
        async with stage.scenario("fanout") as s:
            # One expectation per connected device — the test "is" each
            # device, watching its own command topic. All three handles
            # must resolve PASS under the scenario deadline for the
            # fan-out to count as correct.
            handles = {
                device_id: s.expect(
                    f"devices.{device_id}.command",
                    field_equals("device_id", device_id),
                    on="nats",
                )
                for device_id in ("battery-1", "battery-2", "wind-1")
            }

            # Market wants 10 MW dispatched. The orchestrator splits
            # proportionally to capacity: 5 MW + 3 MW + 2 MW.
            s.publish(
                "market.dispatch", {"target_mw": 10}, on="kafka"
            )

            result = await s.await_all(timeout_ms=500)
    finally:
        await stage.disconnect()

    result.assert_passed()
    # Each device received the right share of the dispatch target.
    assert handles["battery-1"].message["setpoint_mw"] == 5.0
    assert handles["battery-2"].message["setpoint_mw"] == 3.0
    assert handles["wind-1"].message["setpoint_mw"] == 2.0
    # Per-transport view: three commands on NATS, nothing back on Kafka.
    assert len(result.by_transport["nats"]) == 3
    assert result.by_transport.get("kafka", ()) == ()
