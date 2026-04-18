# 04 — Transport authentication

Shows how to wire authentication into a transport using the typed `auth=`
parameter.  The example uses `MockTransport` so it runs anywhere without
a broker — swap for `NatsTransport` with `NatsAuth.user_password(...)` to
talk to a real authenticated NATS server.

## Run it

```bash
pytest examples/04-transport-auth/
```

## What's going on

Every real transport accepts an `auth=` parameter.  You pass a typed
descriptor — `NatsAuth.token(...)`, `NatsAuth.user_password(...)`, etc. —
and the library handles credential lifecycle and redaction.

`MockTransport` accepts the same `auth=` parameter for parity.  It
validates the descriptor shape (a wrong variant raises immediately), clears
it after `connect()`, and emits a warning.  This means you can develop
your fixture with auth wired in and swap to a real transport later without
changing the auth plumbing.

## What to try

- Remove the `auth=` line.  The test still passes — `auth=None` is the
  default and preserves the pre-auth behaviour.
- Pass a wrong-variant descriptor (e.g. pass a bare string instead of a
  `NatsAuth` descriptor).  Mock raises `TransportError` the same way a
  real transport would.
- Inspect the descriptor's `repr()` — it prints `_NatsToken(<redacted>)`,
  never the actual secret.

## Full auth guide

See [docs/guides/authentication.md](../../docs/guides/authentication.md)
for every auth mode, TLS variants, and resolver recipes.
