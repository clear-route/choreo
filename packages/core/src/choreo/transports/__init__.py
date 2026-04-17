"""Transport Protocol and built-in implementations.

To add a new backend, put a module alongside `mock.py` that implements the
`Transport` Protocol and optionally extends the `Allowlist`-based enforcement
pattern shown in `MockTransport`.
"""

from __future__ import annotations

from .base import Transport, TransportCallback, TransportCapabilities, TransportError
from .mock import MockTransport

__all__ = [
    "MockTransport",
    "Transport",
    "TransportCallback",
    "TransportCapabilities",
    "TransportError",
]


_LAZY = {
    "NatsTransport": ("nats", "NatsTransport"),
    "KafkaTransport": ("kafka", "KafkaTransport"),
    "RabbitTransport": ("rabbit", "RabbitTransport"),
    "RedisTransport": ("redis", "RedisTransport"),
}


def __getattr__(name: str):
    # Lazy-import each backend so the matching extra is only required when
    # the caller actually wants that transport.
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    return getattr(module, attr)
