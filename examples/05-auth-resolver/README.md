# 05 — Auth resolvers (env vars, Vault, Secrets Manager)

Shows the resolver pattern for fetching credentials at `connect()` time
rather than at transport construction.  The resolver is a callable (sync or
async) that returns a typed auth descriptor.  The library invokes it inside
`connect()`, consumes the result for logon, and clears it — the secret
exists in memory only for the duration of the handshake.

## Run it

```bash
# The example reads from environment variables (with safe defaults).
NATS_TOKEN=my-dev-token pytest examples/05-auth-resolver/

# Or just run with defaults:
pytest examples/05-auth-resolver/
```

## What's going on

Instead of hardcoding credentials in source:

```python
auth=NatsAuth.token("hardcoded")          # literal — secret at construction
```

You pass a callable that returns the descriptor:

```python
auth=lambda: NatsAuth.token(os.environ["NATS_TOKEN"])   # sync resolver
auth=fetch_from_vault                                    # async resolver
```

The resolver is called exactly once per `connect()`.  If it raises, the
error is wrapped in a `TransportError` that exposes only the exception
class name — never the args, never the cause chain.

## Three resolver shapes

| Shape | When secret enters memory | Example |
|-------|---------------------------|---------|
| `lambda: NatsAuth.token(...)` | Inside `connect()` | Env vars, `.env` files |
| `def resolver(): ...` | Inside `connect()` | Sync Vault / KMS client |
| `async def resolver(): ...` | Inside `connect()` | Async Vault / Secrets Manager SDK |

## What to try

- Set `NATS_TOKEN` to a value and run the test.  The resolver reads it
  at connect time, not at import time.
- Unset `NATS_TOKEN` (or set it to empty).  The resolver raises, and
  the test shows the `TransportError` — notice the error message says
  "auth resolver failed" without leaking the `KeyError` args.

## Full auth guide

See [docs/guides/authentication.md](../../docs/guides/authentication.md)
for Vault and AWS Secrets Manager recipes.
