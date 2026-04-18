# Transport authentication

Every real transport (`NatsTransport`, `KafkaTransport`, `RabbitTransport`,
`RedisTransport`) accepts an optional `auth=` parameter.  Pass a typed
descriptor for the auth mode your broker requires, and the library handles
credential lifecycle, redaction, and cleanup.  When `auth` is omitted the
transport connects without authentication — the same behaviour as before.

This guide is task-oriented: pick your transport + auth mode, copy the
pattern.  For the rationale behind the design, see
[ADR-0020](../adr/0020-transport-auth.md).

---

## The 30-second version

```python
from choreo.transports import NatsTransport, NatsAuth

# Literal — credentials in source (fine for local dev / CI).
transport = NatsTransport(
    servers=["nats://localhost:4222"],
    auth=NatsAuth.user_password("admin", "s3cret"),
)

# Resolver — credentials fetched at connect() time (stronger lifetime).
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=lambda: NatsAuth.token(os.environ["NATS_TOKEN"]),
)

# Async resolver — for async-native secret stores.
async def fetch_from_vault():
    secret = await vault_client.read("secret/nats")
    return NatsAuth.user_password(secret["username"], secret["password"])

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=fetch_from_vault,
)
```

`MockTransport` accepts `auth=` too — it validates the descriptor shape,
logs a warning, and discards it.  This means you can develop against Mock
and swap for a real transport later without changing your fixture's auth
wiring.

---

## Two forms: literal and resolver

The `auth=` parameter accepts three shapes:

| Shape | When the secret enters memory | Lifetime |
|-------|-------------------------------|----------|
| Literal descriptor (`NatsAuth.token("...")`) | At construction | Cleared after `connect()` returns |
| Sync callable (`lambda: NatsAuth.token(...)`) | Inside `connect()` | Cleared after `connect()` returns |
| Async callable (`async def: ...`) | Inside `connect()` | Cleared after `connect()` returns |

The resolver forms are stronger: the secret exists in memory only for the
duration of the `connect()` call.  Use them for anything beyond a local dev
broker.

---

## NATS

### User/password

```python
from choreo.transports import NatsTransport, NatsAuth

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.user_password("admin", "s3cret"),
)
```

### Token

```python
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.token("my-auth-token"),
)
```

### NKey seed

```python
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.nkey("SUAM..."),  # accepts str, bytes, or bytearray
)
```

### Credentials file (.creds)

```python
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.credentials_file("/path/to/user.creds"),
)
```

### TLS (unauthenticated tunnel)

```python
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.tls(
        ca="/path/to/ca.pem",
        cert="/path/to/client.pem",  # optional
        key="/path/to/client-key.pem",  # optional
        hostname="broker.example.com",  # optional SNI override
    ),
)
```

For pre-built SSL contexts (HSM-backed certs, custom trust stores):

```python
import ssl

ctx = ssl.create_default_context()
ctx.load_cert_chain("client.pem", "client-key.pem")

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.tls(ca=ctx),
)
```

### Auth + TLS combined

Each auth mode has a `*_with_tls` variant that pairs credentials with a
TLS tunnel.  The legal pairings are enumerated — illegal combinations
(e.g. NKey + user/password) are construction-time errors.

```python
tls = NatsAuth.tls(ca="/path/to/ca.pem")

# User/password over TLS
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.user_password_with_tls("admin", "s3cret", tls),
)

# Token over TLS
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.token_with_tls("my-token", tls),
)

# NKey over TLS
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.nkey_with_tls("SUAM...", tls),
)

# Credentials file over TLS
transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=NatsAuth.credentials_file_with_tls("/path/to/user.creds", tls),
)
```

---

## Resolver recipes

### Environment variables

```python
import os
from choreo.transports import NatsTransport, NatsAuth

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=lambda: NatsAuth.user_password(
        os.environ["NATS_USER"],
        os.environ["NATS_PASSWORD"],
    ),
)
```

### HashiCorp Vault (async)

```python
import hvac
from choreo.transports import NatsTransport, NatsAuth

async def vault_resolver():
    # hvac is sync; run in executor if latency matters.
    client = hvac.Client(url="https://vault.internal:8200")
    secret = client.secrets.kv.v2.read_secret_version(path="nats/prod")
    data = secret["data"]["data"]
    return NatsAuth.user_password(data["username"], data["password"])

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=vault_resolver,
)
```

### AWS Secrets Manager (async)

```python
import json
import aioboto3
from choreo.transports import NatsTransport, NatsAuth

async def aws_resolver():
    session = aioboto3.Session()
    async with session.client("secretsmanager") as client:
        resp = await client.get_secret_value(SecretId="prod/nats")
        data = json.loads(resp["SecretString"])
        return NatsAuth.token(data["token"])

transport = NatsTransport(
    servers=["nats://broker:4222"],
    auth=aws_resolver,
)
```

---

## Consumer fixture pattern

Wrap transport construction in a session-scoped fixture.  The resolver
form ensures credentials are fetched once per session, at connect time.

```python
# consumer-repo/conftest.py
import os
from pathlib import Path

import pytest_asyncio

from choreo import Harness
from choreo.transports import NatsTransport, NatsAuth


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness():
    transport = NatsTransport(
        servers=[os.environ["NATS_URL"]],
        allowlist_path=Path(os.environ.get("ALLOWLIST", "config/allowlist.yaml")),
        auth=lambda: NatsAuth.user_password(
            os.environ["NATS_USER"],
            os.environ["NATS_PASSWORD"],
        ),
    )
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()
```

---

## MockTransport and auth

`MockTransport` accepts `auth=` for parity.  It validates the descriptor
shape (wrong variant = loud error), clears it, and emits a
`mock_transport_ignored_auth` WARNING.  This means:

- A descriptor typo fails against Mock, not only against the real broker.
- A consumed (reused) descriptor fails against Mock, not silently.
- Your test fixture can wire auth once and swap Mock for a real transport
  by changing one line.

```python
from choreo.transports import MockTransport, NatsAuth

# Works — shape-validated and discarded.
transport = MockTransport(auth=NatsAuth.token("ignored"))

# Fails loudly — wrong variant type.
from choreo.transports.nats_auth import _NatsToken
transport = MockTransport(auth="not-a-descriptor")  # TransportError
```

---

## Security properties

The auth system provides these guarantees by construction:

| Property | How |
|----------|-----|
| No credential in `repr()` | Every descriptor prints `ClassName(<redacted>)` only |
| No credential in `pickle.dumps()` | Descriptors and transports raise `TypeError` |
| No credential in `copy.deepcopy()` | Descriptors raise `TypeError` |
| No credential in pytest assertion diffs | `eq=False` on every descriptor — identity comparison only |
| No credential in error messages | Resolver failures expose exception class name only |
| Bounded lifetime | Credentials cleared in `finally` after `connect()`, success or failure |
| No reuse | A consumed descriptor is refused by a second `connect()` |
| No subclassing | `__init_subclass__` + exact-type allowlist block descriptor spoofing |

---

## Limitations

- **Python cannot zero `str` or `bytes` in memory.** `_clear_auth_fields`
  drops the reference; GC frees the storage eventually.  `bytearray` fields
  are zeroed in place.
- **A transport constructed with a literal and never connected** retains
  the descriptor until GC.  Use the resolver form if this matters.
- **Channel trust is the operator's responsibility.** The library does not
  enforce TLS.  A plaintext descriptor against a permissive broker sends
  bytes in plaintext.
