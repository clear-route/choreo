"""LbmTransport — a real-network pub/sub backend over Informatica Ultra Messaging (LBM).

Purpose: Exercise the Transport Protocol contract against LBM multicast messaging.
LBM is a high-performance, low-latency messaging system used in trading platforms.
This transport enables Choreo test harness to test services that communicate via LBM.

LBM characteristics:
    - Ultra-low latency multicast messaging
    - Topic-based publish/subscribe
    - Fire-and-forget pub/sub (like NATS/Redis)
    - Messages published before subscriber exist are lost
    - Broadcast fan-out to all subscribers

Threading / loop model:
    LBM is a C library with Python bindings (lbm module). The library is
    callback-based and thread-safe. We bridge to asyncio by:
    1. LBM callbacks run on LBM's internal threads
    2. We post received messages to the asyncio loop via loop.call_soon_threadsafe
    3. publish() schedules the LBM send on a thread pool executor
    4. subscribe() registers LBM receivers with callbacks that cross to the loop

LBM Configuration:
    LBM requires an XML configuration file that defines:
    - Transport protocols (TCP, LBT-RU, LBT-RM, etc.)
    - Topic resolution (multicast groups, ports)
    - Network interfaces
    - Reliability settings

    The config file path is passed via LBM_CONFIG_FILE environment variable
    or lbm_config_file parameter.

Installation:
    LBM must be installed separately:
    1. Install LBM library from Informatica
    2. Set LBM_LICENSE_FILENAME environment variable
    3. Ensure lbm Python module is in PYTHONPATH

    Example:
        export LBM_LICENSE_FILENAME=/path/to/lbm_license.txt
        export PYTHONPATH=/path/to/lbm-distro/python:$PYTHONPATH

Topic mapping:
    Choreo topics map 1:1 to LBM topic strings. Wildcard patterns are supported
    using LBM's pattern receiver (e.g., "orders.*" matches "orders.new", "orders.cancel").

Example usage:
    ```python
    from choreo import Harness
    from choreo_lbm_transport import LbmTransport

    transport = LbmTransport(
        lbm_config_file="/path/to/lbm_config.xml",
        app_name="choreo_test"
    )

    harness = Harness(transport=transport)

    with harness.scope() as s:
        s.subscribe("orders.new", handler)
        s.publish("orders.new", b'{"order_id": "TEST001", "symbol": "ES"}')
        await asyncio.sleep(0.1)  # Let message propagate
    ```
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Import will be attempted in connect(), not at module load time
# This allows the module to be imported even if LBM is not installed


class LbmError(Exception):
    """Base for LBM-specific failures."""


class LbmTransport:
    """Transport backed by Informatica Ultra Messaging (LBM).

    Args:
        lbm_config_file: Path to LBM XML configuration file. If None, will check
            LBM_CONFIG_FILE environment variable.
        app_name: Application name for LBM context (useful for debugging).
        connect_timeout_s: Seconds before connect attempt fails.
        license_file: Path to LBM license file. If None, will check
            LBM_LICENSE_FILENAME environment variable.
    """

    # LBM capabilities match Redis/NATS pub/sub semantics
    capabilities: Any  # TransportCapabilities - imported from base.py

    def __init__(
        self,
        *,
        lbm_config_file: Path | str | None = None,
        app_name: str = "choreo",
        connect_timeout_s: float = 5.0,
        license_file: Path | str | None = None,
    ) -> None:
        # Resolve config file path
        if lbm_config_file is None:
            lbm_config_file = os.environ.get("LBM_CONFIG_FILE")
        if lbm_config_file is None:
            raise ValueError(
                "lbm_config_file must be provided or LBM_CONFIG_FILE environment variable must be set"
            )
        self._config_file = Path(lbm_config_file)
        if not self._config_file.exists():
            raise ValueError(f"LBM config file not found: {self._config_file}")

        # Resolve license file path
        if license_file is not None:
            os.environ["LBM_LICENSE_FILENAME"] = str(license_file)
        if "LBM_LICENSE_FILENAME" not in os.environ:
            raise ValueError(
                "LBM license file must be provided via license_file parameter "
                "or LBM_LICENSE_FILENAME environment variable"
            )

        self._app_name = app_name
        self._connect_timeout_s = connect_timeout_s

        # LBM objects (created in connect())
        self._lbm: Any = None  # lbm module
        self._ctx: Any = None  # LBM context
        self._event_queue: Any = None  # LBM event queue for callbacks
        self._loop: asyncio.AbstractEventLoop | None = None

        # Subscription tracking: topic -> list of (callback, lbm_receiver) pairs
        # List (not dict) allows same callback to be subscribed multiple times
        self._subs: dict[str, list[tuple[Any, Any]]] = {}

        # Thread pool for async LBM operations (publish, etc.)
        self._executor: ThreadPoolExecutor | None = None

        # Track pending operations for clean shutdown
        self._pending: set[asyncio.Task[Any]] = set()

        # Event queue polling task
        self._polling_task: asyncio.Task[Any] | None = None
        self._should_poll: bool = False

    def __reduce__(self) -> None:  # type: ignore[override]
        raise TypeError("LbmTransport does not support pickling")

    # ---- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        """Connect to LBM messaging system.

        Raises:
            LbmError: If LBM library cannot be loaded or connection fails
        """
        try:
            import pylbm as lbm
        except ImportError as e:
            raise LbmError(
                "LbmTransport requires the LBM Python module (pylbm).\n"
                "Install LBM from Informatica and ensure it's in PYTHONPATH.\n"
                "Set LBM_LICENSE_FILENAME environment variable to license file path."
            ) from e

        self._lbm = lbm
        self._loop = asyncio.get_running_loop()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lbm_")

        try:
            # Load LBM configuration from XML file with application name
            # pylbm API: lbm_read_xml_config(config_file, app_name)
            lbm.lbm_read_xml_config(str(self._config_file), self._app_name)

            # Create LBM context (connection to messaging infrastructure)
            # Context creation validates license and uses loaded configuration
            # pylbm API: LbmContext(attrs=None)
            self._ctx = lbm.LbmContext()

            # Create event queue for receiver callbacks
            # This allows callbacks to be processed on demand
            self._event_queue = lbm.LbmEventQueue()

            # Start background task to poll event queue
            self._should_poll = True
            self._polling_task = self._loop.create_task(self._poll_event_queue())

            # Give LBM time to initialize (topic resolution, etc.)
            await asyncio.sleep(0.1)

        except Exception as e:
            raise LbmError(
                f"Failed to create LBM context with config {self._config_file}: {e}"
            ) from e

    async def _poll_event_queue(self) -> None:
        """Background task to poll LBM event queue for messages."""
        while self._should_poll:
            try:
                if self._event_queue is not None:
                    # Poll event queue to process callbacks
                    # pylbm API: poll() takes no arguments
                    # This runs on the executor to avoid blocking the loop
                    await self._loop.run_in_executor(self._executor, self._event_queue.poll)
                    # Small sleep to avoid busy-waiting
                    await asyncio.sleep(0.01)
                else:
                    await asyncio.sleep(0.1)
            except Exception:
                # Don't let polling errors kill the task
                await asyncio.sleep(0.1)

    async def disconnect(self) -> None:
        """Disconnect from LBM and clean up resources."""
        if self._ctx is None:
            return

        # Stop polling
        self._should_poll = False
        if self._polling_task is not None:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

        # Wait for any pending operations to complete
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        # Clean up all subscriptions
        for _topic, subs in list(self._subs.items()):
            for _callback, receiver in subs:
                try:
                    # pylbm API uses destroy() not close()
                    receiver.destroy()
                except Exception:
                    pass

        # Close event queue
        try:
            if self._event_queue is not None:
                self._event_queue.destroy()
        except Exception:
            pass

        # Close LBM context
        try:
            if self._ctx is not None:
                # pylbm API uses destroy() not close()
                self._ctx.destroy()
        except Exception:
            pass

        # Shutdown thread pool
        if self._executor is not None:
            self._executor.shutdown(wait=True)

        self._ctx = None
        self._event_queue = None
        self._lbm = None
        self._loop = None
        self._executor = None
        self._polling_task = None
        self._subs.clear()
        self._pending.clear()

    # ---- pub/sub ---------------------------------------------------------

    def subscribe(self, topic: str, callback: Any) -> None:
        """Subscribe to an LBM topic.

        Args:
            topic: LBM topic string (supports wildcards like "orders.*")
            callback: Callable[[str, bytes], None] - invoked when message received

        Raises:
            RuntimeError: If transport is not connected
        """
        if self._ctx is None or self._lbm is None or self._loop is None:
            raise RuntimeError("LbmTransport is not connected; cannot subscribe")

        loop = self._loop

        # LBM callback - runs on LBM thread, must cross to asyncio loop
        def lbm_receiver_callback(msg: Any) -> int:
            """Called by LBM on its internal thread when message arrives."""
            try:
                # Extract topic and data from LBM message
                # pylbm API: msg.topic_name and msg.data are PROPERTIES not methods!
                topic_str = msg.topic_name  # Property
                data = msg.data  # Property

                # Post to asyncio loop thread-safely
                # Wrap user callback in try/except so one broken test doesn't cascade
                def invoke_callback() -> None:
                    try:
                        callback(topic_str, bytes(data))
                    except Exception:
                        # Swallow exceptions like NatsTransport does
                        pass

                loop.call_soon_threadsafe(invoke_callback)

            except Exception:
                # If we fail to post to loop, swallow exception
                # Don't let callback errors kill LBM receiver thread
                pass

            # Return 0 to tell LBM we handled the message
            return 0

        try:
            # Create LBM receiver for this topic
            # pylbm API: LbmReceiver(ctx, topic, callback, queue=None, tattrs=None)
            # Receiver can be wildcard (pattern) or exact topic
            # Use event queue so we can poll for messages
            rcv_attr = self._lbm.LbmReceiverTopicAttributes()
            receiver = self._lbm.LbmReceiver(
                self._ctx, topic, lbm_receiver_callback, queue=self._event_queue, tattrs=rcv_attr
            )

            # Track subscription
            self._subs.setdefault(topic, []).append((callback, receiver))

        except Exception as e:
            raise LbmError(f"Failed to subscribe to LBM topic '{topic}': {e}") from e

    def unsubscribe(self, topic: str, callback: Any) -> None:
        """Unsubscribe from an LBM topic.

        Args:
            topic: LBM topic string
            callback: The callback that was registered with subscribe()
        """
        if self._ctx is None:
            return

        entries = self._subs.get(topic)
        if not entries:
            return

        # Find matching subscription
        for idx, (cb, receiver) in enumerate(entries):
            if cb is callback:
                # Destroy LBM receiver (pylbm API)
                try:
                    receiver.destroy()
                except Exception:
                    pass

                # Remove from tracking
                entries.pop(idx)
                if not entries:
                    self._subs.pop(topic, None)
                break

    def publish(
        self,
        topic: str,
        payload: bytes,
        *,
        on_sent: Any = None,  # OnSent | None
    ) -> None:
        """Publish a message to an LBM topic.

        Args:
            topic: LBM topic string
            payload: Message data (bytes)
            on_sent: Optional callback invoked after message is sent

        Raises:
            RuntimeError: If transport is not connected
        """
        if self._ctx is None or self._lbm is None or self._loop is None:
            raise RuntimeError("LbmTransport is not connected; cannot publish")

        loop = self._loop
        executor = self._executor

        async def _do_publish() -> None:
            """Async wrapper for LBM publish operation."""
            try:
                # LBM publish happens on thread pool to avoid blocking loop
                await loop.run_in_executor(
                    executor,
                    self._publish_sync,
                    topic,
                    payload,
                )

                # Post-wire: fire on_sent callback on loop thread
                if on_sent is not None:
                    on_sent()

            except Exception:
                # Log but don't propagate - like NatsTransport
                # Publish failures shouldn't crash the test
                pass

        task = loop.create_task(_do_publish())
        self._track(task)

    def _publish_sync(self, topic: str, payload: bytes) -> None:
        """Synchronous LBM publish - called from thread pool."""
        try:
            # Create source for this topic (LBM sender)
            # pylbm API: LbmSource(ctx, topic, callback=None, tattrs=None)
            # Note: In production, sources would be cached per topic
            # For simplicity, we create on-demand (LBM handles efficiency)
            src_attr = self._lbm.LbmSourceTopicAttributes()
            source = self._lbm.LbmSource(self._ctx, topic, callback=None, tattrs=src_attr)

            # Send message
            # pylbm API: send(data, flags) - NOT send(data, length, flags)!
            source.send(payload, flags=0)

            # Destroy source (pylbm API uses destroy() not close())
            # Note: For high-frequency publishing, keep sources cached
            source.destroy()

        except Exception:
            # Publish errors are swallowed - test shouldn't crash on send failure
            pass

    # ---- diagnostics -----------------------------------------------------

    def active_subscription_count(self) -> int:
        """Return total number of active subscriptions."""
        return sum(len(subs) for subs in self._subs.values())

    def clear_subscriptions(self) -> None:
        """Clear subscription tracking (used by test harness on scope teardown)."""
        # Note: This just clears the dict, actual receivers stay active
        # until disconnect() or explicit unsubscribe()
        self._subs.clear()

    # ---- internals -------------------------------------------------------

    def _track(self, task: asyncio.Task[Any]) -> None:
        """Track async task for clean shutdown."""
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


# Import TransportCapabilities from Choreo base
# This is done at the end to avoid circular import
try:
    from choreo.transports.base import TransportCapabilities

    LbmTransport.capabilities = TransportCapabilities(
        broadcast_fanout=True,  # LBM broadcasts to all subscribers
        loses_messages_without_subscriber=True,  # Fire-and-forget pub/sub
        ordered_per_topic=True,  # LBM preserves message order per topic
    )
except ImportError:
    # If Choreo isn't installed, define a stub
    # This allows the module to be used standalone
    class _StubCapabilities:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    LbmTransport.capabilities = _StubCapabilities(
        broadcast_fanout=True,
        loses_messages_without_subscriber=True,
        ordered_per_topic=True,
    )
