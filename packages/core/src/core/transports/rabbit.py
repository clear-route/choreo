"""RabbitTransport — a real-network pub/sub backend over aio-pika.

Purpose: exercise the Transport Protocol contract against a live RabbitMQ
broker in the e2e suite.

Routing model:
    The transport declares a single topic exchange (default name
    ``choreo``). Every ``publish(topic, ...)`` call publishes to that
    exchange with the ``topic`` as the routing key. Every ``subscribe(topic,
    callback)`` declares an exclusive auto-delete queue, binds it to the
    exchange with the routing key, and consumes from it. Independent queues
    mean fan-out matches NATS/Mock semantics: subscribing twice delivers
    twice.

Threading / loop model:
    aio-pika is fully asyncio. The Transport Protocol is sync for subscribe /
    unsubscribe / publish; we bridge by scheduling coroutines on the running
    loop. Subscribe tasks are tracked so ``publish()`` can wait for any
    in-flight binds before sending.

Installation:
    pip install 'core[rabbitmq]'   # pulls aio-pika >= 9.0
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..environment import load_allowlist
from .base import OnSent, TransportCallback, TransportCapabilities, TransportError


@dataclass
class _Sub:
    callback: TransportCallback
    task: asyncio.Task[Any]
    # Resolved by the subscribe task once queue.consume() returns the tag.
    ready: asyncio.Future[tuple[Any, Any]] = field(
        default_factory=asyncio.Future
    )


class RabbitTransport:
    """Transport backed by a real RabbitMQ broker via ``aio-pika``.

    Args:
        url: AMQP URL (e.g. ``amqp://guest:guest@localhost:5672/``). Must
            appear in the allowlist's ``amqp_brokers`` category when
            ``allowlist_path`` is supplied.
        allowlist_path: Optional YAML path. When given, the URL is
            validated against the ``amqp_brokers`` category at connect time.
        exchange_name: Topic exchange the transport publishes into and
            binds subscription queues from. Declared durable=False so
            the test fixture doesn't leave state on the broker.
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
        exchange_name: str = "choreo",
        connect_timeout_s: float = 5.0,
    ) -> None:
        if not url:
            raise ValueError("RabbitTransport requires a URL")
        self._url = url
        self._allowlist_path = allowlist_path
        self._exchange_name = exchange_name
        self._connect_timeout_s = connect_timeout_s
        self._connection: Any = None
        self._channel: Any = None
        self._exchange: Any = None
        self._subs: dict[str, list[_Sub]] = {}
        self._pending: set[asyncio.Task[Any]] = set()
        self._pending_subs: set[asyncio.Task[Any]] = set()

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        if self._allowlist_path is not None:
            load_allowlist(self._allowlist_path).enforce(
                "amqp_brokers",
                [self._url],
                label="RabbitMQ URL",
            )

        try:
            import aio_pika
            from aio_pika.exceptions import AMQPConnectionError
        except ImportError as e:
            raise TransportError(
                "RabbitTransport requires aio-pika — "
                "install with `pip install 'core[rabbitmq]'`"
            ) from e

        try:
            self._connection = await asyncio.wait_for(
                aio_pika.connect_robust(self._url),
                timeout=self._connect_timeout_s,
            )
        except (AMQPConnectionError, asyncio.TimeoutError, OSError) as e:
            raise TransportError(
                f"could not connect to RabbitMQ at {self._url!r}: {e}"
            ) from e

        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=False,
            auto_delete=False,
        )

    async def disconnect(self) -> None:
        if self._connection is None:
            return

        # Drain outstanding subscribe/publish tasks so nothing is lost.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        for entries in self._subs.values():
            for sub in entries:
                if sub.ready.done() and not sub.ready.cancelled():
                    try:
                        queue, tag = sub.ready.result()
                        await queue.cancel(tag)
                    except Exception:
                        pass

        try:
            await self._connection.close()
        except Exception:
            pass

        self._connection = None
        self._channel = None
        self._exchange = None
        self._subs.clear()
        self._pending.clear()
        self._pending_subs.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._exchange is None:
            raise RuntimeError(
                "RabbitTransport is not connected; cannot subscribe"
            )
        loop = asyncio.get_running_loop()
        sub = _Sub(callback=callback, task=None)  # type: ignore[arg-type]

        async def _do_subscribe() -> None:
            try:
                queue_name = f"choreo.{uuid.uuid4().hex[:12]}"
                queue = await self._channel.declare_queue(
                    queue_name,
                    exclusive=True,
                    auto_delete=True,
                    durable=False,
                )
                await queue.bind(self._exchange, routing_key=topic)

                async def handler(message: Any) -> None:
                    async with message.process(ignore_processed=True):
                        try:
                            callback(topic, bytes(message.body))
                        except Exception:
                            pass

                consumer_tag = await queue.consume(handler)
            except Exception as exc:
                if not sub.ready.done():
                    sub.ready.set_exception(exc)
                return
            if not sub.ready.done():
                sub.ready.set_result((queue, consumer_tag))

        task = loop.create_task(_do_subscribe())
        sub.task = task
        self._subs.setdefault(topic, []).append(sub)
        self._track(task)
        self._pending_subs.add(task)
        task.add_done_callback(self._pending_subs.discard)

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        entries = self._subs.get(topic)
        if not entries:
            return
        target: _Sub | None = None
        for idx, sub in enumerate(entries):
            if sub.callback is callback:
                target = sub
                entries.pop(idx)
                break
        if target is None:
            return
        if not entries:
            self._subs.pop(topic, None)

        loop = asyncio.get_running_loop()

        async def _do_unsubscribe() -> None:
            try:
                queue, consumer_tag = await target.ready
            except Exception:
                return
            try:
                await queue.cancel(consumer_tag)
            except Exception:
                pass
            try:
                await queue.delete(if_unused=False, if_empty=False)
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
        if self._exchange is None:
            raise RuntimeError(
                "RabbitTransport is not connected; cannot publish"
            )

        try:
            import aio_pika
        except ImportError as e:
            raise TransportError(
                "RabbitTransport requires aio-pika"
            ) from e

        loop = asyncio.get_running_loop()
        exchange = self._exchange
        in_flight = [t for t in self._pending_subs if not t.done()]

        async def _do_publish() -> None:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
            try:
                await exchange.publish(
                    aio_pika.Message(body=payload),
                    routing_key=topic,
                )
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

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
