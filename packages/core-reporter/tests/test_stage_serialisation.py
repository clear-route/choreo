"""Stage scenario serialisation.

Unit tests for the reporter's Stage support: kind-based dispatch,
reply-state mapping (StageReplyState → schema strings, no enum
extension), per-handle transport emission, hash-based wire-id
redaction at the reporter boundary, scenario.stage block, and
run-level transports aggregation.

Tests construct framework objects directly to keep the unit boundary
tight; pytester-driven end-to-end emission lives in
`test_json_output_shape.py`.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from admiral.scenario import (
    Handle,
    Outcome,
    ReplyReport,
    ReplyReportState,
    ScenarioResult,
    TimelineAction,
    TimelineEntry,
)
from admiral.stage import (
    StageReplyReport,
    StageReplyState,
    StageScenarioResult,
)
from admiral_reporter._collect import Collector
from admiral_reporter._redact import RedactionStats
from admiral_reporter._serialise import (
    _serialise_reply_state,
    serialise_reply_report,
    serialise_scenario,
)

_REDACTED_PATTERN = re.compile(r"^sha256:[0-9a-f]{16}$")


# ---------------------------------------------------------------------------
# Reply state mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (StageReplyState.FIRED, "replied"),
        (StageReplyState.FIRED_BUILDER_ERROR, "reply_failed"),
        (StageReplyState.ARMED_NO_MATCH, "armed_no_match"),
        (StageReplyState.ARMED_MATCHER_MISMATCHED, "armed_matcher_mismatched"),
        (ReplyReportState.REPLIED, "replied"),
        (ReplyReportState.REPLY_FAILED, "reply_failed"),
        (ReplyReportState.ARMED_NO_MATCH, "armed_no_match"),
        (ReplyReportState.ARMED_MATCHER_MISMATCHED, "armed_matcher_mismatched"),
    ],
)
def test_serialise_reply_state_should_map_every_enum_value_per_the_table(
    state: Any, expected: str
) -> None:
    assert _serialise_reply_state(state) == expected


def test_serialise_reply_state_should_raise_on_None():
    with pytest.raises(ValueError):
        _serialise_reply_state(None)


def test_serialise_reply_state_should_raise_on_unknown_string():
    """The reporter does not silently coerce; freeze-path bugs surface
    immediately. 2."""
    with pytest.raises(ValueError):
        _serialise_reply_state("nonsense")


def test_serialise_reply_state_should_raise_assertion_error_on_StageReplyState_ARMED():
    """ARMED is a runtime-only state; encountering it at serialisation
    is a freeze-path bug in the framework's __aexit__.  §1.7
    requires AssertionError (programmer-error class) rather than
    ValueError (input-error class) so the distinction is preserved
    in CI logs."""
    with pytest.raises(AssertionError):
        _serialise_reply_state(StageReplyState.ARMED)


# ---------------------------------------------------------------------------
# Reply transport fields
# ---------------------------------------------------------------------------


def test_a_stage_reply_report_should_emit_trigger_and_response_transports():
    rep = StageReplyReport(
        trigger_topic="orders.new",
        trigger_transport="kafka",
        matcher_description="any",
        response_topic="orders.processed",
        response_transport="nats",
        state=StageReplyState.FIRED,
        candidate_count=1,
        match_count=1,
        reply_published=True,
        builder_error=None,
    )
    out = serialise_reply_report(rep)
    assert out["trigger_transport"] == "kafka"
    assert out["response_transport"] == "nats"
    # Topic mapping: response_topic → reply_topic JSON key (§1.2.1)
    assert out["reply_topic"] == "orders.processed"
    # State mapping: FIRED → "replied" (no enum extension)
    assert out["state"] == "replied"


def test_a_single_harness_reply_report_should_omit_transport_fields():
    rep = ReplyReport(
        trigger_topic="orders.new",
        reply_topic="orders.processed",
        state=ReplyReportState.REPLIED,
        matcher_description="any",
        candidate_count=1,
        match_count=1,
        reply_published=True,
        builder_error=None,
        correlation_overridden=False,
    )
    out = serialise_reply_report(rep)
    # Backward compat: single-Harness reports must not carry the new keys.
    assert "trigger_transport" not in out
    assert "response_transport" not in out


# ---------------------------------------------------------------------------
# Stage scenario emission
# ---------------------------------------------------------------------------


def _make_handle(
    *,
    topic: str = "results",
    transport: str | None = "nats",
    wire_id: str = "logical-3f2a91b8c4d50e1f",
    outcome: Outcome = Outcome.PASS,
) -> Handle:
    """Construct a minimal pass-state Handle for serialisation tests."""
    h = Handle(
        topic=topic,
        matcher_description="any",
        correlation_id=wire_id,
        outcome=outcome,
        _transport=transport,
    )
    h._latency_ms = 5.0
    return h


def _stage_scenario_result(
    *,
    correlation_id: str = "logical-3f2a91b8c4d50e1f",
    handles: tuple[Handle, ...] | None = None,
    replies: tuple[StageReplyReport, ...] = (),
) -> StageScenarioResult:
    if handles is None:
        handles = (_make_handle(transport="nats"),)
    return StageScenarioResult(
        handles=handles,
        passed=True,
        replies=replies,
        correlation_id=correlation_id,
    )


def test_a_stage_scenario_should_emit_a_stage_block():
    result = _stage_scenario_result()
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert "stage" in out
    block = out["stage"]
    assert set(block.keys()) == {"bridge_class", "transports", "correlation_ids"}


def test_a_stage_scenario_should_emit_the_scope_name_passed_to_stage_scenario():
    """The user-feedback fix: the report's scenario `name` field
    carries the scope name (`stage.scenario("bridge_round_trip")`)
    instead of the v2 placeholder string `"stage"`."""
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="bridge_round_trip",
        bridge_class="MappedBridge",
        registered_transports=("nats",),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["name"] == "bridge_round_trip"


def test_stage_block_transports_should_list_every_registered_transport_not_just_touched():
    """The user-feedback fix: a Stage scope opens against (e.g.)
    `("nats", "kafka")` even if a particular test path only fires
    on `nats`. The report should show the registered shape, not
    the executed subset."""
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),  # only nats produced a handle
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="round_trip",
        bridge_class="MappedBridge",
        registered_transports=("nats", "kafka"),  # both registered
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["stage"]["transports"] == ["kafka", "nats"]


def test_a_stage_block_should_carry_sorted_transports():
    """Determinism is a snapshot-test contract."""
    handles = (
        _make_handle(transport="nats", wire_id="nats-3f2a91b8c4d50e1f"),
        _make_handle(transport="kafka", wire_id="kafka-3f2a91b8c4d50e1f"),
    )
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["stage"]["transports"] == ["kafka", "nats"]


def test_stage_block_correlation_ids_should_be_hash_redacted():
    """5.1: every per-transport correlation id in the
    report is redacted via `admiral.redaction.redact_correlation_id`.
    Shape: `sha256:<16 hex>`."""
    handles = (
        _make_handle(transport="nats", wire_id="nats-3f2a91b8c4d50e1f"),
        _make_handle(transport="kafka", wire_id="kafka-3f2a91b8c4d50e1f"),
    )
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    for transport, redacted in out["stage"]["correlation_ids"].items():
        assert _REDACTED_PATTERN.fullmatch(redacted), (
            f"correlation_ids[{transport}]={redacted!r} does not match sha256:<16 hex>"
        )


def test_a_stage_handle_correlation_id_should_be_hash_redacted():
    """5.1 / Goal 6: Stage handles' correlation_id is
    hash-redacted at the report boundary; v2's leakage path closed."""
    handles = (_make_handle(transport="nats", wire_id="nats-orders-3f2a91b8"),)
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    handle_out = out["handles"][0]
    assert "correlation_id" in handle_out
    assert _REDACTED_PATTERN.fullmatch(handle_out["correlation_id"])


