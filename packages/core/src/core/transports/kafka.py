"""KafkaTransport — a real-network pub/sub backend over aiokafka.

Purpose: exercise the Transport Protocol contract against a live Kafka broker
in the e2e suite. Kafka is the most widely deployed queue backend; this
transport lets the same contract tests that run against NATS/Mock also run
against Kafka without the Harness knowing.

Threading / loop model:
    aiokafka is fully asyncio. The harness's Transport Protocol is sync for
    subscribe / unsubscribe / publish. We bridge by scheduling coroutines on
    the running loop via ``loop.create_task(...)``, tracking them in a pending
    set that ``disconnect()`` drains.

Semantics choice — one consumer per subscribe() call:
    Kafka's native fan-out model is consumer groups: members of a group split
    partitions; independent groups each receive every message. To match the
    NATS/Mock fan-out behaviour (where subscribing twice means delivering
    twice), each ``subscribe()`` call spins up its own AIOKafkaConsumer with a
    unique ``group_id``. Heavier than the NATS model, but it keeps the
    contract uniform.

Delivery semantics — latest offset:
    Consumers start at ``auto_offset_reset=latest`` so that a test which
    subscribes and then publishes gets the message, and a test that publishes
    with no subscribers does NOT retroactively feed messages to a later
    subscriber (matching NATS/Redis semantics). If you need at-least-once
    from-earliest behaviour, instantiate a separate transport.

Installation:
    pip install 'core[kafka]'   # pulls aiokafka >= 0.11
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from ..environment import load_allowlist
from .base import OnSent, TransportCallback, TransportCapabilities, TransportError


class KafkaTransport:
    """Transport backed by a real Kafka cluster via ``aiokafka``.

    Args:
        bootstrap_servers: One or more ``host:port`` bootstrap entries.
            All must appear in the allowlist's ``kafka_brokers`` category
            when ``allowlist_path`` is supplied.
        allowlist_path: Optional YAML path. When given, every bootstrap
            entry is validated against the ``kafka_brokers`` category at
            connect time.
        client_id: Client identity reported to the broker (visible in
            ``kafka-consumer-groups`` output).
        connect_timeout_s: Seconds before a connect attempt fails.
        request_timeout_ms: Underlying producer request timeout; consumer
            session timeouts are scaled from this.
    """

    capabilities = TransportCapabilities(
        broadcast_fanout=True,
        loses_messages_without_subscriber=True,
        ordered_per_topic=True,
    )

    def __init__(
        self,
        *,
        bootstrap_servers: list[str],
        allowlist_path: Optional[Path] = None,
        client_id: str = "choreo",
        connect_timeout_s: float = 10.0,
        request_timeout_ms: int = 4000,
    ) -> None:
        if not bootstrap_servers:
            raise ValueError(
                "KafkaTransport requires at least one bootstrap server"
            )
        self._bootstrap_servers = list(bootstrap_servers)
        self._allowlist_path = allowlist_path
        self._client_id = client_id
        self._connect_timeout_s = connect_timeout_s
        self._request_timeout_ms = request_timeout_ms
        self._producer: Any = None
        # (callback, consumer, reader_task) per topic; a list so repeated
        # subscribes of the same callback fan out independently.
        self._subs: dict[
            str,
            list[tuple[TransportCallback, Any, asyncio.Task[Any]]],
        ] = {}
        self._pending: set[asyncio.Task[Any]] = set()
        # Subscribe-ready futures: subscribe() pushes a Future here that
        # resolves once the underlying AIOKafkaConsumer has joined its
        # group and been assigned its partition. publish() awaits any
        # in-flight readies so the classic subscribe-then-publish
        # pattern doesn't race the consumer join.
        self._pending_subs: set[asyncio.Future[None]] = set()

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        if self._allowlist_path is not None:
            # Kafka bootstrap entries are bare ``host:port``; permit both the
            # raw form and a ``kafka://host:port`` URL form for consistency
            # with other transports' allowlist shapes.
            load_allowlist(self._allowlist_path).enforce(
                "kafka_brokers",
                self._bootstrap_servers,
                label="Kafka broker",
                normalise=_normalise_bootstrap,
            )

        try:
            from aiokafka import AIOKafkaProducer
            from aiokafka.errors import KafkaConnectionError, KafkaError
        except ImportError as e:
            raise TransportError(
                "KafkaTransport requires aiokafka — "
                "install with `pip install 'core[kafka]'`"
            ) from e

        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            request_timeout_ms=self._request_timeout_ms,
            # acks='all' so on_sent semantics match other transports: the hook
            # fires after the broker has acknowledged the write.
            acks="all",
        )
        try:
            await asyncio.wait_for(
                producer.start(), timeout=self._connect_timeout_s
            )
        except (KafkaConnectionError, KafkaError, asyncio.TimeoutError) as e:
            # stop() may raise on a never-started producer, ignore.
            try:
                await producer.stop()
            except Exception:
                pass
            raise TransportError(
                f"could not connect to Kafka at {self._bootstrap_servers!r}: {e}"
            ) from e

        self._producer = producer

    async def disconnect(self) -> None:
        if self._producer is None and not self._subs:
            return

        # Stop all consumer reader tasks first.
        consumers: list[Any] = []
        for entries in self._subs.values():
            for _cb, consumer, task in entries:
                task.cancel()
                consumers.append(consumer)
        # Await cancellations.
        for entries in self._subs.values():
            for _cb, _consumer, task in entries:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Stop consumers after reader tasks have exited the poll loop.
        for consumer in consumers:
            try:
                await consumer.stop()
            except Exception:
                pass

        # Drain any producer tasks we spawned.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        if self._producer is not None:
            try:
                await self._producer.stop()
            except Exception:
                pass

        self._producer = None
        self._subs.clear()
        self._pending.clear()
        self._pending_subs.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._producer is None:
            raise RuntimeError(
                "KafkaTransport is not connected; cannot subscribe"
            )

        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as e:
            raise TransportError(
                "KafkaTransport requires aiokafka — "
                "install with `pip install 'core[kafka]'`"
            ) from e

        loop = asyncio.get_running_loop()
        # Unique group_id so every subscribe() consumes independently of all
        # other subscribes (broadcast fan-out semantics).
        group_id = f"{self._client_id}-{uuid.uuid4().hex[:12]}"
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            group_id=group_id,
            # Only receive messages published after subscribe lands — mirrors
            # NATS/Redis Pub/Sub: a late subscriber does not get replays.
            auto_offset_reset="latest",
            enable_auto_commit=False,
            request_timeout_ms=self._request_timeout_ms,
            # Heartbeat / session scaled from request_timeout_ms so they
            # stay internally consistent.
            session_timeout_ms=max(6000, self._request_timeout_ms + 2000),
            heartbeat_interval_ms=max(2000, self._request_timeout_ms // 2),
        )

        ready: asyncio.Future[None] = loop.create_future()
        self._pending_subs.add(ready)
        ready.add_done_callback(self._pending_subs.discard)

        async def _reader() -> None:
            try:
                await consumer.start()
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
                return
            # After start() returns, the consumer has joined the group and
            # been assigned its partitions — a subsequent publish will land
            # on a subscriber that actually exists on the broker.
            if not ready.done():
                ready.set_result(None)
            try:
                async for msg in consumer:
                    try:
                        callback(msg.topic, bytes(msg.value))
                    except Exception:
                        # One broken callback must not kill the reader loop.
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                return

        task = loop.create_task(_reader())
        self._track(task)
        self._subs.setdefault(topic, []).append((callback, consumer, task))

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        entries = self._subs.get(topic)
        if not entries:
            return
        target: tuple[TransportCallback, Any, asyncio.Task[Any]] | None = None
        for idx, entry in enumerate(entries):
            if entry[0] is callback:
                target = entry
                entries.pop(idx)
                break
        if target is None:
            return
        if not entries:
            self._subs.pop(topic, None)

        _cb, consumer, task = target
        loop = asyncio.get_running_loop()

        async def _teardown() -> None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await consumer.stop()
            except Exception:
                pass

        self._track(loop.create_task(_teardown()))

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError(
                "KafkaTransport is not connected; cannot publish"
            )
        loop = asyncio.get_running_loop()
        producer = self._producer
        # Snapshot ready-futures now so the publish waits for any SUBs
        # currently joining their group to finish joining before the
        # bytes go out. Without this, a subscribe-then-publish in the
        # same tick races the consumer join and the message is lost.
        in_flight = [f for f in self._pending_subs if not f.done()]

        async def _do_publish() -> None:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
            try:
                await producer.send_and_wait(topic, payload)
            except Exception:
                # Publish failures are swallowed — scenarios that care about
                # delivery observe timeouts / TIMEOUT outcomes, which is the
                # same signal MockTransport / NatsTransport produce.
                return
            if on_sent is not None:
                on_sent()

        self._track(loop.create_task(_do_publish()))

    # ---- diagnostics -----------------------------------------------------

    def active_subscription_count(self) -> int:
        return sum(len(entries) for entries in self._subs.values())

    def clear_subscriptions(self) -> None:
        self._subs.clear()

    # ---- internals -------------------------------------------------------

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


def _normalise_bootstrap(entry: str) -> str:
    """Accept both ``host:port`` and ``kafka://host:port``; return the
    bare form so the allowlist can contain either shape."""
    if "://" in entry:
        parsed = urlparse(entry)
        if parsed.hostname and parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
    return entry
