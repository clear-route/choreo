# Choreo examples

Self-contained, runnable examples for getting started. Each directory is a
complete pytest project — `cd` into it and run `pytest`.

| Example | What it shows |
|---|---|
| [01-hello-world](01-hello-world/) | The minimum useful test: publish a dict, expect a shape, assert passed. |
| [02-request-reply](02-request-reply/) | The `on(trigger).publish(reply)` primitive — staging a fake upstream service inside a test. |
| [03-parallel-isolation](03-parallel-isolation/) | Opting into per-scope routing with a `CorrelationPolicy` so parallel scenarios don't cross-match. |
| [04-transport-auth](04-transport-auth/) | Wire a typed `auth=` descriptor into a transport — credential lifecycle, redaction, and the Mock parity guarantee. |
| [05-auth-resolver](05-auth-resolver/) | Fetch credentials at `connect()` time via sync/async resolvers — env vars, Vault, Secrets Manager. |

## Prerequisites

```bash
pip install choreo
pip install pytest pytest-asyncio
```

Or, from a clone of this repo:

```bash
pip install -e 'packages/core[test]'
```

## How the examples are structured

Each example directory has:

- `allowlist.yaml` — the tiny allowlist the MockTransport validates against.
- `test_*.py` — the scenario code, written the way you'd write real tests.
- `README.md` — what the example demonstrates, and any knobs worth knowing.

The examples use `MockTransport` so they run anywhere without brokers or
containers. Swap `MockTransport` for `NatsTransport` / `KafkaTransport` /
`RabbitTransport` / `RedisTransport` to talk to a real broker — the scenario
code is identical.