# ---------------------------------------------------------------------------
# Redaction sites canary
# ---------------------------------------------------------------------------


def test_a_secret_marker_in_a_stage_handle_correlation_id_should_not_appear_verbatim_in_the_emitted_json():
    """Canary test for redaction completeness. A wire id containing
    `SECRET-CANARY-XYZ` is set on every Stage handle's correlation_id;
    after serialisation, the marker must NOT appear in the emitted
    JSON for that handle (the value is replaced by the `sha256:<hex>`
    redacted form). 5.1, §2.4."""
    import json as _json

    canary = "SECRET-CANARY-XYZ-3f2a91b8c4d50e1f"
    handles = (_make_handle(transport="nats", wire_id=canary),)
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    serialised = _json.dumps(out)
    assert canary not in serialised, (
        f"redaction failed — canary {canary!r} appears verbatim in output"
    )


def test_a_secret_marker_in_stage_correlation_ids_should_not_appear_verbatim_in_the_emitted_json():
    """Canary test for the `scenario.stage.correlation_ids` redaction
    site. Even though the values are constructed from per-handle
    correlation_ids (covered by the test above), this asserts the
    explicit redaction path is independent — a future refactor that
    bypasses one site does not silently bypass the other."""
    import json as _json

    canary = "SECRET-CANARY-XYZ-7e8b50c1ad4912ff"
    handles = (
        _make_handle(transport="nats", wire_id=canary),
        _make_handle(transport="kafka", wire_id=canary),
    )
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=12.5,
        completed_normally=True,
        stats=RedactionStats(),
    )
    serialised = _json.dumps(out)
    assert canary not in serialised, (
        f"correlation_ids redaction failed — canary {canary!r} appears verbatim"
    )


