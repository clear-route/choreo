# choreo — Choreo test harness library

An async Python test framework for message-driven systems. Write tests that
declare *"when I publish X, I expect Y"* and the harness handles routing,
correlation, timing, and reporting.

The library is transport-agnostic. Plug in a transport — `MockTransport` for
unit tests, `NatsTransport` for end-to-end, your own for LBM / Kafka /
RabbitMQ / anything else — and the same scenario DSL works against all of
them.

- Python 3.11+
- No runtime dependencies; `pytest`, `pytest-asyncio`, and `pyyaml` are test
  extras only.
- Transport client libraries ship as optional extras
  (`pip install 'choreo[nats]'`, `choreo[kafka]`, `choreo[rabbitmq]`, `choreo[redis]`).

## Install

```bash
pip install choreo               # library only
pip install 'choreo[nats]'         # + NATS client for the e2e suite
pip install 'choreo[nats,test]'    # + pytest + pytest-asyncio + pyyaml
```

Pair with the companion reporter plugin for HTML + JSON test output:

```bash
pip install choreo-reporter
```

## Correlation policy

The library ships with three correlation profiles (ADR-0019):

```python
from choreo import Harness, NoCorrelationPolicy, DictFieldPolicy, test_namespace

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

## Examples

Runnable example projects live in the repo's `examples/` directory:

- `examples/01-hello-world/` — minimum useful test.
- `examples/02-request-reply/` — staging a fake upstream with `on().publish()`.
- `examples/03-parallel-isolation/` — opting into a `CorrelationPolicy`.

```bash
pytest examples/01-hello-world/
```

## Documentation

See the project README at
<https://github.com/clear-route/choreo> for architecture, the Scenario DSL,
matchers, transports, and the downstream-consumer fixture pattern.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
