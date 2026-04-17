"""Harness — suite-scoped facade for the test framework (ADR-0001).

The Harness is a pure coordinator. It takes any object satisfying the
`Transport` Protocol, plus a `Codec` for decoding payloads, and nothing else.

The transport owns its own config — endpoints, identities, credentials, its
own allowlist enforcement. The Harness doesn't know whether it's talking to
NATS, Kafka, RabbitMQ, Redis, or a mock; that's the transport's concern.

Consumer pattern:

    from choreo import Harness
    from choreo.transports import MockTransport
    from choreo.codecs import JSONCodec

    transport = MockTransport(
        allowlist_path=Path("config/allowlist.yaml"),
        endpoint="mock://localhost",
    )
    harness = Harness(transport)   # codec defaults to JSON
    await harness.connect()
    try:
        async with harness.scenario("name") as s:
            ...
    finally:
        await harness.disconnect()
"""

from __future__ import annotations

import logging
from typing import Any

from .codecs import Codec, JSONCodec
from .correlation import CorrelationPolicy, NoCorrelationPolicy
from .transports import Transport, TransportCallback
from .transports.base import OnSent

_LOG = logging.getLogger("choreo.harness")


class Harness:
    """Pure coordinator over a Transport. No queue-specific knowledge."""

    def __init__(
        self,
        transport: Transport,
        *,
        codec: Codec | None = None,
        correlation: CorrelationPolicy | None = None,
    ) -> None:
        """Construct a Harness around a transport instance.

        Args:
            transport: Anything satisfying the `Transport` Protocol. Built-in:
                `MockTransport`. Consumers construct their own transports for
                real backends.
            codec: How raw wire bytes are decoded for matching. Defaults to
                `JSONCodec`. Consumers can plug in protobuf / Avro / tag-value /
                raw-bytes codecs for non-JSON backends.
            correlation: Controls whether and how correlation ids are attached
                to outbound messages and read from inbound ones. Default is
                `NoCorrelationPolicy()` — transparent passthrough; inbound
                routing falls back to broadcast (every live scope on a topic
                sees every message). Consumers who need per-scope isolation
                pass a `DictFieldPolicy` (or `test_namespace()` for the
                `TEST-`-prefixed profile, pre-ADR-0019 captive behaviour).
                See ADR-0019.
        """
        self._transport = transport
        self._codec: Codec = codec if codec is not None else JSONCodec()
        self._correlation: CorrelationPolicy = (
            correlation if correlation is not None else NoCorrelationPolicy()
        )
        self._connected = False

    # ---- lifecycle --------------------------------------------------------

    async def connect(self) -> None:
        """Delegate to the transport. Any allowlist enforcement, credential
        handling, or socket-opening happens inside the transport.

        Idempotent on an already-connected harness — the call is a no-op. If
        the transport raises, `_connected` stays False and the caller sees
        the exception.
        """
        if self._connected:
            return
        await self._transport.connect()
        self._connected = True
        self._warn_if_noop_policy_on_real_transport()

    def _warn_if_noop_policy_on_real_transport(self) -> None:
        """ADR-0019 §Security Considerations §Defence-in-depth regression.

        Emit a structured WARNING when a no-op policy is paired with any
        transport other than MockTransport — scope isolation is off, and
        against a real broker this means messages fan out across scopes.
        """
        if not isinstance(self._correlation, NoCorrelationPolicy):
            return
        transport_class = type(self._transport).__name__
        if transport_class == "MockTransport":
            return
        _LOG.warning(
            "correlator_noop_against_real_transport: "
            "Harness was constructed with NoCorrelationPolicy against %s; "
            "per-scope correlation routing is disabled and inbound messages "
            "fan out to every live scope. Safe only on dedicated / per-run "
            "infrastructure (ADR-0019).",
            transport_class,
        )

    async def disconnect(self) -> None:
        """Delegate. Idempotent regardless of whether the transport raised.

        If `transport.disconnect()` raises, the harness still transitions to
        the disconnected state and clears any subscription tracking so a
        subsequent `disconnect()` is a clean no-op. The exception propagates
        so the caller sees it — but the invariant "after disconnect() returns
        (by exception or not), the harness is no longer usable for publish"
        is preserved.
        """
        if not self._connected:
            return
        try:
            await self._transport.disconnect()
        finally:
            try:
                self._transport.clear_subscriptions()
            except Exception:
                pass
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def codec(self) -> Codec:
        """Read-only accessor for the codec held by this harness.

        Scenario subscriber callbacks read this to decode inbound wire bytes
        before running matchers. Exposed so `choreo.scenario` does not reach
        into `_codec` across the module boundary."""
        return self._codec

    @property
    def correlation(self) -> CorrelationPolicy:
        """Read-only accessor for the CorrelationPolicy held by this harness.

        The Scenario DSL routes id generation, outbound stamping, and inbound
        filtering through this policy (ADR-0019).
        """
        return self._correlation

    # ---- facade over the transport ---------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        self._require_connected()
        self._transport.subscribe(topic, callback)

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        self._transport.unsubscribe(topic, callback)

    def publish(
        self,
        topic: str,
        payload: bytes | Any,
        *,
        on_sent: OnSent | None = None,
    ) -> None:
        """Publish to the transport. Bytes go straight through; anything
        else is run through the codec's `encode()`.

        `on_sent` is an optional hook the transport invokes at the post-wire
        moment for the message — scenarios use this to timestamp PUBLISHED
        / REPLIED events after the bytes have actually left, which matters
        for async transports (NATS) where `publish()` returns before the
        send has completed.
        """
        self._require_connected()
        if isinstance(payload, (bytes, bytearray, memoryview)):
            wire = bytes(payload)
        else:
            wire = self._codec.encode(payload)
        self._transport.publish(topic, wire, on_sent=on_sent)

    def active_subscription_count(self) -> int:
        return self._transport.active_subscription_count()

    def scenario(self, name: str) -> Any:
        """Enter a scenario scope. Returns an async context manager yielding
        a Scenario. See ADR-0012, ADR-0014, ADR-0015."""
        from .scenario import _ScenarioScope

        return _ScenarioScope(self, name)

    # ---- internals --------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Harness is not connected; call connect() first")

    # ---- safety -----------------------------------------------------------

    def __repr__(self) -> str:
        return f"<Harness transport={type(self._transport).__name__} connected={self._connected}>"

    def __reduce__(self) -> Any:
        raise TypeError(
            "Harness is not pickleable — transports may hold credentials and "
            "live sockets that must not cross a process boundary (ADR-0001)"
        )