def test_a_single_harness_handle_correlation_id_should_be_unchanged():
    """Backward compat: single-Harness handle correlation_ids are NOT
    redacted in the report."""
    handle = Handle(
        topic="orders.settled",
        matcher_description="any",
        correlation_id="TEST-3f2a91b8c4d50e1f",
        outcome=Outcome.PASS,
    )
    handle._latency_ms = 5.0
    result = ScenarioResult(
        name="settle",
        correlation_id="TEST-3f2a91b8c4d50e1f",
        handles=(handle,),
        passed=True,
    )
    out = serialise_scenario(
        result,
        duration_ms=5.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    # Single-Harness scenarios do not carry per-handle correlation_id
    # in the JSON output (the scenario-level correlation_id covers it).
    # The scenario-level correlation_id must NOT be hashed.
    assert out["correlation_id"] == "TEST-3f2a91b8c4d50e1f"


def test_a_stage_handle_should_emit_a_transport_field():
    handles = (_make_handle(transport="nats"),)
    result = _stage_scenario_result(handles=handles)
    out = serialise_scenario(
        result,
        duration_ms=5.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    handle_out = out["handles"][0]
    assert handle_out["transport"] == "nats"


def test_a_single_harness_handle_should_omit_the_transport_field():
    """Backward compat: byte-identical to v1.0 emission."""
    handle = Handle(
        topic="orders.settled",
        matcher_description="any",
        correlation_id="TEST-x",
        outcome=Outcome.PASS,
    )
    handle._latency_ms = 5.0
    result = ScenarioResult(
        name="settle",
        correlation_id="TEST-x",
        handles=(handle,),
        passed=True,
    )
    out = serialise_scenario(
        result,
        duration_ms=5.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    handle_out = out["handles"][0]
    # Single-Harness handles MUST OMIT the transport key entirely
    # (not emit `null`) — keeps JSON byte-identical pre/post
    #.
    assert "transport" not in handle_out


def test_a_single_harness_scenario_should_omit_the_stage_block():
    handle = Handle(
        topic="orders.settled",
        matcher_description="any",
        correlation_id="TEST-x",
        outcome=Outcome.PASS,
    )
    handle._latency_ms = 5.0
    result = ScenarioResult(
        name="settle",
        correlation_id="TEST-x",
        handles=(handle,),
        passed=True,
    )
    out = serialise_scenario(
        result,
        duration_ms=5.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert "stage" not in out


# ---------------------------------------------------------------------------
# Run-level transports + schema_version bump
# ---------------------------------------------------------------------------


def _collector_with_scenarios(*scenarios: Any) -> Collector:
    """Helper: build a Collector seeded with scenarios on a synthetic test."""
    collector = Collector()
    collector.start_run(0.0)
    for scenario in scenarios:
        collector.record_scenario(scenario, nodeid="t.py::test_x", completed_normally=True)
    collector.finish_run()
    return collector


def _to_dict(collector: Collector) -> dict[str, Any]:
    return collector.to_dict(
        reporter_version="0.2.0",
        harness_version="0.1.0",
        git_sha=None,
        git_branch=None,
        xdist=None,
        final_duration_ms=1.0,
    )


def test_emitted_reports_should_carry_schema_version_1_3():
    collector = _collector_with_scenarios()
    out = _to_dict(collector)
    assert out["schema_version"] == "1.3"


# ---------------------------------------------------------------------------
# Stage timeline serialisation
# ---------------------------------------------------------------------------


def _make_timeline_entry(
    *,
    action: TimelineAction = TimelineAction.PUBLISHED,
    topic: str | None = "orders.new",
    transport: str | None = "kafka",
    detail: str = "",
    logical_topic: str | None = None,
) -> TimelineEntry:
    return TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic=topic,
        action=action,
        detail=detail,
        transport=transport,
        logical_topic=logical_topic,
    )


def test_a_stage_scenario_should_serialise_its_timeline_entries():
    """ §3.1: the reporter populates `scenario.timeline` from
    `StageScenarioResult.timeline`. The Phase-1-deferral marker
    (`timeline: []`) is removed."""
    entry = _make_timeline_entry(action=TimelineAction.PUBLISHED, transport="kafka")
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="round-trip",
        bridge_class="MappedBridge",
        registered_transports=("nats", "kafka"),
        timeline=(entry,),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert len(out["timeline"]) == 1
    assert out["timeline"][0]["action"] == "published"
    assert out["timeline"][0]["topic"] == "orders.new"


def test_a_stage_scenario_should_round_trip_timeline_dropped():
    """`timeline_dropped` is the per-scope ring-buffer overflow count
    surfaced to the consumer alongside the captured entries."""
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="dropped",
        bridge_class="MappedBridge",
        registered_transports=("nats",),
        timeline=(),
        timeline_dropped=42,
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["timeline_dropped"] == 42


def test_a_stage_timeline_entry_should_emit_the_transport_field_when_set():
    """Stage entries produced by a per-transport child carry the
    child's transport name in the JSON.  §1.1."""
    entry = _make_timeline_entry(transport="kafka")
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="transport-field",
        bridge_class="MappedBridge",
        registered_transports=("nats", "kafka"),
        timeline=(entry,),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["timeline"][0]["transport"] == "kafka"


def test_a_stage_scope_level_event_should_omit_the_transport_key_entirely():
    """ §D-3: scope-level events (DEADLINE) omit the `transport`
    JSON key entirely - not `null`. Symmetric `topic` omission applies."""
    entry = _make_timeline_entry(
        action=TimelineAction.DEADLINE,
        topic=None,
        transport=None,
        detail="timeout_ms=200",
    )
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="deadline-omit",
        bridge_class="MappedBridge",
        registered_transports=("nats",),
        timeline=(entry,),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    payload = out["timeline"][0]
    assert "transport" not in payload
    assert "topic" not in payload
    assert payload["action"] == "deadline"


def test_a_stage_timeline_entry_should_emit_logical_topic_when_set():
    """Forward-compat: when a translating bridge populates
    `logical_topic`, the reporter emits the field. Otherwise the JSON
    key is omitted."""
    entry = _make_timeline_entry(
        topic="nats-orders",
        transport="nats",
        logical_topic="orders",
    )
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="logical-topic",
        bridge_class="MappedBridge",
        registered_transports=("nats",),
        timeline=(entry,),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert out["timeline"][0]["logical_topic"] == "orders"


def test_a_stage_timeline_entry_should_omit_logical_topic_when_unset():
    """Default `logical_topic=None` produces an absent JSON key, not
    `"logical_topic": null`."""
    entry = _make_timeline_entry(transport="kafka")
    result = StageScenarioResult(
        handles=(_make_handle(transport="nats"),),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="no-logical-topic",
        bridge_class="MappedBridge",
        registered_transports=("kafka",),
        timeline=(entry,),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    assert "logical_topic" not in out["timeline"][0]


def test_a_single_harness_timeline_entry_should_omit_the_transport_key():
    """Byte-identity contract from : single-`Harness` entries
    have `transport=None` and the reporter omits the JSON key entirely
    (no surprise `null` regression for v1.1-pinned consumers)."""
    from admiral_reporter._serialise import serialise_timeline_entry

    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="results",
        action=TimelineAction.PUBLISHED,
    )
    out = serialise_timeline_entry(entry)
    assert "transport" not in out
    assert "logical_topic" not in out
    #  §1.6 / schema v1.3: single-Harness entries also omit
    # `source` for byte-identity with v1.0/v1.1/v1.2.
    assert "source" not in out


def test_a_timeline_entry_should_emit_source_when_set():
    """v1.3 schema field: `source` carries the DSL-surface attribution
    (`publish` / `expect` / `reply` / `scope`). Emitted when set."""
    from admiral_reporter._serialise import serialise_timeline_entry

    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
        source="publish",
    )
    out = serialise_timeline_entry(entry)
    assert out["source"] == "publish"


def test_a_reply_chain_published_response_should_carry_source_reply_in_the_json():
    """End-to-end: a Stage REPLIED entry with `source="reply"` round-trips
    into the JSON's `scenario.timeline[].source`. Disambiguates the
    chain's automatic response from a test-side publish on the same
    topic without reading the test code."""
    from admiral.stage import StageScenarioResult

    handle = _make_handle(transport="kafka")
    test_pub = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
        source="publish",
    )
    chain_replied = TimelineEntry(
        offset_ms=1.0,
        _wall_clock_epoch=0.001,
        topic="orders.processed",
        action=TimelineAction.REPLIED,
        transport="nats",
        detail="trigger=orders.new",
        source="reply",
    )
    result = StageScenarioResult(
        handles=(handle,),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="source-disambiguation",
        bridge_class="MappedBridge",
        registered_transports=("kafka", "nats"),
        timeline=(test_pub, chain_replied),
    )
    out = serialise_scenario(
        result,
        duration_ms=1.0,
        completed_normally=True,
        stats=RedactionStats(),
    )
    sources = [e.get("source") for e in out["timeline"]]
    assert sources == ["publish", "reply"]


# ---------------------------------------------------------------------------
# End-to-end Phase 1 -> Phase 2 wiring
# ---------------------------------------------------------------------------


def test_an_end_to_end_stage_scenario_should_emit_a_non_empty_timeline_in_the_report():
    """Phase 1 + Phase 2 wired together: a real `Stage` scope's
    `result.timeline` (populated by the eight hook points) must
    round-trip through the reporter into the JSON's
    `scenario.timeline[]` array - no longer the v2-era empty-list
    deferral marker."""
    from admiral.scenario import Outcome
    from admiral.stage import StageScenarioResult

    # Construct a result mirroring what the framework produces today
    # for a Stage scope that published once and the matcher accepted.
    handle = _make_handle(transport="kafka", outcome=Outcome.PASS)
    publish_entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
    )
    received_entry = TimelineEntry(
        offset_ms=0.5,
        _wall_clock_epoch=0.0005,
        topic="orders.new",
        action=TimelineAction.RECEIVED,
        transport="kafka",
    )
    matched_entry = TimelineEntry(
        offset_ms=1.0,
        _wall_clock_epoch=0.001,
        topic="orders.new",
        action=TimelineAction.MATCHED,
        transport="kafka",
    )
    result = StageScenarioResult(
        handles=(handle,),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="end-to-end",
        bridge_class="MappedBridge",
        registered_transports=("kafka",),
        timeline=(publish_entry, received_entry, matched_entry),
    )
    collector = _collector_with_scenarios(result)
    out = _to_dict(collector)
    scenario = out["tests"][0]["scenarios"][0]
    actions = [e["action"] for e in scenario["timeline"]]
    assert actions == ["published", "received", "matched"]
    assert all(e["transport"] == "kafka" for e in scenario["timeline"])


