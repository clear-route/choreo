"""Transport Protocol — the contract every queue backend implements.

The Harness talks only through this interface. All queue-specific behaviour
(allowlists, credentials, wire format) belongs to the implementing class.

To add a new backend (Kafka, RabbitMQ, NATS, …), implement the five methods
below and the Harness won't know the difference.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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


def safe_url(url: str) -> str:
    """Return *url* with any embedded credentials redacted.

    Transports format their connection URL into exception messages on connect
    failure.  URLs like ``amqp://user:pass@host`` or ``redis://:pw@host``
    carry credentials inline; we must not leak those into tracebacks that
    reach CI logs or test reports.

    Redaction covers two surfaces:

    1. **Userinfo** — ``user:password@`` is replaced by ``<redacted>@``.
    2. **Query-string parameters** — any key whose case-folded name is in
       ``_CREDENTIAL_KEY_NAMES`` (defined in ``transports/_auth.py``) has
       its value replaced by ``<redacted>``.  Percent-encoded keys are
       normalised before matching; repeated keys are all inspected.

    A URL without credentials (in either surface) is returned unchanged.
    """
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    except ImportError:
        return url

    # Lazy import to avoid circular dependency at module load time.
    from ._auth import _CREDENTIAL_KEY_NAMES

    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    has_userinfo = bool(parts.username or parts.password)

    # --- query-string redaction ---
    redacted_query = parts.query
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        needs_redaction = any(k.casefold() in _CREDENTIAL_KEY_NAMES for k, _ in pairs)
        if needs_redaction:
            redacted_pairs = [
                (k, "<redacted>") if k.casefold() in _CREDENTIAL_KEY_NAMES else (k, v)
                for k, v in pairs
            ]
            redacted_query = urlencode(redacted_pairs)

    if not has_userinfo and redacted_query == parts.query:
        return url

    # --- userinfo redaction ---
    if has_userinfo:
        host = parts.hostname or ""
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        netloc = f"<redacted>@{host}" if host else "<redacted>"
    else:
        netloc = parts.netloc

    return urlunsplit((parts.scheme, netloc, parts.path, redacted_query, parts.fragment))


@runtime_checkable
class Transport(Protocol):
    """The operations every pub/sub backend supports.

    Implementations are responsible for their own configuration, allowlist
    enforcement at `connect()`, and thread-safety when firing callbacks across
    threads. Callbacks must run on the asyncio loop thread. Shipping
    transports are all natively async and schedule work via
    `loop.create_task(...)` directly; a threaded transport would cross the
    boundary via `choreo._internal.LoopPoster` (reserved scaffolding, ADR-0003).

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
        on_sent: OnSent | None = None,
    ) -> None: ...
    def active_subscription_count(self) -> int: ...
    def clear_subscriptions(self) -> None: ...
