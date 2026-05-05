"""ScenarioResult and its diagnostic helpers (PRD-006, PRD-012)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ._handle import Handle
from ._outcome import Outcome
from ._reply import ReplyReport
from ._timeline import TimelineEntry


@dataclass
class ScenarioResult:
    name: str
    correlation_id: str | None
    handles: tuple[Handle, ...]
    passed: bool
    timeline: tuple[TimelineEntry, ...] = ()
    timeline_dropped: int = 0
    replies: tuple[ReplyReport, ...] = ()
    # PRD-012 §2.1, §2.8: explicit discriminator the choreo-reporter
    # dispatches on, replacing the v2-proposed hasattr duck-typing.
    # Per-type fixed (init=False); a Stage result's `kind` is "stage".
    kind: Literal["single_harness"] = field(default="single_harness", init=False)

    @property
    def failing_handles(self) -> tuple[Handle, ...]:
        return tuple(h for h in self.handles if not h.was_fulfilled())

    def __reduce__(self) -> Any:
        raise TypeError(
            "ScenarioResult is not pickleable — carries Handle and ReplyReport "
            "objects that may hold payload content (ADR-0017)"
        )

    def reply_at(self, trigger_topic: str) -> ReplyReport:
        """Return the reply report for `trigger_topic` (ADR-0017).

        Raises KeyError when no reply with that trigger was registered.
        """
        for r in self.replies:
            if r.trigger_topic == trigger_topic:
                return r
        raise KeyError(trigger_topic)

    def assert_passed(self) -> None:
        """Raise AssertionError with a breakdown of every non-passing expectation.

        Use this in place of `assert result.passed is True`; the error message
        names every failing topic, matcher, outcome, and reason rather than
        just showing `False != True`.
        """
        if self.passed:
            return
        raise AssertionError(self.failure_summary())

    def failure_summary(self) -> str:
        """Multi-line breakdown of failing expectations. Always shows every
        handle so the caller sees the full context.

        The diagnosis text distinguishes between a silent timeout (no message
        arrived on the topic matching the scope's correlation) and a near-miss
        timeout (N messages matched correlation but failed the matcher). The
        two are materially different bugs — routing vs expectation — and the
        error message must make the distinction obvious."""
        failing = self.failing_handles
        total = len(self.handles)
        header = (
            f"scenario {self.name!r} failed — {len(failing)} of {total} expectations did not pass"
        )
        lines = [
            header,
            f"correlation: {self.correlation_id}",
            "",
        ]
        for h in self.handles:
            latency = f"{h._latency_ms:.1f}ms" if h._latency_ms is not None else "-"
            lines.append(f"  [{h.outcome.value.upper()}] {h.topic}")
            lines.append(f"      matcher : {h.matcher_description}")
            lines.append(f"      why     : {_diagnose(h)}")
            lines.append(f"      latency : {latency}")
            lines.append("")
        if self.timeline:
            shown = self.timeline[-20:]
            total = len(self.timeline) + self.timeline_dropped
            lines.append(f"Timeline (last {len(shown)} of {total}):")
            if self.timeline_dropped:
                lines.append(f"  ... {self.timeline_dropped} earliest entries dropped (buffer cap)")
            elif len(self.timeline) > len(shown):
                lines.append(f"  ... {len(self.timeline) - len(shown)} earlier entries elided")
            for e in shown:
                suffix = f"  ({e.detail})" if e.detail else ""
                lines.append(f"  {e.offset_ms:7.1f}ms  {e.topic:<24} {e.action.value}{suffix}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def summary(self) -> str:
        """Short single-line-per-handle summary, intended for structured logs."""
        lines = [f"scenario={self.name} passed={self.passed}"]
        for h in self.handles:
            lines.append(
                f"  [{h.outcome.value}] topic={h.topic} "
                f"matcher={h.matcher_description}"
                + (f" latency={h.latency_ms:.1f}ms" if h._latency_ms is not None else "")
            )
        if self.replies:
            lines.append("Replies:")
            for r in self.replies:
                detail = f" builder={r.builder_error}" if r.builder_error else ""
                override = " correlation_overridden" if r.correlation_overridden else ""
                lines.append(
                    f"  {r.trigger_topic} -> {r.reply_topic}: {r.state.value} "
                    f"({r.match_count} match / {r.candidate_count} candidate)"
                    f"{detail}{override}"
                )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.failure_summary()


def _diagnose(handle: Handle) -> str:
    """One-line human-readable explanation of a handle's outcome.

    Outcome labels carry the primary signal:
      - TIMEOUT → no message arrived on the topic + correlation (routing issue)
      - FAIL    → messages arrived but none satisfied the matcher (expectation issue)
      - PASS    → matched
    """
    if handle.outcome is Outcome.PASS:
        return f"matched: {handle._reason}"
    if handle.outcome is Outcome.TIMEOUT:
        latency = handle._latency_ms or 0
        return f"no matching message arrived on topic {handle.topic!r} within {latency:.0f}ms"
    if handle.outcome is Outcome.FAIL:
        plural = "s" if handle._attempts != 1 else ""
        return (
            f"{handle._attempts} message{plural} matched the correlation "
            f"but failed the matcher; latest mismatch: "
            f"{handle._last_mismatch_reason}"
        )
    if handle.outcome is Outcome.SLOW:
        budget = handle._budget_ms or 0
        latency = handle._latency_ms or 0
        return (
            f"matched in {latency:.1f}ms, budget {budget:.1f}ms "
            f"(exceeded by {latency - budget:.1f}ms)"
        )
    return f"unresolved (outcome={handle.outcome.value})"