def test_a_stage_timeline_should_round_trip_through_the_full_reporter_to_renderer_chain():
    """End-to-end: Phase 1 framework -> Phase 2 reporter -> Phase 2
    renderer. A real `StageScenarioResult` carrying a Stage timeline
    is serialised by the reporter and rendered by `render_html`; the
    inlined JSON in the rendered HTML carries the timeline entries
    in the v1.2 shape with optional fields correctly omitted.

    Closes the integration gap test-quality review flagged: previous
    tests covered framework->reporter or hand-authored-JSON->renderer
    in isolation; this exercises the full chain so a divergence
    between the two boundaries surfaces immediately."""
    import json as _json

    from admiral.stage import StageScenarioResult
    from admiral_reporter._template import render_html
    from bs4 import BeautifulSoup

    publish_entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
    )
    deadline_entry = TimelineEntry(
        offset_ms=200.0,
        _wall_clock_epoch=0.2,
        topic=None,  # scope-level event omits topic per  §D-3
        action=TimelineAction.DEADLINE,
        detail="timeout_ms=200",
    )
    handle = _make_handle(transport="kafka")
    result = StageScenarioResult(
        handles=(handle,),
        passed=True,
        replies=(),
        correlation_id="logical-x",
        name="full-chain",
        bridge_class="MappedBridge",
        registered_transports=("kafka",),
        timeline=(publish_entry, deadline_entry),
    )
    collector = _collector_with_scenarios(result)
    report_dict = _to_dict(collector)
    html = render_html(_json.dumps(report_dict))
    soup = BeautifulSoup(html, "html.parser")
    inlined = _json.loads(soup.find("script", id="harness-results").string)
    timeline = inlined["tests"][0]["scenarios"][0]["timeline"]
    assert [e["action"] for e in timeline] == ["published", "deadline"]
    # Scope-level event: topic key omitted (not null)
    assert "topic" not in timeline[1]
    # Per-transport entry carries transport
    assert timeline[0]["transport"] == "kafka"
    # Scope-level entry omits transport
    assert "transport" not in timeline[1]


