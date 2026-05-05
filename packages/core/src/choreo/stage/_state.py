"""Stage internal state types and the public result/report dataclasses.

Held here so the helpers, scope, and Stage modules can import the same
type definitions without circular dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Literal

from choreo.harness import Harness
from choreo.scenario import Handle, TimelineEntry


class _StageState(Enum):
    NEW = "new"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class StageReplyState(StrEnum):
    """Lifecycle state of a Stage scope's reactive reply.

    Mirrors `scenario.ReplyReportState` adapted for the cross-transport
    case. Terminal values appear in `StageReplyReport.state` after
    `await_all`; ARMED is runtime-only — the terminal-state derivation
    in `_resolve_pending_reply` flips ARMED to one of the three derived
    states at scope exit.
    """

    ARMED = "armed"
    """Runtime: registered, no trigger has fired."""

    FIRED = "fired"
    """Terminal: trigger arrived, build succeeded, response published."""

    FIRED_BUILDER_ERROR = "fired_builder_error"
    """Terminal: trigger arrived AND fire-once committed, but the
    build callback raised OR the response publish raised. Mutually
    exclusive with FIRED — once set the reply does not retry."""

    ARMED_NO_MATCH = "armed_no_match"
    """Terminal (derived at scope exit): no message ever reached the
    trigger callback. Distinct from ARMED_MATCHER_MISMATCHED below."""

    ARMED_MATCHER_MISMATCHED = "armed_matcher_mismatched"
    """Terminal (derived at scope exit): messages reached the trigger
    callback (`candidate_count > 0`) but the matcher rejected every
    one (`match_count == 0`). The trigger topic was wired correctly;
    the matcher is the reason no reply fired."""


@dataclass
class _StageReply:
    """Internal record for a live reactive reply registration.

    Mutated by the trigger-topic subscriber as messages arrive; frozen
    into a `StageReplyReport` at scope exit. Held on the TRIGGER child's
    `replies` list (single-writer per ADR-0016 §Fire-once enforcement);
    the response child has no record.
    """

    trigger_topic: str
    trigger_transport: str
    response_topic: str
    response_transport: str
    matcher: Any | None
    matcher_description: str
    build: Callable[[Any], Any]
    state: StageReplyState = StageReplyState.ARMED
    candidate_count: int = 0
    match_count: int = 0
    builder_error: str | None = None
    """Class name only — never `str(exc)`. Builder/publish exceptions
    can carry payload-derived data; the redacted name is enough for
    diagnosis (mirrors ADR-0017 §Security Considerations posture)."""


@dataclass(frozen=True)
class StageReplyReport:
    """Per-reply observability record on `StageScenarioResult.replies`.

    Carries lifecycle state, counts, topics, transports, and a
    redacted-for-logging matcher description. Does NOT carry the
    triggering payload or the response payload — `__repr__` and any
    summary path must not leak payload content.
    """

    trigger_topic: str
    trigger_transport: str
    matcher_description: str
    response_topic: str
    response_transport: str
    state: StageReplyState
    candidate_count: int
    match_count: int
    reply_published: bool
    builder_error: str | None = None

    def __repr__(self) -> str:
        return (
            f"<StageReplyReport trigger={self.trigger_topic} "
            f"@{self.trigger_transport} → {self.response_topic} "
            f"@{self.response_transport} state={self.state.value}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError("StageReplyReport is not pickleable: redaction enforced structurally")


@dataclass
class _StageExpectation:
    """A pending expectation registered via `_StageScenarioScope.expect()`.

    Holds the handle (whose outcome the dispatcher resolves), the matcher
    (applied to inbound payloads), the future (set when the matcher
    accepts a message; awaited by `await_all()`), and the registration
    timestamp (for latency measurement on resolve).
    """

    handle: Handle
    matcher: Any
    fulfilled: asyncio.Future[None]
    registered_at: float


@dataclass
class _StageChild:
    """Per-transport state held by a `_StageScenarioScope`.

    `wire_id` is the value `bridge.to_wire(logical, transport)` returned;
    serialised as `correlation_id` at the report boundary. Wire-id
    terminology is preserved internally because it matches the bridge
    protocol's `to_wire` / `from_wire` API.

    `subscriber_refs` are `(topic, callback)` pairs registered via
    `harness.subscribe()`; teardown iterates these to call
    `harness.unsubscribe()` per pair, isolated by a per-pair try/except
    so one failure does not abort the rest.

    Replies are held only on the TRIGGER child (single-writer per
    ADR-0016).
    """

    wire_id: str
    harness: Harness
    subscriber_refs: list[tuple[str, Callable[[str, bytes], None]]] = field(default_factory=list)
    expectations: list[_StageExpectation] = field(default_factory=list)
    replies: list[_StageReply] = field(default_factory=list)


@dataclass(frozen=True)
class StageScenarioResult:
    """Result of `_StageScenarioScope.await_all(...)`.

    `passed` is handles-only by design — replies are observed via
    `result.replies` and do not dictate scenario pass/fail. Consumers
    asserting that a reply must have FIRED check the report explicitly.

    `assert_passed()` raises `AssertionError` with failing-handle
    diagnostics; reasons are NOT included (may carry payload content).
    """

    handles: tuple[Handle, ...]
    passed: bool
    replies: tuple[StageReplyReport, ...] = ()
    correlation_id: str | None = None
    """The scope's logical id (`bridge.fresh()` output captured at scope
    entry, coerced to str). The choreo-reporter populates
    `scenario.correlation_id` from this without reaching into private
    scope attributes (PRD-012 §2.6)."""
    name: str = ""
    """The scope name passed to `stage.scenario("name")`. The reporter
    uses this as the scenario's display name (replacing the v2
    placeholder `"stage"`)."""
    bridge_class: str | None = None
    """Class name of the `CorrelationBridge` in effect for the Stage
    scope this result came from. The reporter emits the value in
    `scenario.stage.bridge_class` (PRD-012 §1.4)."""
    registered_transports: tuple[str, ...] = ()
    """The transports registered on the Stage at scope open, in
    registration order. Distinct from `by_transport.keys()` which only
    lists transports that produced at least one handle. The reporter
    uses this for the breadcrumb's transports list so the test author
    sees every transport they configured, even if the test path didn't
    fire on one of them."""
    timeline: tuple[TimelineEntry, ...] = ()
    """Observed events recorded during the scope's lifetime, in
    observation order (PRD-013 §2.4). Each entry's `transport` field
    carries the per-transport attribution for transport-scoped events
    and is omitted (`None`) for scope-level events such as `DEADLINE`
    (PRD-013 §D-3). Empty for scopes that did not exercise any
    instrumented path."""
    timeline_dropped: int = 0
    """Count of events the per-scope `_Timeline` ring buffer had to
    drop because the per-scope cap (256) was hit. Distinct from the
    per-run aggregate cap policed at the reporter boundary
    (PRD-013 §D-4)."""
    kind: Literal["stage"] = field(default="stage", init=False)
    """Explicit discriminator (`"stage"`) the choreo-reporter uses for
    dispatch in place of duck-typed hasattr checks (PRD-012 §2.8)."""

    @property
    def by_transport(self) -> Mapping[str, tuple[Handle, ...]]:
        """Per-transport view of `handles`, keyed by `Handle.transport`.

        Stage scenarios produce one entry per touched transport (e.g.
        `{"nats": (...,), "kafka": (...,)}`); single-Harness scenario
        results bypass this view (their handles' `transport` is `None`
        so the mapping is empty).
        """
        groups: dict[str, list[Handle]] = {}
        for handle in self.handles:
            transport = handle.transport
            if transport is None:
                continue
            groups.setdefault(transport, []).append(handle)
        return {name: tuple(group) for name, group in groups.items()}

    def assert_passed(self) -> None:
        """Raise AssertionError if any handle did not resolve as PASS.

        The message names each failing handle's topic, transport, and
        outcome — but NOT `handle.reason`. Reasons are constructed
        from matcher feedback that may include payload contents
        (mismatch reasons can interpolate the rejected value); those
        belong on a programmatic access path, not in an exception
        message that lands in CI logs and pytest verbose output.
        Mirrors the redaction posture `BridgeAmbiguityError` and
        `BridgeTranslationError` take.
        """
        if self.passed:
            return
        failed = [
            f"  - topic={h.topic!r} transport={h.transport!r} "
            f"outcome={h.outcome.value} (see handle.reason for details)"
            for h in self.handles
            if not h.was_fulfilled()
        ]
        raise AssertionError("Stage scenario did not pass; failing handles:\n" + "\n".join(failed))


# Backward-compat alias for the pre-PRD-012 underscored name. In-tree
# imports (`from choreo.stage import _StageScenarioResult`) continue to
# resolve to the public class. Aliased for two minor versions per
# PRD-012 §2.8.
_StageScenarioResult = StageScenarioResult
