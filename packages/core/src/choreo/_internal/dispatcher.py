"""Dispatcher — correlation-based inbound Mediator (ADR-0004).

The single dispatch point for every inbound message. Extracts the correlation
ID from the payload via a per-topic extractor, looks up the owning scope, and
fires the caller-supplied resolver. Unmatched inbound goes to a redacted
surprise log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .loop_poster import LoopPoster

Extractor = Callable[[bytes], str | None]
Resolver = Callable[[Any, bytes], None]


@dataclass(frozen=True)
class SurpriseEntry:
    """Redacted record of an inbound message the dispatcher could not route.

    No `payload` field by design — payloads may contain regulated or
    personal data. See [ADR-0004 §Security Considerations](../../docs/adr/0004-dispatcher-correlation-mediator.md).
    """

    topic: str
    correlation_id: str
    classification: (
        str  # "unknown_scope" | "timeout_race" | "no_correlation_field" | "no_extractor"
    )
    size: int


# Modules whose loaders execute payload-supplied code — forbidden as extractors.
_FORBIDDEN_EXTRACTOR_MODULES: frozenset[str] = frozenset({"pickle", "_pickle", "marshal", "shelve"})
_FORBIDDEN_EXTRACTOR_NAMES: frozenset[str] = frozenset({"loads", "load"})


def _is_forbidden_extractor(fn: Callable[..., Any]) -> bool:
    module = getattr(fn, "__module__", "") or ""
    name = getattr(fn, "__name__", "") or ""
    if module in _FORBIDDEN_EXTRACTOR_MODULES and name in _FORBIDDEN_EXTRACTOR_NAMES:
        return True
    # yaml.load without SafeLoader — err strict
    if module == "yaml" and name == "load":
        return True
    return False


class Dispatcher:
    """Single dispatch point for inbound. Subclassing that overrides
    `dispatch` is refused at class creation."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "dispatch" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} cannot override Dispatcher.dispatch; the single "
                "dispatch-point invariant is enforced at class creation (ADR-0004)"
            )

    def __init__(self, *, poster: LoopPoster) -> None:
        self._poster = poster
        self._scopes: dict[str, Any] = {}
        self._deregistered: set[str] = set()
        self._extractors: dict[str, Extractor] = {}
        self._surprise_log: list[SurpriseEntry] = []

    def register_scope(self, scope: Any, correlation_id: str) -> None:
        """Attach a scope to its correlation ID so inbound can reach it."""
        self._scopes[correlation_id] = scope
        self._deregistered.discard(correlation_id)

    def deregister_scope(self, scope: Any) -> None:
        """Detach a scope. Any subsequent inbound for its correlation ID
        becomes a `timeout_race` entry in the surprise log."""
        to_remove = [cid for cid, sc in self._scopes.items() if sc is scope]
        for cid in to_remove:
            del self._scopes[cid]
            self._deregistered.add(cid)

    def register_extractor(self, topic: str, extractor: Extractor) -> None:
        """Register a per-topic parser that pulls the correlation ID out of a payload.

        Deserialisers that execute payload-supplied code (pickle.loads, marshal.loads,
        yaml.load) are refused."""
        if _is_forbidden_extractor(extractor):
            raise ValueError(
                f"extractor {extractor!r} is a deserialiser and is forbidden; "
                "extractors must be pure parsing functions (ADR-0004 §Security)"
            )
        self._extractors[topic] = extractor

    def dispatch(
        self,
        *,
        topic: str,
        payload: bytes,
        source: str,
        resolver: Resolver,
    ) -> None:
        """Route one inbound message.

        Must run on the asyncio loop thread. Transports call this via
        `LoopPoster.post(dispatcher.dispatch, ...)`.
        """
        extractor = self._extractors.get(topic)
        if extractor is None:
            self._surprise_log.append(
                SurpriseEntry(
                    topic=topic,
                    correlation_id="",
                    classification="no_extractor",
                    size=len(payload),
                )
            )
            return

        correlation_id = extractor(payload)
        if correlation_id is None:
            self._surprise_log.append(
                SurpriseEntry(
                    topic=topic,
                    correlation_id="",
                    classification="no_correlation_field",
                    size=len(payload),
                )
            )
            return

        scope = self._scopes.get(correlation_id)
        if scope is not None:
            resolver(scope, payload)
            return

        classification = "timeout_race" if correlation_id in self._deregistered else "unknown_scope"
        self._surprise_log.append(
            SurpriseEntry(
                topic=topic,
                correlation_id=correlation_id,
                classification=classification,
                size=len(payload),
            )
        )

    def surprise_log(self) -> list[SurpriseEntry]:
        return list(self._surprise_log)
