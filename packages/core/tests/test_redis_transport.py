"""Unit tests for RedisTransport — paths exercisable without a broker.

Round-trip behaviour is covered by the e2e contract suite against a real
Redis broker. These tests only cover:

  - constructor validation
  - allowlist enforcement at connect time
  - a clear TransportError when the optional redis dependency is absent
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.environment import HostNotInAllowlist
from core.transports import RedisTransport, TransportError


def test_a_redis_transport_constructed_with_no_url_should_raise() -> None:
    with pytest.raises(ValueError):
        RedisTransport(url="")


async def test_a_redis_transport_should_refuse_to_connect_to_a_server_outside_the_allowlist(
    allowlist_yaml_path: Path,
) -> None:
    transport = RedisTransport(
        url="redis://prod.internal:6379/0",
        allowlist_path=allowlist_yaml_path,
    )
    with pytest.raises(HostNotInAllowlist) as exc:
        await transport.connect()
    assert "prod.internal" in str(exc.value)


async def test_a_redis_transport_should_raise_transport_error_when_redis_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    transport = RedisTransport(url="redis://localhost:6379/0")
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert "redis" in str(exc.value)
    assert "core[redis]" in str(exc.value)
