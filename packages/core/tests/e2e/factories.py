"""Per-transport factories for the shared contract suite.

Each factory knows:
  - its SDK import and module-level availability probe
  - how to build a fresh `Transport` instance against the local compose stack
  - how to translate a logical topic name into a form the backend accepts
  - which capabilities the backend declares

`tests/e2e/conftest.py` parametrises a `transport_factory` fixture over the
factories registered here; every test under `tests/e2e/contract/` runs once
per reachable + installed backend, automatically skipped when either is
missing.

The probes are one-shot per session and cache their result on the factory
instance. A skip surfaces as a pytest.skip() inside the fixture so the
contract tests never masquerade as real failures when the dependency is
simply absent.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from choreo.transports.base import TransportCapabilities

# -- URL / endpoint resolution from env --------------------------------------


NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
AMQP_URL = os.environ.get("AMQP_URL", "amqp://guest:guest@localhost:5672/")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


# -- Factory base -----------------------------------------------------------


@dataclass
class TransportFactory:
    """Contract the parametrised fixture calls into."""

    name: str
    capabilities: TransportCapabilities
    # Populated lazily the first time probe() runs.
    _probed: bool = field(default=False, init=False)
    _skip_reason: str | None = field(default=None, init=False)

    async def probe_or_skip(self) -> None:
        """Skip this parameter if the SDK or broker is unavailable. One-shot."""
        if self._probed:
            if self._skip_reason is not None:
                pytest.skip(self._skip_reason)
            return
        self._probed = True
        reason = await self._probe()
        if reason is not None:
            self._skip_reason = reason
            pytest.skip(reason)

    async def _probe(self) -> str | None:  # pragma: no cover - override
        raise NotImplementedError

    def build(self, allowlist_path: Path) -> Any:  # pragma: no cover - override
        raise NotImplementedError

    def topic(self, prefix: str) -> str:
        """Every test gets a unique topic so concurrent runs don't collide."""
        return f"e2e.contract.{self.name}.{prefix}.{uuid.uuid4().hex[:8]}"


# -- NATS -------------------------------------------------------------------


class NatsFactory(TransportFactory):
    def __init__(self) -> None:
        super().__init__(
            name="nats",
            capabilities=TransportCapabilities(
                broadcast_fanout=True,
                loses_messages_without_subscriber=True,
                ordered_per_topic=True,
            ),
        )

    async def _probe(self) -> str | None:
        try:
            import nats
            from nats.errors import NoServersError
            from nats.errors import TimeoutError as NatsTimeoutError
        except ImportError:
            return (
                "nats-py is not installed — run `pip install 'choreo[nats]'` to "
                "enable the NATS contract tests"
            )
        try:
            nc = await nats.connect(
                servers=[NATS_URL],
                connect_timeout=2.0,
                allow_reconnect=False,
            )
        except (NoServersError, NatsTimeoutError) as e:
            return (
                f"no NATS broker at {NATS_URL}: {e} — "
                f"bring one up with `docker compose -f docker/compose.e2e.yaml "
                f"--profile nats up -d`"
            )
        await nc.drain()
        return None

    def build(self, allowlist_path: Path) -> Any:
        from choreo.transports import NatsTransport

        return NatsTransport(servers=[NATS_URL], allowlist_path=allowlist_path)


# -- Kafka ------------------------------------------------------------------


class KafkaFactory(TransportFactory):
    def __init__(self) -> None:
        super().__init__(
            name="kafka",
            capabilities=TransportCapabilities(
                broadcast_fanout=True,
                loses_messages_without_subscriber=True,
                ordered_per_topic=True,
            ),
        )

    async def _probe(self) -> str | None:
        try:
            from aiokafka import AIOKafkaProducer
            from aiokafka.errors import KafkaConnectionError, KafkaError
        except ImportError:
            return (
                "aiokafka is not installed — run `pip install 'choreo[kafka]'` "
                "to enable the Kafka contract tests"
            )
        producer = AIOKafkaProducer(
            bootstrap_servers=[KAFKA_BOOTSTRAP],
            request_timeout_ms=3000,
        )
        try:
            await producer.start()
        except (KafkaConnectionError, KafkaError, OSError) as e:
            try:
                await producer.stop()
            except Exception:
                pass
            return (
                f"no Kafka broker at {KAFKA_BOOTSTRAP}: {e} — "
                f"bring one up with `docker compose -f docker/compose.e2e.yaml "
                f"--profile kafka up -d`"
            )
        await producer.stop()
        return None

    def build(self, allowlist_path: Path) -> Any:
        from choreo.transports import KafkaTransport

        return KafkaTransport(
            bootstrap_servers=[KAFKA_BOOTSTRAP],
            allowlist_path=allowlist_path,
        )

    def topic(self, prefix: str) -> str:
        # Kafka permits dots but discourages them; underscores are safer.
        return f"e2e_contract_{self.name}_{prefix}_{uuid.uuid4().hex[:8]}"


# -- RabbitMQ ---------------------------------------------------------------


class RabbitFactory(TransportFactory):
    def __init__(self) -> None:
        super().__init__(
            name="rabbitmq",
            capabilities=TransportCapabilities(
                broadcast_fanout=True,
                loses_messages_without_subscriber=True,
                ordered_per_topic=True,
            ),
        )

    async def _probe(self) -> str | None:
        try:
            import aio_pika
            from aio_pika.exceptions import AMQPConnectionError
        except ImportError:
            return (
                "aio-pika is not installed — run `pip install 'choreo[rabbitmq]'` "
                "to enable the RabbitMQ contract tests"
            )
        try:
            connection = await aio_pika.connect_robust(AMQP_URL, timeout=2.0)
        except (AMQPConnectionError, OSError, Exception) as e:
            return (
                f"no RabbitMQ broker at {AMQP_URL}: {e} — "
                f"bring one up with `docker compose -f docker/compose.e2e.yaml "
                f"--profile rabbitmq up -d`"
            )
        await connection.close()
        return None

    def build(self, allowlist_path: Path) -> Any:
        from choreo.transports import RabbitTransport

        return RabbitTransport(url=AMQP_URL, allowlist_path=allowlist_path)


# -- Redis ------------------------------------------------------------------


class RedisFactory(TransportFactory):
    def __init__(self) -> None:
        super().__init__(
            name="redis",
            capabilities=TransportCapabilities(
                broadcast_fanout=True,
                loses_messages_without_subscriber=True,
                ordered_per_topic=True,
            ),
        )

    async def _probe(self) -> str | None:
        try:
            from redis.asyncio import Redis
            from redis.exceptions import RedisError
        except ImportError:
            return (
                "redis is not installed — run `pip install 'choreo[redis]'` to "
                "enable the Redis contract tests"
            )
        client = Redis.from_url(REDIS_URL, socket_connect_timeout=2.0)
        try:
            await client.ping()
        except (RedisError, OSError) as e:
            try:
                await client.aclose()
            except Exception:
                pass
            return (
                f"no Redis broker at {REDIS_URL}: {e} — "
                f"bring one up with `docker compose -f docker/compose.e2e.yaml "
                f"--profile redis up -d`"
            )
        await client.aclose()
        return None

    def build(self, allowlist_path: Path) -> Any:
        from choreo.transports import RedisTransport

        return RedisTransport(url=REDIS_URL, allowlist_path=allowlist_path)


# -- Registry ---------------------------------------------------------------


ALL_FACTORIES: tuple[TransportFactory, ...] = (
    NatsFactory(),
    KafkaFactory(),
    RabbitFactory(),
    RedisFactory(),
)
