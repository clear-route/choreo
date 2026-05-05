# 06 — Multi-transport bridge: vendor adapter into the energy domain

Some services sit at the seam between two messaging worlds. An energy
platform's field devices — smart meters, inverters, EV chargers — speak
whatever proprietary wire format their vendor ships, often on a
vendor-managed Kafka feed. The platform's own services speak a canonical
event shape on its internal NATS bus. A small adapter sits between the
two, consuming the vendor's wire format on one transport, normalising
it, and republishing onto the domain bus.

Testing that adapter means asserting on what comes out of one transport
in response to what went into another — in the same scenario, under one
deadline.

That's `Stage`. A `Stage` wraps a named registry of `Harness` instances
(one per transport) and a `CorrelationBridge` that translates a per-scope
id across transports so the two sides can be filtered independently.
Single-transport tests should keep using a plain `Harness` — `Stage`
adds concepts you don't need until the test boundary genuinely spans
two wires.

## Run it

```bash
pytest examples/06-multi-transport-bridge/
```

## What's going on

The first two tests use `MeterAdapter` — a small inline class that
takes vendor telemetry off Kafka and republishes it onto the NATS
domain bus in the platform's canonical shape. The third test uses
`DispatchOrchestrator` — an AUT that fans out per-device commands
to a fleet of connected devices. In real life both would be your
services, with their own NATS clients; here the example uses the
test's MockTransport so it runs anywhere without a broker.

Three tests.

**Normalisation, fire-and-forget** —
`test_an_external_vendor_reading_should_appear_normalised_on_the_meters_domain_topic`.
The classic adapter case. The test publishes a vendor-shaped reading on
Kafka. The adapter consumes it, normalises to the canonical vocabulary,
and publishes on NATS. The scenario expects the normalised reading on
NATS and asserts the per-transport view of the result.

**Round-trip, with a domain ack** —
`test_an_external_reading_should_be_confirmed_after_a_domain_round_trip`.
The adapter calls into the domain (NATS) and waits for an ack before
confirming back to the vendor (Kafka). The test stands in for the
domain ingest service using
`s.on("meters.ingested", on="nats").publish("meters.acked", on="nats", build=...)` —
the reactive reply chain registers on one transport and replies on the
same transport. This is the full power of `Stage`: a single deadline
covers the entire vendor → domain → vendor loop.

**Fan-out, multiple connected devices** —
`test_a_dispatch_signal_should_be_fanned_out_to_each_registered_device`.
Several devices "log on" to the orchestrator (a battery pair and a
wind turbine). When a market dispatch signal arrives on Kafka, the
orchestrator must send each device its own setpoint command on its
own NATS topic, sized proportionally to capacity. The test stands in
for every connected device by registering one expectation per topic —
all must resolve under one scenario deadline. This is the canonical
"did the AUT talk to each connected service correctly?" assertion.

```
                                        ┌──▶ devices.battery-1.command
                                        │
   market.dispatch  ──Kafka──▶  Orchestrator ──NATS──▶ devices.battery-2.command
   (the test)                           │
                                        └──▶ devices.wind-1.command
                                                      ▲
                                       (the test stands in for each device)
```

## How `Stage` works in this example

```
   vendor feed   ──Kafka──▶  MeterAdapter  ──NATS──▶  domain ingest
   (the test)                    (SUT)                (test stand-in)
                                    │
                                    ▼
                              Kafka reply  ◀─── (test asserts here)
```

A few rules worth knowing:

- **`on=` is required on every DSL call.** `expect`, `publish`, and `on`
  all take a transport name. Omitting it raises `MissingTransportError`,
  not the generic Python `TypeError`, so you can catch it specifically.
- **`MappedBridge` translates the per-scope id per-transport.**
  `forwards={"kafka": lambda l: f"kafka-{l}", "nats": lambda l: f"nats-{l}"}`
  is enough for most tests — every transport gets a deterministic prefix.
- **Per-harness `DictFieldPolicy(field="correlation_id")`** stamps that
  wire id onto outbound messages and uses it to filter inbound ones.
  The adapter SUT is responsible for *honouring* the correlation field
  when it republishes — that's the contract a real adapter would uphold
  via whatever correlation header it propagates.
- **`result.by_transport`** groups handles by which transport
  satisfied them, useful when you want to assert "domain saw exactly
  one reading and the vendor saw exactly one confirmation".

## The shape transformation

Vendor Kafka shape (what the device feed produces — proprietary,
camelCase, eccentric casing on `kWh`):

```json
{"deviceId": "INV-77", "kWh": 12.5, "tariff": "PEAK"}
```

Canonical NATS shape (what the rest of the platform speaks —
snake_case, expanded names, normalised tariff vocabulary):

```json
{"meter_id": "INV-77", "energy_kwh": 12.5, "tariff_band": "PEAK"}
```

The `MeterAdapter` does the renaming and re-stamps the correlation
id from `kafka-{id}` to `nats-{id}` so the NATS-side scope filter
routes the message into the right scenario. In production code your
adapter would derive the correlation translation from a header, a
trace id, or whatever your platform standardises on — the principle
is the same.

## When *not* to use `Stage`

If only one transport is involved, use a plain `Harness`. `Stage`'s
correlation translation, per-transport routing, and `on=` requirements
all cost nothing on the multi-transport path but add concepts you'd
have to learn for no payoff on a single-transport test.

See [docs/guides/stage.md](../../docs/guides/stage.md) for the full
guide and [ADR-0027](../../docs/adr/0027-stage-multi-transport-coordinator.md)
for the design rationale.
