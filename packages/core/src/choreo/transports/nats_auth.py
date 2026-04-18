"""NATS authentication descriptors.

Each variant is a frozen dataclass representing one of the auth modes
``nats-py``'s ``Client.connect()`` supports.  Secret-bearing fields use
``repr=False``; the base class supplies a safe ``__repr__`` that prints
only the variant tag.

Usage::

    from choreo.transports import NatsTransport, NatsAuth

    # Literal (weaker lifetime — secret in memory at construction)
    transport = NatsTransport(
        servers=["nats://broker:4222"],
        auth=NatsAuth.token("s3cret"),
    )

    # Resolver (stronger lifetime — secret materialised inside connect())
    transport = NatsTransport(
        servers=["nats://broker:4222"],
        auth=lambda: NatsAuth.token(os.environ["NATS_TOKEN"]),
    )

See ADR-0020 for the full design rationale.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from pathlib import Path

from ._auth import _TransportAuth, _register_variants


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

@dataclass(frozen=True, eq=False, repr=False)
class _NatsUserPassword(_TransportAuth):
    """NATS user/password authentication."""

    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsToken(_TransportAuth):
    """NATS token authentication."""

    token: str = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsNKey(_TransportAuth):
    """NATS NKey seed authentication."""

    seed: str | bytes | bytearray = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsCredentialsFile(_TransportAuth):
    """NATS credentials file (.creds) authentication."""

    path: Path


@dataclass(frozen=True, eq=False, repr=False)
class _NatsTLS(_TransportAuth):
    """NATS TLS-only (unauthenticated tunnel)."""

    ca: str | Path | bytes | ssl.SSLContext
    cert: str | Path | bytes | None = None
    key: str | Path | bytes | None = None
    hostname: str | None = None


@dataclass(frozen=True, eq=False, repr=False)
class _NatsUserPasswordWithTLS(_TransportAuth):
    """NATS user/password over TLS."""

    username: str
    password: str = field(repr=False)
    tls: _NatsTLS = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsTokenWithTLS(_TransportAuth):
    """NATS token over TLS."""

    token: str = field(repr=False)
    tls: _NatsTLS = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsNKeyWithTLS(_TransportAuth):
    """NATS NKey seed over TLS."""

    seed: str | bytes | bytearray = field(repr=False)
    tls: _NatsTLS = field(repr=False)


@dataclass(frozen=True, eq=False, repr=False)
class _NatsCredentialsFileWithTLS(_TransportAuth):
    """NATS credentials file (.creds) over TLS."""

    path: Path
    tls: _NatsTLS = field(repr=False)


# ---------------------------------------------------------------------------
# Public namespace — named constructors
# ---------------------------------------------------------------------------

class NatsAuth:
    """NATS authentication descriptors.

    Each class method returns a frozen descriptor accepted by
    ``NatsTransport(auth=...)``.  The descriptor is consumed (cleared)
    after ``connect()`` completes.
    """

    @staticmethod
    def user_password(username: str, password: str) -> _NatsUserPassword:
        return _NatsUserPassword(username=username, password=password)

    @staticmethod
    def token(token: str) -> _NatsToken:
        return _NatsToken(token=token)

    @staticmethod
    def nkey(seed: str | bytes | bytearray) -> _NatsNKey:
        return _NatsNKey(seed=seed)

    @staticmethod
    def credentials_file(path: Path | str) -> _NatsCredentialsFile:
        return _NatsCredentialsFile(path=Path(path))

    @staticmethod
    def tls(
        ca: str | Path | bytes | ssl.SSLContext,
        cert: str | Path | bytes | None = None,
        key: str | Path | bytes | None = None,
        hostname: str | None = None,
    ) -> _NatsTLS:
        return _NatsTLS(ca=ca, cert=cert, key=key, hostname=hostname)

    @staticmethod
    def user_password_with_tls(
        username: str,
        password: str,
        tls: _NatsTLS,
    ) -> _NatsUserPasswordWithTLS:
        return _NatsUserPasswordWithTLS(username=username, password=password, tls=tls)

    @staticmethod
    def token_with_tls(token: str, tls: _NatsTLS) -> _NatsTokenWithTLS:
        return _NatsTokenWithTLS(token=token, tls=tls)

    @staticmethod
    def nkey_with_tls(
        seed: str | bytes | bytearray,
        tls: _NatsTLS,
    ) -> _NatsNKeyWithTLS:
        return _NatsNKeyWithTLS(seed=seed, tls=tls)

    @staticmethod
    def credentials_file_with_tls(
        path: Path | str,
        tls: _NatsTLS,
    ) -> _NatsCredentialsFileWithTLS:
        return _NatsCredentialsFileWithTLS(path=Path(path), tls=tls)


# ---------------------------------------------------------------------------
# Register variants
# ---------------------------------------------------------------------------

_ALL_NATS_VARIANTS: frozenset[type] = frozenset({
    _NatsUserPassword,
    _NatsToken,
    _NatsNKey,
    _NatsCredentialsFile,
    _NatsTLS,
    _NatsUserPasswordWithTLS,
    _NatsTokenWithTLS,
    _NatsNKeyWithTLS,
    _NatsCredentialsFileWithTLS,
})

_register_variants("nats", _ALL_NATS_VARIANTS)
# Mock accepts all NATS variants for shape-validation parity.
_register_variants("mock", _ALL_NATS_VARIANTS)
