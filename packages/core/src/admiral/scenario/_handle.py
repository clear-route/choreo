"""Handle — the per-expectation observation object (ADR-0014, PRD-006)."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

from admiral.matchers import MatchFailure

from ._outcome import Outcome




_FAILURES_MAX = 20


@dataclass
class Handle:
    topic: str
    matcher_description: str
    correlation_id: str | None
    outcome: Outcome = Outcome.PENDING
    _transport: str | None = None
    """Multi-transport scenarios (Stage, ADR-0027) populate this with the
    transport name the handle was registered against. None for handles
    created by single-Harness scenarios. The leading underscore plus the
    `transport` property below mean there is no public setter; the value
    is set once via the dataclass constructor and is read-only thereafter.
    Prevents consumer-side post-hoc mutation that would (a) confuse the
    dispatcher routing and (b) leak unintended values into Handle.__repr__
    (per ADR-0027 §Security Considerations)."""
    _message: Any = None
    _latency_ms: float | None = None
    _reason: str = ""
    _attempts: int = 0
    _last_mismatch_reason: str | None = None
    _last_mismatch_payload: Any = None
    _failures: list[MatchFailure] = field(default_factory=list)
    _failures_dropped: int = 0
    _budget_ms: float | None = None
    _matcher_expected: Any = None

    @property
    def transport(self) -> str | None:
        """Read-only transport name. See `_transport` docstring above
        and ADR-0027 §Security Considerations for the read-only rationale."""
        return self._transport

    def within_ms(self, budget_ms: float) -> Handle:
        """Declare a latency budget for this expectation (PRD-006).

        If the matcher accepts a message but the elapsed time since the
        expectation was registered exceeds `budget_ms`, the outcome is
        `Outcome.SLOW` and the scenario fails. Call this after `expect()`
        and before `publish()`; calling after the handle resolves raises
        `RuntimeError`. Re-calling replaces the prior budget and emits
        a `UserWarning`.
        """
        if not isinstance(budget_ms, (int, float)) or isinstance(budget_ms, bool):
            raise TypeError(f"budget_ms must be a number, got {type(budget_ms).__name__}")
        fb = float(budget_ms)
        if fb <= 0 or fb != fb or fb == float("inf"):
            raise ValueError(f"budget_ms must be positive and finite, got {budget_ms!r}")
        if self.outcome is not Outcome.PENDING:
            raise RuntimeError(
                "within_ms() called after the handle resolved "
                f"(outcome={self.outcome.value}); declare budgets before publish()"
            )
        if self._budget_ms is not None:
            warnings.warn(
                f"within_ms({fb}) overrides previously-set budget "
                f"{self._budget_ms} on handle for topic {self.topic!r}",
                UserWarning,
                stacklevel=2,
            )
        self._budget_ms = fb
        return self

    def was_fulfilled(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def message(self) -> Any:
        if self.outcome is Outcome.PENDING:
            raise RuntimeError("handle accessed before await_all() — outcome is still PENDING")
        return self._message

    @property
    def latency_ms(self) -> float:
        if self._latency_ms is None:
            raise RuntimeError("handle accessed before await_all() — latency not yet measured")
        return self._latency_ms

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def attempts(self) -> int:
        """Count of messages that matched the scope's correlation on this topic
        but failed the matcher. Zero means no message arrived at all;
        non-zero means routing worked but the matcher did not accept what came in."""
        return self._attempts

    @property
    def last_mismatch_reason(self) -> str | None:
        """Reason from the most recent matcher mismatch, or None if no
        attempts occurred. Useful for diagnosing near-miss timeouts."""
        return self._last_mismatch_reason

    @property
    def failures(self) -> tuple[MatchFailure, ...]:
        """Every near-miss observed on this handle, oldest-first. Bounded by
        `_FAILURES_MAX`; overflow is counted on `failures_dropped`. Structured
        alternative to `last_mismatch_reason` — the report renders these into
        a typed expected-vs-actual diff without parsing any prose."""
        return tuple(self._failures)

    @property
    def failures_dropped(self) -> int:
        """Count of near-misses beyond the cap that were not retained."""
        return self._failures_dropped

    @property
    def last_mismatch_payload(self) -> Any:
        """The decoded payload of the most recent matcher mismatch, or None
        if no attempts occurred. Consumed by the test report (PRD-007) to
        render the actual-side of the expected-vs-actual diff when the
        handle resolves to FAIL (i.e. messages arrived but none matched)."""
        return self._last_mismatch_payload

    @property
    def matcher_expected(self) -> Any:
        """Machine-readable expected shape captured from `matcher.expected_shape()`
        at expectation-registration time, or None if the matcher does not
        expose one. Consumed by the test report (PRD-007) to render the
        expected-side of the expected-vs-actual diff."""
        return self._matcher_expected

    def __repr__(self) -> str:
        
        
        
        
        
        transport_part = f" transport={self._transport}" if self._transport is not None else ""
        return (
            f"<Handle topic={self.topic}{transport_part} "
            f"outcome={self.outcome.value} "
            f"matcher={self.matcher_description!r}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError("Handle is not pickleable — may carry payload data")
