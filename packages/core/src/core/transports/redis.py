"""RedisTransport — a real-network pub/sub backend over redis-py.

Purpose: exercise the Transport Protocol contract against a live Redis broker
in the e2e suite. Redis Pub/Sub is fire-and-forget and broadcasts to every
subscribed client, which matches NATS/Mock semantics closely.

Routing model:
    Each ``subscribe(topic, callback)`` registers a handler on the single
    pubsub connection. One asyncio task reads from ``pubsub.listen()`` and
    dispatches to the registered callbacks for the message's channel. This
    gives broadcast fan-out: subscribing the same callback twice on a topic
    delivers each message twice, matching MockTransport.

Threading / loop model:
    redis-py async is fully asyncio. The Transport Protocol is sync for
    subscribe / unsubscribe / publish; we bridge by scheduling coroutines on
    the running loop. ``publish()`` awaits any in-flight subscribe so the
    SUBSCRIBE command has reached the server before the PUBLISH lands.

Subscribe-ack semantics:
    redis-py's ``pubsub.subscribe(channel)`` sends the SUBSCRIBE command
    but does NOT wait for the server's ``subscribe`` reply — the reply is
    delivered as a pubsub message that the reader picks up. A naive
    ``await pubsub.subscribe(chan); await client.publish(chan, msg)`` pattern
    therefore races the broker: the PUBLISH can land before the server has
    fully registered the subscription, producing a zero-recipients ``PUBLISH
    returned 0`` and a silently dropped message. To fix this, the reader
    loop does NOT set ``ignore_subscribe_messages`` — it observes each
    ``subscribe`` reply and resolves the matching pending future. The
    transport's ``publish()`` awaits that future before issuing the PUBLISH.

Installation:
    pip install 'core[redis]'   # pulls redis >= 5.0
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..environment import load_allowlist
from .base import OnSent, TransportCallback, TransportCapabilities, TransportError


@dataclass
class _RedisSub:
    callback: TransportCallback
    # Resolved when the server's ``subscribe`` reply lands on the pubsub
    # reader, signalling that the subscription is active. publish() awaits
    # this before issuing its PUBLISH so we don't race the broker.
    ready: asyncio.Future[None] = field(default_factory=asyncio.Future)


class RedisTransport:
    """Transport backed by a real Redis server via ``redis-py``.

    Args:
        url: Redis URL (e.g. ``redis://localhost:6379/0``). Must appear in
            the allowlist's ``redis_servers`` category when
            ``allowlist_path`` is supplied.
        allowlist_path: Optional YAML path. When given, the URL is
            validated against the ``redis_servers`` category at connect time.
        connect_timeout_s: Seconds before a connect attempt fails.
    """

    capabilities = TransportCapabilities(
        broadcast_fanout=True,
        loses_messages_without_subscriber=True,
        ordered_per_topic=True,
    )

    def __init__(
        self,
        *,
        url: str,
        allowlist_path: Optional[Path] = None,
        connect_timeout_s: float = 5.0,
    ) -> None:
        if not url:
            raise ValueError("RedisTransport requires a URL")
        self._url = url
        self._allowlist_path = allowlist_path
        self._connect_timeout_s = connect_timeout_s
        self._client: Any = None
        self._pubsub: Any = None
        self._reader_task: Optional[asyncio.Task[Any]] = None
        self._subs: dict[str, list[_RedisSub]] = {}
        self._pending: set[asyncio.Task[Any]] = set()
        # Futures resolved by the reader on the server's subscribe-ack.
        # publish() awaits any in-flight to enforce subscribe-before-publish.
        self._pending_ready: set[asyncio.Future[None]] = set()
        # Publishes run as tasks off the call site, but they share one Redis
        # connection. Without a lock, 50 concurrent publish-tasks race each
        # other for the connection's command queue and land at the broker in
        # wake-order rather than publish-order. The lock forces FIFO
        # per-transport publish semantics to match NATS / Kafka / Rabbit.
        self._publish_lock: Optional[asyncio.Lock] = None
        # SUBSCRIBE and UNSUBSCRIBE share the same pubsub connection. If a
        # scope teardown's UNSUBSCRIBE races the next scope's SUBSCRIBE on
        # the same channel, the server can see them in either order — and
        # a late UNSUBSCRIBE means a subsequent PUBLISH is dropped. This
        # lock guarantees pubsub commands hit the server in caller order.
        self._pubsub_lock: Optional[asyncio.Lock] = None

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        if self._allowlist_path is not None:
            load_allowlist(self._allowlist_path).enforce(
                "redis_servers",
                [self._url],
                label="Redis URL",
            )

        try:
            from redis.asyncio import Redis
            from redis.exceptions import RedisError
        except ImportError as e:
            raise TransportError(
                "RedisTransport requires redis — "
                "install with `pip install 'core[redis]'`"
            ) from e

        client = Redis.from_url(
            self._url,
            socket_connect_timeout=self._connect_timeout_s,
        )
        try:
            await asyncio.wait_for(client.ping(), timeout=self._connect_timeout_s)
        except (RedisError, asyncio.TimeoutError, OSError) as e:
            try:
                await client.aclose()
            except Exception:
                pass
            raise TransportError(
                f"could not connect to Redis at {self._url!r}: {e}"
            ) from e

        self._client = client
        # ignore_subscribe_messages=False so the reader sees subscribe-acks
        # and can resolve the matching ``ready`` future — see the module
        # docstring's subscribe-ack note for why this matters.
        self._pubsub = client.pubsub(ignore_subscribe_messages=False)
        self._publish_lock = asyncio.Lock()
        self._pubsub_lock = asyncio.Lock()
        self._reader_task = asyncio.get_running_loop().create_task(
            self._reader_loop()
        )

    async def disconnect(self) -> None:
        if self._client is None:
            return
        # Drain subscribe/publish tasks before tearing the pubsub down.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:
                pass

        try:
            await self._client.aclose()
        except Exception:
            pass

        self._client = None
        self._pubsub = None
        self._reader_task = None
        self._publish_lock = None
        self._pubsub_lock = None
        self._subs.clear()
        self._pending.clear()
        self._pending_ready.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._pubsub is None:
            raise RuntimeError(
                "RedisTransport is not connected; cannot subscribe"
            )
        loop = asyncio.get_running_loop()
        sub = _RedisSub(callback=callback)
        already_subscribed = topic in self._subs
        self._subs.setdefault(topic, []).append(sub)
        lock = self._pubsub_lock
        # Track the ready future so publish() can await it before sending.
        self._pending_ready.add(sub.ready)
        sub.ready.add_done_callback(self._pending_ready.discard)

        async def _do_subscribe() -> None:
            assert lock is not None
            try:
                if already_subscribed:
                    # Reuse the existing server-side subscription — the
                    # reader is already dispatching messages on this
                    # channel to every entry in self._subs[topic].
                    if not sub.ready.done():
                        sub.ready.set_result(None)
                    return
                async with lock:
                    # Issue SUBSCRIBE. The server's subscribe-ack is
                    # read out-of-band by the reader loop, which then
                    # resolves `sub.ready`. Do NOT resolve it here —
                    # that would re-introduce the publish-before-active
                    # race this transport exists to avoid.
                    await self._pubsub.subscribe(topic)
            except Exception as exc:
                if not sub.ready.done():
                    sub.ready.set_exception(exc)

        task = loop.create_task(_do_subscribe())
        self._track(task)

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        entries = self._subs.get(topic)
        if not entries:
            return
        target: _RedisSub | None = None
        for idx, sub in enumerate(entries):
            if sub.callback is callback:
                target = sub
                entries.pop(idx)
                break
        if target is None:
            return
        loop = asyncio.get_running_loop()
        should_unsubscribe = not entries
        if should_unsubscribe:
            self._subs.pop(topic, None)
        lock = self._pubsub_lock

        async def _do_unsubscribe() -> None:
            # Wait for the subscribe-ack before issuing the UNSUBSCRIBE
            # so we don't race the server-side state.
            try:
                await target.ready
            except Exception:
                return
            if should_unsubscribe and self._pubsub is not None:
                assert lock is not None
                async with lock:
                    try:
                        await self._pubsub.unsubscribe(topic)
                    except Exception:
                        pass

        self._track(loop.create_task(_do_unsubscribe()))

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None:
        if self._client is None:
            raise RuntimeError(
                "RedisTransport is not connected; cannot publish"
            )
        loop = asyncio.get_running_loop()
        client = self._client
        lock = self._publish_lock
        # Snapshot subscribe-ack futures now so the publish waits for any
        # in-flight SUBs to be confirmed server-side before the PUBLISH
        # goes out. Without this, the broker sees PUBLISH before it has
        # registered the subscription and returns zero recipients.
        in_flight = [f for f in self._pending_ready if not f.done()]

        async def _do_publish() -> None:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
            # Serialise the actual PUBLISH through the shared lock so the
            # wire order matches the publish() call order. asyncio.Lock is
            # FIFO across waiters, and tasks created in order hit
            # acquire() in order, so this preserves the caller's sequence.
            assert lock is not None
            async with lock:
                try:
                    await client.publish(topic, payload)
                except Exception:
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

    async def _reader_loop(self) -> None:
        assert self._pubsub is not None
        while True:
            try:
                # Low timeout so the loop reacts quickly to new subscriptions
                # and to cancellation. None would block forever on an idle
                # pubsub with zero channels, deadlocking disconnect.
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=False,
                    timeout=0.1,
                )
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                # redis-py raises "pubsub connection not set" when
                # get_message() runs before the first SUBSCRIBE lands.
                # Waiting a beat and retrying keeps the reader alive
                # across the gap between connect() and the first
                # subscribe() call — without this, the very first
                # RuntimeError silently kills the reader task.
                await asyncio.sleep(0.05)
                continue
            except Exception:
                # Any other exception is terminal — the pubsub is in a
                # state we don't understand, better to stop than to spin.
                return
            if message is None:
                continue
            mtype = message.get("type")
            channel = message.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8", errors="replace")
            if mtype == "subscribe":
                # Server confirmed the subscription. Resolve every pending
                # ready future for this channel so publish() can proceed.
                for sub in self._subs.get(channel, ()):
                    if not sub.ready.done():
                        sub.ready.set_result(None)
                continue
            if mtype != "message":
                # unsubscribe, psubscribe, pmessage, etc — not our concern.
                continue
            data = message.get("data")
            if not isinstance(data, (bytes, bytearray)):
                continue
            entries = list(self._subs.get(channel, ()))
            for sub in entries:
                try:
                    sub.callback(channel, bytes(data))
                except Exception:
                    pass

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
