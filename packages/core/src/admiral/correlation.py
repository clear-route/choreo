"""Correlation policy — pluggable injection and extraction (ADR-0019).

The Harness no longer hardcodes a correlation field name or format. A
`CorrelationPolicy` instance decides how a correlation id is generated,
written onto an outbound `Envelope`, and read from an inbound one. The
default is `NoCorrelationPolicy` — no mutation, no filtering, no routing.

Shipped profiles:

    NoCorrelationPolicy()             # transparent passthrough (default)
    DictFieldPolicy(field="trace_id") # stamp/read a dict field
    test_namespace()                  # DictFieldPolicy with TEST- prefix

A `CorrelationPolicy` is consumer code: exceptions raised inside
`new_id` / `write` / `read` are wrapped in `CorrelationPolicyError` at
call sites so a buggy policy fails the scenario cleanly rather than
poisoning the event loop.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Envelope:
    """Internal shape seen by a CorrelationPolicy.

    Not wire-visible: transports still deal in bytes. The envelope sits
    between Scenario and codec/transport, giving header-based policies
    somewhere to put the correlation id without mutating the payload.
    """

    topic: str
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class CorrelationPolicy(Protocol):
    """Strategy for embedding and extracting a correlation id.

    Contract:
      * `write` returns a new `Envelope`; implementations must not mutate
        the input envelope or its payload in place.
      * `read` returns `None` when no id is present. `None` triggers the
        broadcast fallback on inbound.
      * `new_id` may be async; sync implementations return immediately.
      * `routes_by_correlation` is True when this policy produces per-scope
        isolation on inbound routing. Negative-assertion matchers gate on
        this flag.

    See ADR-0019 Security Considerations for the trust-boundary contract.
    """

    async def new_id(self) -> str | None: ...

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope: ...

    def read(self, envelope: Envelope) -> str | None: ...

    @property
    def routes_by_correlation(self) -> bool: ...


class CorrelationPolicyError(RuntimeError):
    """Raised when a CorrelationPolicy method raises unexpectedly.

    Wraps the original exception and records the policy class name so the
    diagnostic names the offending policy rather than surfacing a raw
    RuntimeError from inside the Harness.
    """

    def __init__(self, policy_class: str, method: str, original: BaseException) -> None:
        super().__init__(f"{policy_class}.{method} raised {type(original).__name__}")
        self.policy_class = policy_class
        self.method = method
        self.original = original


class CorrelationIdNotInNamespaceError(ValueError):
    """Raised by DictFieldPolicy.write when an explicit correlation id
    does not match the policy's configured prefix."""


class NoCorrelationPolicy:
    """Transparent no-op. Payloads are neither stamped nor read.

    Under this policy, `Scenario.correlation_id` is `None` and inbound
    routing falls back to broadcast — every live scope subscribed to a
    topic sees every message on it. This is only safe on dedicated or
    per-run infrastructure: on a shared broker, messages from sibling
    scopes (or sibling tests / pipelines / tenants) will fan out.

    See ADR-0019 §Security Considerations §Broadcast confidentiality.
    """

    async def new_id(self) -> None:
        return None

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        return envelope

    def read(self, envelope: Envelope) -> None:
        return None

    @property
    def routes_by_correlation(self) -> bool:
        return False


class DictFieldPolicy:
    """Stamps a string field on dict payloads; reads the same field back.

    Non-dict payloads pass through unchanged (write is a no-op, read
    returns None). This matches the pre-ADR-0019 library behaviour when
    instantiated with `field="correlation_id"` and no prefix.

    WARNING: without a distinguishing `prefix`, two instances that share
    the same `field` share a correlation namespace. On shared broker
    infrastructure this exposes one scope's messages to another scope's
    filter. Use a prefix on anything other than dedicated per-run infra.
    See ADR-0019 §Security Considerations.
    """

    def __init__(
        self,
        field: str = "correlation_id",
        prefix: str = "",
        id_generator: Callable[[], str] | None = None,
    ) -> None:
        """Build a DictFieldPolicy.

        Args:
            field: Key on the dict payload used for the correlation id.
            prefix: Optional string prepended to every generated id and
                required on any explicit override written through
                `Scenario.publish`. An explicit id that does not start
                with `prefix` raises `CorrelationIdNotInNamespaceError`.
            id_generator: Callable producing the id body. Defaults to
                `secrets.token_hex(16)` — 128 bits of entropy. Collision
                resistance is the consumer's responsibility; the library
                does not attempt runtime collision detection.
        """
        self._field = field
        self._prefix = prefix
        self._id_generator = id_generator if id_generator is not None else _default_id

    async def new_id(self) -> str:
        return f"{self._prefix}{self._id_generator()}"

    def write(self, envelope: Envelope, correlation_id: str) -> Envelope:
        if not isinstance(envelope.payload, dict):
            return envelope
        if self._prefix and not (
            isinstance(correlation_id, str) and correlation_id.startswith(self._prefix)
        ):
            raise CorrelationIdNotInNamespaceError(
                f"correlation_id {correlation_id!r} is not in the "
                f"{self._prefix!r} namespace; explicit overrides must "
                "start with the policy's prefix"
            )
        if self._field in envelope.payload:
            # Caller set the field explicitly; validate the prefix then
            # leave the payload alone. Mirrors the pre-ADR-0019 semantic
            # that an explicit correlation id is honoured, not silently
            # overwritten.
            existing = envelope.payload[self._field]
            if self._prefix and not (
                isinstance(existing, str) and existing.startswith(self._prefix)
            ):
                raise CorrelationIdNotInNamespaceError(
                    f"{self._field}={existing!r} is not in the {self._prefix!r} namespace"
                )
            return envelope
        new_payload = {**envelope.payload, self._field: correlation_id}
        return Envelope(topic=envelope.topic, payload=new_payload, headers=envelope.headers)

    def read(self, envelope: Envelope) -> str | None:
        payload = envelope.payload
        if not isinstance(payload, dict):
            return None
        value = payload.get(self._field)
        return value if isinstance(value, str) else None

    @property
    def routes_by_correlation(self) -> bool:
        return True


def test_namespace(field: str = "correlation_id") -> DictFieldPolicy:
    """Factory matching the pre-ADR-0019 library posture.

    Returns a `DictFieldPolicy` that stamps a `TEST-` prefix on every
    correlation id and enforces the prefix on explicit overrides. Used
    by the captive test suite; implements the downstream ingress-filter
    contract previously described in ADR-0006 §Security Considerations.
    """
    return DictFieldPolicy(field=field, prefix="TEST-")


def _default_id() -> str:
    return secrets.token_hex(16)


__all__ = [
    "CorrelationIdNotInNamespaceError",
    "CorrelationPolicy",
    "CorrelationPolicyError",
    "DictFieldPolicy",
    "Envelope",
    "NoCorrelationPolicy",
    "test_namespace",
]
