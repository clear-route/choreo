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
    pip install 'choreo[kafka]'   # pulls aiokafka >= 0.11
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
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
        allowlist_path: Path | None = None,
        client_id: str = "choreo",
        connect_timeout_s: float = 10.0,
        request_timeout_ms: int = 4000,
    ) -> None:
        if not bootstrap_servers:
            raise ValueError("KafkaTransport requires at least one bootstrap server")
        self._bootstrap_servers = list(bootstrap_servers)
        self._allowlist_path = allowlist_path
        self._client_id = client_id
        self._connect_timeout_s = connect_timeout_s
        self._request_timeout_ms = request_timeout_ms
        self._producer: Any = None
        self._admin: Any = None
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
            from aiokafka.admin import AIOKafkaAdminClient
            from aiokafka.errors import KafkaConnectionError, KafkaError
        except ImportError as e:
            raise TransportError(
                "KafkaTransport requires aiokafka — install with `pip install 'choreo[kafka]'`"
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
            await asyncio.wait_for(producer.start(), timeout=self._connect_timeout_s)
        except (TimeoutError, KafkaConnectionError, KafkaError) as e:
            # stop() may raise on a never-started producer, ignore.
            try:
                await producer.stop()
            except Exception:
                pass
            raise TransportError(
                f"could not connect to Kafka at {self._bootstrap_servers!r}: {e}"
            ) from e

        # AdminClient so subscribe() can pre-create topics. Without this,
        # a consumer created for a non-existent topic stalls indefinitely
        # in `consumer.start()` waiting on metadata — aiokafka does not
        # honour broker-side auto-create for consumer-initiated metadata
        # fetches. Pre-creating via the admin API is idempotent (already
        # existing topics surface TopicAlreadyExistsError, which we swallow).
        admin = AIOKafkaAdminClient(
            bootstrap_servers=self._bootstrap_servers,
            client_id=f"{self._client_id}-admin",
            request_timeout_ms=self._request_timeout_ms,
        )
        try:
            await asyncio.wait_for(admin.start(), timeout=self._connect_timeout_s)
        except (TimeoutError, KafkaConnectionError, KafkaError):
            # The admin client is best-effort; if it can't start, subscribe()
            # falls back to hoping the topic already exists or gets created
            # by a concurrent produce. We still continue with the producer.
            try:
                await admin.close()
            except Exception:
                pass
            admin = None

        self._producer = producer
        self._admin = admin

    async def disconnect(self) -> None:
        if self._producer is None and not self._subs:
            return

        # Stop all consumer reader tasks first.
        consumers: list[Any] = []
        tasks: list[asyncio.Task[Any]] = []
        for entries in self._subs.values():
            for _cb, consumer, task in entries:
                task.cancel()
                consumers.append(consumer)
                tasks.append(task)
        # Await cancellations, but never more than a few seconds per task.
        # aiokafka's consumer.start() can stall on metadata fetch for a
        # non-existent topic and ignore task.cancel() briefly; a hard cap
        # keeps teardown from inheriting that stall.
        for task in tasks:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        # Stop consumers after reader tasks have exited the poll loop.
        for consumer in consumers:
            try:
                await asyncio.wait_for(consumer.stop(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        # Drain any producer tasks we spawned. Bounded so a single stuck
        # task (typically an unsubscribe teardown on a stalled consumer)
        # cannot wedge disconnect indefinitely.
        if self._pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._pending, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                pass

        if self._producer is not None:
            try:
                await asyncio.wait_for(self._producer.stop(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        if self._admin is not None:
            try:
                await asyncio.wait_for(self._admin.close(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        self._producer = None
        self._admin = None
        self._subs.clear()
        self._pending.clear()
        self._pending_subs.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaTransport is not connected; cannot subscribe")

        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as e:
            raise TransportError(
                "KafkaTransport requires aiokafka — install with `pip install 'choreo[kafka]'`"
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
                # Pre-create the topic via the admin client. aiokafka
                # consumers hang on metadata fetch for a topic that doesn't
                # exist yet (even with broker-side auto-create enabled),
                # so we create-if-missing before calling consumer.start().
                await self._ensure_topic(topic)
                # Bound consumer.start() — if metadata is still unavailable
                # (admin failed, cluster slow), aiokafka can stall here.
                # connect_timeout_s is the authoritative budget for
                # "subscribe should be ready by now".
                await asyncio.wait_for(
                    consumer.start(), timeout=self._connect_timeout_s
                )
                # Force the consumer's read position to the current end
                # offset BEFORE signalling ready. With auto_offset_reset=
                # 'latest' and a fresh group_id, position is otherwise
                # resolved lazily on the first poll — which races
                # publish(): the published record is written between
                # consumer.start() returning and the first poll, the poll
                # then sees position=latest AFTER the record, and the
                # consumer silently skips it. Explicit seek_to_end pins
                # position at the current high-water mark so subsequent
                # publishes land at a later offset that this consumer
                # does read.
                try:
                    await asyncio.wait_for(
                        consumer.seek_to_end(), timeout=self._connect_timeout_s
                    )
                except Exception:
                    # seek_to_end can race partition assignment on a slow
                    # cluster; fall through and let the first poll resolve
                    # position — marginal behaviour is already better than
                    # the hang we were fixing.
                    pass
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
                return
            # After start() + seek, the consumer is positioned and
            # subsequent publishes will be picked up reliably.
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
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            try:
                await asyncio.wait_for(consumer.stop(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

        self._track(loop.create_task(_teardown()))

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: OnSent | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaTransport is not connected; cannot publish")
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

    async def _ensure_topic(self, topic: str) -> None:
        """Idempotently create `topic` so consumer subscribe doesn't stall.

        aiokafka consumers wait on metadata for missing topics indefinitely,
        regardless of the broker's ``auto.create.topics.enable``. Calling
        ``AdminClient.create_topics`` first forces the topic into existence
        before we try to consume from it. If the topic already exists or the
        admin client isn't available, we swallow and let the consumer try
        its luck.
        """
        if self._admin is None:
            return
        try:
            from aiokafka.admin import NewTopic
            from aiokafka.errors import TopicAlreadyExistsError
        except ImportError:
            return
        try:
            await asyncio.wait_for(
                self._admin.create_topics(
                    [NewTopic(name=topic, num_partitions=1, replication_factor=1)]
                ),
                timeout=self._connect_timeout_s,
            )
        except TopicAlreadyExistsError:
            pass
        except (asyncio.TimeoutError, Exception):
            # Best effort — fall through to consumer.start(), which will
            # surface its own TimeoutError if metadata never arrives.
            pass


def _normalise_bootstrap(entry: str) -> str:
    """Accept both ``host:port`` and ``kafka://host:port``; return the
    bare form so the allowlist can contain either shape."""
    if "://" in entry:
        parsed = urlparse(entry)
        if parsed.hostname and parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
    return entry
