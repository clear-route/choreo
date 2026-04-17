"""Transport Protocol — the contract every queue backend implements.

The Harness talks only through this interface. All queue-specific behaviour
(allowlists, credentials, wire format) belongs to the implementing class.

To add a new backend (Kafka, RabbitMQ, NATS, …), implement the five methods
below and the Harness won't know the difference.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


TransportCallback = Callable[[str, bytes], None]


@dataclass(frozen=True)
class TransportCapabilities:
    """Declarative description of what a transport can actually do.

    Contract tests read these to decide whether to run or skip — the 5-method
    Protocol is the shape of every transport, but semantics diverge
    (Redis Pub/Sub loses messages without an active subscriber; Kafka splits
    within a consumer group; RabbitMQ supports wildcards via topic exchanges;
    etc.). Declare honestly: a False flag skips the corresponding contract
    test rather than silently failing.
    """

    # Subscribing the same callback N times delivers each message N times.
    # True for NATS/Redis/MockTransport; true for our Kafka/Rabbit impls
    # because each subscribe() owns its own consumer / queue.
    broadcast_fanout: bool = True

    # A message published before any subscriber exists is lost. True for
    # pub/sub systems without durability; False for queue systems that
    # retain messages until consumed.
    loses_messages_without_subscriber: bool = True

    # The transport preserves publish order for a single topic.
    ordered_per_topic: bool = True

# Callback fired by `publish()` when the message is confirmed on-wire.
# Synchronous transports (MockTransport) fire it immediately before
# returning; asynchronous transports (NatsTransport) fire it inside the
# publish task AFTER the broker has acknowledged the send. This is the
# hook scenarios use to timestamp PUBLISHED / REPLIED events at their
# true post-wire moment rather than at the moment the publish call was
# made (which for async transports is pre-wire and misleading).
OnSent = Callable[[], None]


class TransportError(Exception):
    """Base for transport-level failures."""


@runtime_checkable
class Transport(Protocol):
    """The operations every pub/sub backend supports.

    Implementations are responsible for their own configuration, allowlist
    enforcement at `connect()`, and thread-safety when firing callbacks across
    threads. Callbacks must run on the asyncio loop thread. Shipping
    transports are all natively async and schedule work via
    `loop.create_task(...)` directly; a threaded transport would cross the
    boundary via `core._internal.LoopPoster` (reserved scaffolding, ADR-0003).

    `publish()` accepts an optional `on_sent` callback that the transport
    MUST invoke on the loop thread at the post-wire moment for the message.
    Consumers that don't care about post-wire timing can pass `None`.

    `capabilities` declares which semantics this transport honours; contract
    tests read it to decide what to exercise.

    `active_subscription_count()` and `clear_subscriptions()` are part of the
    contract so the Harness and scope teardown can audit and reset subscriber
    state uniformly across backends — both are relied on by tests and the
    scope exit path.
    """

    capabilities: TransportCapabilities

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def subscribe(self, topic: str, callback: TransportCallback) -> None: ...
    def unsubscribe(self, topic: str, callback: TransportCallback) -> None: ...
    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None: ...
    def active_subscription_count(self) -> int: ...
    def clear_subscriptions(self) -> None: ...
