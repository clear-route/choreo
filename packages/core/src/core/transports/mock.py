"""In-memory transport for framework-internal tests and consumer-side fakes.

Subscribers receive published payloads synchronously. No network. Optional
allowlist enforcement — mirrors the pattern real transports implement in
their own connect() methods.

For consumers who just want a harness against nothing real, this is what
they inject. For consumers writing their own fakes, they can follow the
allowlist-enforcement shape here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from ..environment import load_allowlist
from .base import OnSent, TransportCallback, TransportCapabilities
from pathlib import Path


class MockTransport:
    """In-memory transport. Optional allowlist enforcement.

    When `allowlist_path` is provided, `connect()` validates the configured
    `endpoint` against the allowlist's `mock_endpoints` category and raises
    before opening any subscriber state. Exactly mirrors what a real
    transport's enforcement looks like.

    When `allowlist_path` is None, the transport skips enforcement entirely
    (useful for tests that don't care about the guard)."""

    capabilities = TransportCapabilities(
        broadcast_fanout=True,
        loses_messages_without_subscriber=True,
        ordered_per_topic=True,
    )

    def __init__(
        self,
        *,
        allowlist_path: Optional[Path] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        self._allowlist_path = allowlist_path
        self._endpoint = endpoint
        self._connected = False
        self._callbacks: dict[str, list[TransportCallback]] = {}
        self._sent: list[tuple[str, bytes]] = []

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        if self._allowlist_path is not None and self._endpoint is not None:
            load_allowlist(self._allowlist_path).enforce(
                "mock_endpoints",
                [self._endpoint],
                label="mock endpoint",
            )
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        self._callbacks.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        callbacks = self._callbacks.get(topic)
        if callbacks is None:
            return
        try:
            callbacks.remove(callback)
        except ValueError:
            return

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None:
        if not self._connected:
            raise RuntimeError("MockTransport is not connected; cannot publish")
        self._sent.append((topic, payload))
        for cb in list(self._callbacks.get(topic, ())):
            cb(topic, payload)
        # Synchronous dispatch — "on wire" is equivalent to "this call
        # returned", so the post-wire callback fires before we return.
        if on_sent is not None:
            on_sent()

    # ---- diagnostics for tests ------------------------------------------

    def sent(self) -> list[tuple[str, bytes]]:
        return list(self._sent)

    def active_subscription_count(self) -> int:
        return sum(len(cbs) for cbs in self._callbacks.values())

    def clear_subscriptions(self) -> None:
        self._callbacks.clear()

