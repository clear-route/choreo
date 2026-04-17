"""NatsTransport — a real-network pub/sub backend over nats-py.

Purpose: exercise the Transport Protocol contract against a live broker in
the e2e suite. NATS is cheap, async-native, and dot-separated subjects map
1:1 to harness topics. It is NOT the production wire (that is LBM) — it is
a probe that the contract holds outside of MockTransport's happy-path
in-memory shortcut.

Threading / loop model:
    nats-py is fully asyncio. The harness's Transport Protocol is sync for
    subscribe / unsubscribe / publish. We bridge by scheduling coroutines on
    the running loop via `loop.create_task(...)`, tracking them in a pending
    set that `disconnect()` drains. Publish waits for any in-flight subscribe
    tasks before firing so a user can write the classic
    `subscribe(...); publish(...); await asyncio.sleep(...)` pattern without
    a race.

Installation:
    pip install 'core[nats]'   # pulls nats-py >= 2.7

Not installed by default — the import error at connect() time is the signal
to install the extra.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from ..environment import load_allowlist
from .base import OnSent, TransportCallback, TransportCapabilities, TransportError


class NatsTransport:
    """Transport backed by a real NATS broker.

    Args:
        servers: NATS server URLs (e.g. ``["nats://localhost:4222"]``). All
            must appear in the allowlist's ``nats_servers`` category when
            ``allowlist_path`` is supplied.
        allowlist_path: Optional YAML path. When given, every server URL is
            validated against the ``nats_servers`` category at connect time.
            When ``None``, no guard runs — intended for cases where the
            caller has already validated upstream.
        name: Client name reported to the server (useful in ``nats-top``).
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
        servers: list[str],
        allowlist_path: Optional[Path] = None,
        name: str = "choreo",
        connect_timeout_s: float = 5.0,
    ) -> None:
        if not servers:
            raise ValueError("NatsTransport requires at least one server URL")
        self._servers = list(servers)
        self._allowlist_path = allowlist_path
        self._name = name
        self._connect_timeout_s = connect_timeout_s
        self._nc: Any = None
        # topic -> list of (callback, subscribe_task) pairs. A list (not a
        # dict keyed by callback) so repeated subscribes of the same callback
        # fan out independently, matching MockTransport's semantics.
        self._subs: dict[str, list[tuple[TransportCallback, asyncio.Task[Any]]]] = {}
        # All in-flight tasks, tracked for disconnect drain so nothing is
        # lost when the scope tears down.
        self._pending: set[asyncio.Task[Any]] = set()
        # Subscribe tasks only, tracked separately so `publish()` awaits
        # just the subs (ensuring the SUB is on the wire before its
        # matching PUB). Awaiting unrelated prior publishes serialises
        # the publish stream, turning an O(1) publish into O(N). This
        # set stays disjoint-ish with publishes; entries are removed by
        # `add_done_callback` when the subscribe completes.
        self._pending_subs: set[asyncio.Task[Any]] = set()

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        if self._allowlist_path is not None:
            load_allowlist(self._allowlist_path).enforce(
                "nats_servers",
                self._servers,
                label="NATS server",
            )

        try:
            import nats
            from nats.errors import NoServersError, TimeoutError as NatsTimeoutError
        except ImportError as e:
            raise TransportError(
                "NatsTransport requires nats-py — install with `pip install 'core[nats]'`"
            ) from e

        try:
            # connect_timeout_s is authoritative: wrap nats.connect in wait_for
            # so the caller's budget bounds the total connect time. nats-py's
            # own connect_timeout kwarg is per-attempt, and its initial connect
            # loop retries internally (max_reconnect_attempts defaults to 60),
            # so without this wrapper an unreachable server takes ~120s to fail
            # regardless of connect_timeout. max_reconnect_attempts=0 disables
            # the retry loop on the nats-py side as belt-and-braces.
            self._nc = await asyncio.wait_for(
                nats.connect(
                    servers=self._servers,
                    name=self._name,
                    connect_timeout=self._connect_timeout_s,
                    allow_reconnect=False,
                    max_reconnect_attempts=0,
                ),
                timeout=self._connect_timeout_s,
            )
        except (NoServersError, NatsTimeoutError, asyncio.TimeoutError) as e:
            raise TransportError(
                f"could not connect to NATS at {self._servers!r}: {e}"
            ) from e

    async def disconnect(self) -> None:
        if self._nc is None:
            return
        # Drain any work we scheduled so no subscribe/publish is lost mid-close.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        try:
            await self._nc.drain()
        except Exception:
            # drain() can raise if already drained / closed; fall back to close
            try:
                await self._nc.close()
            except Exception:
                pass
        self._nc = None
        self._subs.clear()
        self._pending.clear()
        self._pending_subs.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._nc is None:
            raise RuntimeError("NatsTransport is not connected; cannot subscribe")
        loop = asyncio.get_running_loop()

        async def nats_handler(msg: Any) -> None:
            # Exceptions inside a user callback must not kill the NATS
            # reader task — one broken test should not cascade.
            try:
                callback(msg.subject, bytes(msg.data))
            except Exception:
                pass

        async def _do_subscribe() -> Any:
            return await self._nc.subscribe(topic, cb=nats_handler)

        task = loop.create_task(_do_subscribe())
        self._track(task)
        # Also add to the subs-only set so publishes can await SUB tasks
        # without waiting for unrelated prior publishes.
        self._pending_subs.add(task)
        task.add_done_callback(self._pending_subs.discard)
        self._subs.setdefault(topic, []).append((callback, task))

    def unsubscribe(self, topic: str, callback: TransportCallback) -> None:
        if self._nc is None:
            return
        entries = self._subs.get(topic)
        if not entries:
            return
        sub_task: asyncio.Task[Any] | None = None
        for idx, (cb, task) in enumerate(entries):
            if cb is callback:
                sub_task = task
                entries.pop(idx)
                break
        if sub_task is None:
            return
        if not entries:
            self._subs.pop(topic, None)

        loop = asyncio.get_running_loop()

        async def _do_unsubscribe() -> None:
            try:
                sub = await sub_task
                await sub.unsubscribe()
            except Exception:
                # Either the subscribe never completed, or the sub was already
                # torn down by a drain. Either way, nothing more to do.
                pass

        self._track(loop.create_task(_do_unsubscribe()))

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Optional[OnSent] = None,
    ) -> None:
        if self._nc is None:
            raise RuntimeError("NatsTransport is not connected; cannot publish")
        loop = asyncio.get_running_loop()
        # Snapshot pending SUBSCRIBE tasks only — a publish needs its
        # SUBs in place before the wire send, but it does NOT need to
        # wait for prior publishes. Awaiting `self._pending` wholesale
        # serialises publishes behind each other, which turns an O(1)
        # publish into O(N) under burst load (each new publish waits
        # for every previous one). The original comment below still
        # applies to SUBs.
        #
        # "Snapshot pending subscribes BEFORE scheduling the publish so
        #  the publish coroutine can await them — otherwise the publish
        #  might land on the wire before the SUB does (TCP preserves
        #  order once both bytes are buffered, but the subscribe task
        #  has to run first to buffer anything)."
        in_flight = [t for t in self._pending_subs if not t.done()]

        async def _do_publish() -> None:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
            await self._nc.publish(topic, payload)
            # Post-wire: fire the on_sent hook so the caller can timestamp
            # "message sent" accurately. Without this hook, callers that
            # time the publish by reading `loop.time()` immediately after
            # `publish()` returns are capturing "task scheduled", which on
            # a loaded loop or behind `in_flight` blockers can be several
            # ms earlier than the true wire send.
            if on_sent is not None:
                on_sent()

        self._track(loop.create_task(_do_publish()))

    # ---- diagnostics -----------------------------------------------------

    def active_subscription_count(self) -> int:
        return sum(len(m) for m in self._subs.values())

    def clear_subscriptions(self) -> None:
        self._subs.clear()

    # ---- internals -------------------------------------------------------

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
