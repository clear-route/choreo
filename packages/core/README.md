# admiral — Admiral test harness library

An async Python test framework for message-driven systems. Write tests that
declare *"when I publish X, I expect Y"* and the harness handles routing,
correlation, timing, and reporting.

The library is transport-agnostic. Plug in a transport — `MockTransport` for
unit tests, `NatsTransport` / `KafkaTransport` / `RabbitTransport` /
`RedisTransport` for end-to-end, or your own — and the same scenario DSL
works against all of them.

- Python 3.11+
- No runtime dependencies; `pytest`, `pytest-asyncio`, and `pyyaml` are test
  extras only.
- Transport client libraries ship as optional extras
  (`pip install 'admiral[nats]'`, `admiral[kafka]`, `admiral[rabbitmq]`, `admiral[redis]`).

## Install

```bash
pip install admiral               # library only
pip install 'admiral[nats]'         # + NATS client for the e2e suite
pip install 'admiral[nats,test]'    # + pytest + pytest-asyncio + pyyaml
```

Pair with the companion reporter plugin for HTML + JSON test output:

```bash
pip install admiral-reporter
```

## Correlation policy

The library ships with three correlation profiles (ADR-0019):

```python
from admiral import Harness, NoCorrelationPolicy, DictFieldPolicy, test_namespace

# Default — transparent passthrough. Payloads are unchanged; every live scope
# on a topic sees every message (broadcast fallback). Safe on dedicated or
# per-run infrastructure; unsafe on a shared broker.
Harness(transport)

# Opt in to per-scope isolation by stamping/reading a dict field.
Harness(transport, correlation=DictFieldPolicy(field="trace_id", prefix="run-abc-"))

# Opt in to the TEST- prefix posture (downstream ingress filters on `TEST-`).
Harness(transport, correlation=test_namespace())
```

Custom policies implement the `CorrelationPolicy` protocol (`new_id`,
`write`, `read`, `routes_by_correlation`) and can stamp into any shape
the consumer's schema requires — a dict field, a transport header, a
tag-value-protocol tag, a protobuf field. See the ADR for the protocol
contract and the trust-boundary rules.

## Multi-transport scenarios

For tests that span two transports — typically a bridge or protocol
translator AUT that consumes on one wire and republishes on another,
or an orchestrator that fans out to many connected devices — use
`Stage`. A `Stage` wraps a named registry of `Harness` instances and
a `CorrelationBridge` so a single scenario can publish on transport
A, register a reactive reply on transport B, and assert on transport
A again, all under one deadline.

```python
from admiral import DictFieldPolicy, Harness, MappedBridge, Stage
from admiral.transports import KafkaTransport, NatsTransport

stage = Stage(
    harnesses={
        "kafka": Harness(KafkaTransport(...), correlation=DictFieldPolicy(field="correlation_id")),
        "nats":  Harness(NatsTransport(...),  correlation=DictFieldPolicy(field="correlation_id")),
    },
    bridge=MappedBridge(forwards={
        "kafka": lambda l: f"kafka-{l}",
        "nats":  lambda l: f"nats-{l}",
    }),
)
```

Single-transport tests should keep using a plain `Harness` — `Stage`
adds correlation translation and per-transport routing concepts that
cost nothing on the multi-transport path but are unnecessary noise
for a single-transport test.

