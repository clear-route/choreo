"""In-memory transport for framework-internal tests and consumer-side fakes.

Subscribers receive published payloads synchronously. No network. Optional
allowlist enforcement — mirrors the pattern real transports implement in
their own connect() methods.

For consumers who just want a harness against nothing real, this is what
they inject. For consumers writing their own fakes, they can follow the
allowlist-enforcement shape here.

When ``auth=`` is supplied, MockTransport validates the descriptor shape
(same variant-allowlist and ``_consumed`` checks as real transports),
clears the descriptor, and emits a ``mock_transport_ignored_auth`` WARNING
at most once per instance.  This ensures "write once, swap for a real
transport later" is genuinely safe — a wrong-variant descriptor fails
against Mock, not only against the real broker.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..environment import load_allowlist
from ._auth import AuthParam, _clear_auth_fields, _resolve_auth
from .base import OnSent, TransportCallback, TransportCapabilities

logger = logging.getLogger(__name__)


class MockTransport:
    """In-memory transport. Optional allowlist enforcement.

    When `allowlist_path` is provided, `connect()` validates the configured
    `endpoint` against the allowlist's `mock_endpoints` category and raises
    before opening any subscriber state. Exactly mirrors what a real
    transport's enforcement looks like.

    When `allowlist_path` is None, the transport skips enforcement entirely
    (useful for tests that don't care about the guard).

    When ``auth`` is provided, the descriptor is validated and cleared on
    ``connect()`` for parity with real transports, but credentials are not
    used (Mock has no broker to authenticate against).
    """

    capabilities = TransportCapabilities(
        broadcast_fanout=True,
        loses_messages_without_subscriber=True,
        ordered_per_topic=True,
    )

    def __init__(
        self,
        *,
        allowlist_path: Path | None = None,
        endpoint: str | None = None,
        auth: AuthParam = None,
    ) -> None:
        self._allowlist_path = allowlist_path
        self._endpoint = endpoint
        self._auth = auth
        self._has_connected = False
        self._auth_warned = False
        self._connected = False
        self._callbacks: dict[str, list[TransportCallback]] = {}
        self._sent: list[tuple[str, bytes]] = []

    def __reduce__(self) -> None:  # type: ignore[override]
        raise TypeError("MockTransport does not support pickling")

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        # --- resolve and validate auth (ADR-0020 §Implementation step 5) ---
        raw_auth = self._auth
        self._auth = None
        descriptor = await _resolve_auth(raw_auth, "mock")

        if descriptor is not None:
            # Clear BEFORE building the warning event so the payload cannot
            # reference any secret-bearing field even by accident.
            variant_tag = type(descriptor).__qualname__
            _clear_auth_fields(descriptor)

            if not self._auth_warned:
                self._auth_warned = True
                logger.warning(
                    "mock_transport_ignored_auth",
                    extra={"auth_variant": variant_tag},
                )

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
        on_sent: OnSent | None = None,
    ) -> None:
        if not self._connected:
            raise RuntimeError("MockTransport is not connected; cannot publish")
        self._sent.append((topic, payload))
        # Fire `on_sent` BEFORE subscriber dispatch so the post-wire
        # marker records before any nested publish triggered by a
        # subscriber. Matches real-broker semantics (the broker confirms
        # the send, then propagates to subscribers — physically separate
        # channels, the confirmation arrives independently of delivery).
        # Important for the PRD-013 timeline: a synchronous reply chain
        # would otherwise record REPLIED (the inner publish's on_sent)
        # before PUBLISHED (the outer publish's on_sent), inverting the
        # causal order in the rendered waterfall.
        if on_sent is not None:
            on_sent()
        # Isolate subscriber exceptions: one callback raising MUST NOT
        # abort fan-out to siblings AND MUST NOT propagate back to the
        # publisher. Real brokers behave this way (a misbehaving consumer
        # does not fail the publish); MockTransport now matches.
        # Specifically: if a Stage reply chain's response-side subscriber
        # raises, the outer reply chain's `try` block would see the
        # exception and record REPLY_FAILED for an outbound that already
        # recorded REPLIED — a double-event for one publish.
        for cb in list(self._callbacks.get(topic, ())):
            try:
                cb(topic, payload)
            except Exception:
                logging.getLogger(__name__).warning(
                    "mock_transport_subscriber_failed",
                    extra={"topic": topic},
                    exc_info=True,
                )

    # ---- diagnostics for tests ------------------------------------------

    def sent(self) -> list[tuple[str, bytes]]:
        return list(self._sent)

    def active_subscription_count(self) -> int:
        return sum(len(cbs) for cbs in self._callbacks.values())

    def clear_subscriptions(self) -> None:
        self._callbacks.clear()
