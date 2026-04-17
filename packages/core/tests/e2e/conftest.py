"""Fixtures for the e2e suite.

Two layers of e2e tests coexist:

1. **NATS-specific edge-case tests** — the existing suite that exercises
   behaviours only a real wire can surface (callback exceptions on the
   reader task, UNSUB reaching the server, reconnect cycles). These keep
   the ``nats_url`` / ``_nats_available`` fixtures they've always used.

2. **Transport contract tests** under ``tests/e2e/contract/`` — parametrised
   over every installed + reachable transport via the ``transport_factory``
   fixture. One test, N backends; each parameter skips if the SDK or broker
   isn't there.

Bring the dependencies up with:

    docker compose -f docker/compose.e2e.yaml --profile all up -d
    pytest -m e2e

or a single transport:

    docker compose -f docker/compose.e2e.yaml --profile kafka up -d
    pytest -m e2e -k "kafka"
"""
from __future__ import annotations

import pytest

from .factories import (
    ALL_FACTORIES,
    AMQP_URL,
    KAFKA_BOOTSTRAP,
    NATS_URL,
    REDIS_URL,
    KafkaFactory,
    RabbitFactory,
    RedisFactory,
    TransportFactory,
)


@pytest.fixture(scope="session")
def nats_url() -> str:
    return NATS_URL


@pytest.fixture(scope="session")
async def _nats_available(nats_url: str) -> bool:
    """Probe the NATS broker once per session. Skip the existing NATS-specific
    tests if it isn't there so the suite never masquerades as 'real' failures
    when the dependency is simply absent."""
    try:
        import nats
        from nats.errors import NoServersError, TimeoutError as NatsTimeoutError
    except ImportError:
        pytest.skip(
            "nats-py is not installed — run `pip install 'core[nats]'` to "
            "enable the e2e suite",
            allow_module_level=False,
        )
    try:
        nc = await nats.connect(
            servers=[nats_url],
            connect_timeout=2.0,
            allow_reconnect=False,
        )
    except (NoServersError, NatsTimeoutError) as e:
        pytest.skip(
            f"no NATS broker at {nats_url}: {e} — "
            f"bring one up with `docker compose -f docker/compose.e2e.yaml "
            f"--profile nats up -d`"
        )
    await nc.drain()
    return True


# -- Parametrised transport factory for the contract suite ------------------


@pytest.fixture(
    params=ALL_FACTORIES,
    ids=[factory.name for factory in ALL_FACTORIES],
)
async def transport_factory(request: pytest.FixtureRequest) -> TransportFactory:
    """Yield one ``TransportFactory`` per reachable + installed transport.

    Every contract test under ``tests/e2e/contract/`` picks this fixture up and
    runs once per parameter; unreachable / uninstalled backends skip cleanly
    inside ``probe_or_skip()`` rather than fail.
    """
    factory: TransportFactory = request.param
    await factory.probe_or_skip()
    return factory


# -- Per-transport probe fixtures for the backend-specific edge-case suites --
#
# Each edge-case file under tests/e2e/ (e.g. test_e2e_kafka_edge_cases.py)
# exercises behaviours that aren't portable to the shared contract suite —
# transport-specific quirks like Kafka consumer-group broadcast, Rabbit
# queue auto-delete, Redis publish-with-no-subscriber. They take the
# matching fixture below to skip cleanly when the SDK or broker is absent.


@pytest.fixture(scope="session")
def kafka_bootstrap() -> str:
    return KAFKA_BOOTSTRAP


@pytest.fixture(scope="session")
async def _kafka_available() -> bool:
    factory = KafkaFactory()
    await factory.probe_or_skip()
    return True


@pytest.fixture(scope="session")
def amqp_url() -> str:
    return AMQP_URL


@pytest.fixture(scope="session")
async def _rabbit_available() -> bool:
    factory = RabbitFactory()
    await factory.probe_or_skip()
    return True


@pytest.fixture(scope="session")
def redis_url() -> str:
    return REDIS_URL


@pytest.fixture(scope="session")
async def _redis_available() -> bool:
    factory = RedisFactory()
    await factory.probe_or_skip()
    return True