def test_a_run_with_stage_scenarios_should_emit_redaction_version():
    """5.1 / §2.4. Consumers detect algorithm changes
    via this string. Only emitted for runs that carry hash-redacted
    wire ids (i.e. any run with a Stage scenario)."""
    handles = (_make_handle(transport="nats"),)
    stage_result = _stage_scenario_result(handles=handles)
    collector = _collector_with_scenarios(stage_result)
    out = _to_dict(collector)
    assert out["run"]["redactions"]["redaction_version"] == "v1"


def test_a_single_harness_run_should_omit_redaction_version_for_byte_identity():
    """Backward-compat: single-Harness runs with no Stage scenarios
    must emit the v1.0 `redactions` shape unchanged so the snapshot
    test (US-6) holds."""
    collector = _collector_with_scenarios()
    out = _to_dict(collector)
    assert "redaction_version" not in out["run"]["redactions"]


def test_a_run_with_only_single_harness_scenarios_should_emit_transport_only():
    handle = Handle(
        topic="t",
        matcher_description="any",
        correlation_id="x",
        outcome=Outcome.PASS,
    )
    handle._latency_ms = 1.0
    sh_result = ScenarioResult(name="sh", correlation_id="x", handles=(handle,), passed=True)
    collector = Collector(transport="MockTransport")
    collector.start_run(0.0)
    collector.record_scenario(sh_result, "t::sh", True)
    collector.finish_run()
    out = _to_dict(collector)
    assert out["run"]["transport"] == "MockTransport"
    assert "transports" not in out["run"]


