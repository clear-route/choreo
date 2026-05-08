"""Reply types and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from admiral.matchers import Matcher


class _ReplyState(StrEnum):
    """Runtime state of a live reply.

    Distinct from the terminal `ReplyReportState` derived at scope exit:
    this tracks what a reply is doing *while it is armed*, the report
    captures how it ended up.
    """

    ARMED = "armed"
    REPLIED = "replied"
    FAILED = "failed"


class ReplyReportState(StrEnum):
    """Terminal state on `ReplyReport`, derived at scope exit.

    The four states distinguish failure modes that look identical from
    outside the reply: wrong topic (no candidates arrived) vs wrong shape
    (candidates arrived but matcher did not accept any).
    """

    ARMED_NO_MATCH = "armed_no_match"
    ARMED_MATCHER_MISMATCHED = "armed_matcher_mismatched"
    REPLIED = "replied"
    REPLY_FAILED = "reply_failed"


class ReplyAlreadyBoundError(RuntimeError):
    """Raised when `ReplyChain.publish()` is called more than once.

    Chains are single-use: one `on()` binds one reply. Multi-hop chains are
    a follow-up.
    """


@dataclass(frozen=True)
class ReplyReport:
    """Per-reply observability record on `ScenarioResult.replies`.

    Carries state, counts, topics and a redacted-for-logging matcher
    description. It does NOT carry the triggering payload or the published
    reply payload — `__repr__` and `summary()` must not leak payload content
   . `builder_error` is the exception
    class name alone (never `str(e)`) so a builder raising with a
    payload-derived message does not leak through the report.
    """

    trigger_topic: str
    matcher_description: str
    reply_topic: str
    state: ReplyReportState
    candidate_count: int
    match_count: int
    reply_published: bool
    builder_error: str | None = None
    correlation_overridden: bool = False

    def __repr__(self) -> str:
        return (
            f"<ReplyReport trigger={self.trigger_topic} "
            f"reply={self.reply_topic} state={self.state.value}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError("ReplyReport is not pickleable: redaction enforced structurally")


@dataclass
class _Reply:
    """Internal record for a live reply registration.

    Mutated by the trigger-topic subscriber as messages arrive. Frozen into
    a `ReplyReport` at scope exit. One instance per `on().publish()` call.
    """

    trigger_topic: str
    matcher: Matcher | None
    reply_topic: str
    reply_spec: Any
    matcher_description: str
    state: _ReplyState = _ReplyState.ARMED
    candidate_count: int = 0
    match_count: int = 0
    reply_published: bool = False
    builder_error: str | None = None
    correlation_overridden: bool = False


def _derive_reply_state(reply: _Reply) -> ReplyReportState:
    """Map the runtime state + counts onto the terminal report state."""
    if reply.state is _ReplyState.FAILED:
        return ReplyReportState.REPLY_FAILED
    if reply.state is _ReplyState.REPLIED:
        return ReplyReportState.REPLIED
    # state is ARMED
    if reply.candidate_count == 0:
        return ReplyReportState.ARMED_NO_MATCH
    return ReplyReportState.ARMED_MATCHER_MISMATCHED


def _freeze_reply_reports(replies: list[_Reply]) -> tuple[ReplyReport, ...]:
    return tuple(
        ReplyReport(
            trigger_topic=r.trigger_topic,
            matcher_description=r.matcher_description,
            reply_topic=r.reply_topic,
            state=_derive_reply_state(r),
            candidate_count=r.candidate_count,
            match_count=r.match_count,
            reply_published=r.reply_published,
            builder_error=r.builder_error,
            correlation_overridden=r.correlation_overridden,
        )
        for r in replies
    )