def test_a_run_with_a_stage_scenario_should_emit_transports_and_null_transport():
    handles = (
        _make_handle(transport="nats", wire_id="n-1"),
        _make_handle(transport="kafka", wire_id="k-1"),
    )
    stage_result = _stage_scenario_result(handles=handles)
    collector = Collector(transport="MockTransport")
    collector.start_run(0.0)
    collector.record_scenario(stage_result, "t::s", True)
    collector.finish_run()
    out = _to_dict(collector)
    assert out["run"]["transport"] is None
    assert out["run"]["transports"] == ["kafka", "nats"]


def test_a_mixed_run_should_emit_both_transport_and_transports():
    """Mixed-mode runs: transport=null, transports=sorted union (
    §1.6 — `populates run.transports with the union of every transport
    name encountered`). The union includes the single-Harness transport
    class name AND every Stage transport, alphabetically sorted."""
    sh_handle = Handle(
        topic="t",
        matcher_description="any",
        correlation_id="x",
        outcome=Outcome.PASS,
    )
    sh_handle._latency_ms = 1.0
    sh_result = ScenarioResult(name="sh", correlation_id="x", handles=(sh_handle,), passed=True)
    stage_handles = (
        _make_handle(transport="nats", wire_id="n-1"),
        _make_handle(transport="kafka", wire_id="k-1"),
    )
    stage_result = _stage_scenario_result(handles=stage_handles)
    collector = Collector(transport="MockTransport")
    collector.start_run(0.0)
    collector.record_scenario(sh_result, "t::sh", True)
    collector.record_scenario(stage_result, "t::s", True)
    collector.finish_run()
    out = _to_dict(collector)
    assert out["run"]["transport"] is None
    assert out["run"]["transports"] == ["MockTransport", "kafka", "nats"]
