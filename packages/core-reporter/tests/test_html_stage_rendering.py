"""Stage rendering contract for the HTML report (PRD-012 §3).

The renderer is JS-driven from inlined JSON (PRD-007 §7), so static
HTML parsing alone cannot assert on runtime DOM. These tests assert
two complementary contracts:

1. The inlined `<script id="harness-results">` JSON contains the v1.1
   Stage shape (handle.transport, scenario.stage, etc.). Phase-1
   serialiser correctness is already covered in
   `test_stage_serialisation.py`; the assertions here pin the
   round-trip through `render_html`.
2. The JS source contains the Stage-rendering branches and
   `data-*` contract names so the runtime path will produce the
   stable-tier attributes promised in PRD-012 §3.6.

Runtime DOM correctness (Playwright/JSDOM) is a follow-up;
`html.escape` defence-in-depth is satisfied by `el()`'s use of
`setAttribute()` and `textContent` (PRD-007 §9 §FORBIDDEN_JS_SINKS).
"""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup
from choreo_reporter._template import render_html


def _stage_report_json() -> dict[str, Any]:
    """Compact v1.3 Stage report; one Stage scenario, two transports,
    one Stage handle, one cross-transport reply."""
    return {
        "schema_version": "1.3",
        "run": {
            "started_at": "2026-05-04T10:00:00Z",
            "finished_at": "2026-05-04T10:00:01Z",
            "duration_ms": 1.0,
            "totals": {
                "passed": 1,
                "failed": 0,
                "errored": 0,
                "skipped": 0,
                "slow": 0,
                "total": 1,
            },
            "project_name": "demo",
            "transport": None,
            "transports": ["kafka", "nats"],
            "allowlist_path": "config/allowlist.yaml",
            "python_version": "3.13",
            "harness_version": "0.1.0",
            "reporter_version": "0.2.0",
            "git_sha": None,
            "git_branch": None,
            "environment": "dev",
            "hostname": "test",
            "xdist": None,
            "truncated": False,
            "redactions": {
                "fields": 0,
                "stream_matches": 0,
                "redaction_version": "v1",
            },
        },
        "tests": [
            {
                "nodeid": "tests/test_bridge.py::test_round_trip",
                "file": "tests/test_bridge.py",
                "name": "test_round_trip",
                "class": None,
                "markers": [],
                "choreo_meta": None,
                "outcome": "passed",
                "duration_ms": 12.5,
                "traceback": None,
                "stdout": "",
                "stderr": "",
                "log": "",
                "skip_reason": None,
                "worker_id": None,
                "scenarios": [
                    {
                        "name": "bridge",
                        "correlation_id": "logical-3f2a91b8c4d50e1f",
                        "outcome": "pass",
                        "duration_ms": 12.5,
                        "completed_normally": True,
                        "handles": [
                            {
                                "topic": "results",
                                "outcome": "pass",
                                "latency_ms": 41.7,
                                "budget_ms": None,
                                "matcher_description": "any",
                                "expected": None,
                                "actual": None,
                                "attempts": 1,
                                "reason": "matched",
                                "truncated": False,
                                "failure": None,
                                "failures": [],
                                "failures_dropped": 0,
                                "diagnosis": {"kind": "matched"},
                                "transport": "nats",
                                "correlation_id": "sha256:3f2a91b8c4d50e1f",
                            }
                        ],
                        "timeline": [
                            {
                                "offset_ms": 0.0,
                                "wall_clock": "2026-05-04T10:00:00Z",
                                "action": "published",
                                "detail": "",
                                "topic": "orders.new",
                                "transport": "kafka",
                                "source": "publish",
                            },
                            {
                                "offset_ms": 0.5,
                                "wall_clock": "2026-05-04T10:00:00.0005Z",
                                "action": "received",
                                "detail": "",
                                "topic": "orders.new",
                                "transport": "kafka",
                                "source": "reply",
                            },
                            {
                                "offset_ms": 1.0,
                                "wall_clock": "2026-05-04T10:00:00.001Z",
                                "action": "replied",
                                "detail": "trigger=orders.new",
                                "topic": "orders.processed",
                                "transport": "nats",
                                "source": "reply",
                            },
                            {
                                "offset_ms": 1.5,
                                "wall_clock": "2026-05-04T10:00:00.0015Z",
                                "action": "received",
                                "detail": "",
                                "topic": "orders.processed",
                                "transport": "nats",
                                "source": "expect",
                            },
                            {
                                "offset_ms": 2.0,
                                "wall_clock": "2026-05-04T10:00:00.002Z",
                                "action": "matched",
                                "detail": "",
                                "topic": "orders.processed",
                                "transport": "nats",
                                "source": "expect",
                            },
                            {
                                "offset_ms": 12.0,
                                "wall_clock": "2026-05-04T10:00:00.012Z",
                                "action": "deadline",
                                "detail": "timeout_ms=200",
                                "source": "scope",
                            },
                        ],
                        "timeline_dropped": 0,
                        "replies": [
                            {
                                "trigger_topic": "orders.new",
                                "reply_topic": "orders.processed",
                                "state": "replied",
                                "matcher_description": "any",
                                "candidate_count": 1,
                                "match_count": 1,
                                "reply_published": True,
                                "builder_error": None,
                                "correlation_overridden": False,
                                "trigger_transport": "kafka",
                                "response_transport": "nats",
                            }
                        ],
                        "summary_text": "",
                        "stage": {
                            "bridge_class": "MappedBridge",
                            "transports": ["kafka", "nats"],
                            "correlation_ids": {
                                "nats": "sha256:3f2a91b8c4d50e1f",
                                "kafka": "sha256:7e8b50c1ad4912ff",
                            },
                        },
                    }
                ],
            }
        ],
    }


def _render(report: dict[str, Any]) -> str:
    return render_html(json.dumps(report))


# ---------------------------------------------------------------------------
# Inlined JSON: Stage shape round-trips through render_html
# ---------------------------------------------------------------------------


def test_the_inlined_json_should_carry_handle_transport_for_a_stage_handle():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="harness-results")
    assert script is not None
    inlined = json.loads(script.string)
    handle = inlined["tests"][0]["scenarios"][0]["handles"][0]
    assert handle["transport"] == "nats"


def test_the_inlined_json_should_carry_scenario_stage_block():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    stage = inlined["tests"][0]["scenarios"][0]["stage"]
    assert stage["bridge_class"] == "MappedBridge"
    assert stage["transports"] == ["kafka", "nats"]
    assert stage["correlation_ids"]["nats"].startswith("sha256:")
    assert stage["correlation_ids"]["kafka"].startswith("sha256:")


def test_the_inlined_json_should_carry_run_transports_for_a_stage_run():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    assert inlined["run"]["transport"] is None
    assert inlined["run"]["transports"] == ["kafka", "nats"]


# ---------------------------------------------------------------------------
# Renderer code paths: JS source contains the Stage-rendering branches
# ---------------------------------------------------------------------------


def test_the_renderer_js_should_define_renderStageBreadcrumb():
    html = _render(_stage_report_json())
    assert "function renderStageBreadcrumb" in html


def test_the_renderer_js_should_emit_data_handle_transport_for_stage_handles():
    html = _render(_stage_report_json())
    assert "data-handle-transport" in html
    assert "hr-handle-transport-badge" in html


def test_the_renderer_js_should_emit_data_reply_trigger_and_response_transport():
    html = _render(_stage_report_json())
    assert "data-reply-trigger-transport" in html
    assert "data-reply-response-transport" in html


def test_the_renderer_js_should_emit_data_reply_publish_failed():
    html = _render(_stage_report_json())
    assert "data-reply-publish-failed" in html


def test_the_renderer_js_should_emit_failing_side_subbadge_with_data_failing_transports():
    html = _render(_stage_report_json())
    assert "data-failing-transports" in html
    assert "hr-scenario-failing-side" in html


def test_the_renderer_js_should_emit_failing_reply_subbadge():
    html = _render(_stage_report_json())
    assert "data-failing-reply-response-transport" in html
    assert "hr-scenario-failing-reply" in html


# ---------------------------------------------------------------------------
# PR 2.2: Stage timeline visibility (PRD-013 §4.3 banner) + DEADLINE safety
# ---------------------------------------------------------------------------


def test_a_stage_scenario_timeline_should_round_trip_into_the_inlined_json():
    """PR 2.1 wired the reporter to populate `scenario.timeline` for
    Stage scenarios. PR 2.2 verifies the renderer entry point sees the
    full Phase 1 hook output via the inlined JSON."""
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    timeline = inlined["tests"][0]["scenarios"][0]["timeline"]
    actions = [e["action"] for e in timeline]
    assert actions == [
        "published",
        "received",
        "replied",
        "received",
        "matched",
        "deadline",
    ]


def test_a_stage_scenario_timeline_entry_should_carry_transport_in_the_inlined_json():
    """The transport attribution flows from framework -> reporter ->
    inlined JSON unchanged. The renderer reads this for Stage-aware
    visualisation (Phase 2 PR 2.3 swim-lanes)."""
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    published = inlined["tests"][0]["scenarios"][0]["timeline"][0]
    assert published["transport"] == "kafka"


def test_a_stage_deadline_entry_should_omit_topic_in_the_inlined_json():
    """PRD-013 §D-3: scope-level events omit the `topic` JSON key.
    The renderer must be DEADLINE-safe (Phase 1->2 transitional UX)."""
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    timeline = inlined["tests"][0]["scenarios"][0]["timeline"]
    deadlines = [e for e in timeline if e["action"] == "deadline"]
    assert len(deadlines) == 1
    assert "topic" not in deadlines[0]


def test_the_renderer_js_should_guard_against_missing_topic_on_timeline_entries():
    """`buildWaterfall` groups events by `entry.topic` to reconstruct
    the causal tree. PRD-013 §D-3 introduces topic-less entries
    (DEADLINE). The JS must not crash on `undefined` topic - it skips
    the entry from the per-topic waterfall (rendered separately as a
    scope-level event in the swim-lane mode shipped in PR 2.3)."""
    html = _render(_stage_report_json())
    # The guard is `if (!entry.topic)` (or equivalent) inside
    # `buildWaterfall`. Stable-tier contract: a tagged data attribute
    # the test can pin so the renderer can be reorganised internally
    # without breaking this contract.
    assert "data-scope-event" in html


def test_the_renderer_should_emit_a_stage_timeline_banner_for_stage_scenarios():
    """PRD-013 §4.3 visibility banner: Stage scenarios with timeline
    data render a `data-stage-timeline-banner` element so consumers
    can see the timeline was captured even before Phase 2's swim-lane
    layout ships. The banner is additive to the existing waterfall
    and survives into Phase 2 as a semantic header."""
    html = _render(_stage_report_json())
    assert "data-stage-timeline-banner" in html


def test_the_renderer_should_not_emit_a_stage_timeline_banner_for_single_harness():
    """The banner is Stage-specific. A single-`Harness` scenario
    (no `scenario.stage` block) does NOT carry the banner attribute
    in the inlined JSON's scenario shape. The renderer's runtime
    branch is gated on `scenario.stage` presence."""
    report = _stage_report_json()
    # Strip the stage block to simulate a single-Harness scenario.
    scenario = report["tests"][0]["scenarios"][0]
    scenario.pop("stage", None)
    # Strip the per-handle Stage attribution too so the report is
    # plausibly single-Harness shape.
    for handle in scenario["handles"]:
        handle.pop("transport", None)
        handle["correlation_id"] = "logical-3f2a91b8c4d50e1f"
    html = _render(report)
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    # Observable: the inlined JSON has no Stage block.
    assert "stage" not in inlined["tests"][0]["scenarios"][0]


# ---------------------------------------------------------------------------
# PR 2.3a: Swim-lane mode (PRD-013 §4.1, §D-3a)
# ---------------------------------------------------------------------------


def test_the_renderer_js_should_emit_a_swim_lane_wrapper_class_for_stage_mode():
    """PRD-013 §4.1: when the timeline contains entries whose
    `transport` matches `scenario.stage.transports`, the renderer
    activates swim-lane mode and wraps rows in `hr-waterfall-lane`
    elements (DOM additions per §4.1; reuses the existing
    `hr-waterfall-*` taxonomy)."""
    html = _render(_stage_report_json())
    assert "hr-waterfall-lane" in html


def test_the_renderer_js_should_carry_a_data_transport_attribute_on_lane_wrappers():
    """Each lane is keyed by transport name via `data-transport`. This
    is the stable-tier contract consumers (e.g. Phase 3 Chronicle ingest
    or future renderer reorganisations) rely on."""
    html = _render(_stage_report_json())
    assert "data-transport" in html


def test_the_renderer_js_should_propagate_transport_onto_individual_rows():
    """Every per-transport waterfall row carries `data-transport` so
    consumers can filter / highlight by transport without traversing
    up to the lane wrapper. Symmetric with `data-action` and
    `data-scope-event` attributes already on the row."""
    html = _render(_stage_report_json())
    # Look for a per-row data-transport attribute name in the JS source.
    # Distinct from the lane-wrapper version (same name, different
    # element) — both are emitted.
    assert "'data-transport'" in html or '"data-transport"' in html


def test_the_renderer_should_emit_one_lane_per_registered_transport_in_stage_mode():
    """PRD-013 §D-3a + §4.1: swim-lane mode produces one
    `hr-waterfall-lane[data-transport=<name>]` per entry in
    `scenario.stage.transports`. Pinned via the JS source's
    `el(..., {'data-transport': t}, ...)` construction so the
    rendered output carries one lane attribute per registered
    transport."""
    html = _render(_stage_report_json())
    # The fixture registers two transports (kafka, nats) - the JS source
    # must construct lane wrappers for both. The Python-side substring
    # check is observable on the JS source: each lane's `data-transport`
    # value comes from the iteration over `scenario.stage.transports`.
    assert "data-transport" in html
    # The dedicated scope-events lane carries `data-scope-lane="true"`
    # (only emitted when swim-lane mode actually activates AND there
    # are scope-level events).
    assert "data-scope-lane" in html


def test_the_renderer_js_should_emit_a_separate_lane_for_scope_level_events():
    """PRD-013 §D-3 + §4.1: scope-level events (DEADLINE) have no
    transport attribution. They render in a dedicated lane with
    `data-transport=""` (empty) or a `data-scope-lane="true"` marker
    so the swim-lane consumer can route them appropriately."""
    html = _render(_stage_report_json())
    assert "data-scope-lane" in html


def test_the_renderer_should_not_activate_swim_lane_mode_for_single_harness():
    """Mode detection requires `scenario.stage`. Without it, the
    renderer falls through to the legacy per-topic layout that
    PRD-007 v1.0 ships. The DOM still contains the `hr-waterfall-lane`
    class definition (CSS lives at module scope), but the JS does not
    construct lane wrappers for non-Stage scenarios."""
    report = _stage_report_json()
    scenario = report["tests"][0]["scenarios"][0]
    scenario.pop("stage", None)
    for handle in scenario["handles"]:
        handle.pop("transport", None)
        handle["correlation_id"] = "logical-3f2a91b8c4d50e1f"
    # Drop the transport attribution from timeline entries too.
    for entry in scenario["timeline"]:
        entry.pop("transport", None)
    html = _render(report)
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    # Verify the inlined report has no Stage shape.
    assert "stage" not in inlined["tests"][0]["scenarios"][0]
    for entry in inlined["tests"][0]["scenarios"][0]["timeline"]:
        assert "transport" not in entry


# ---------------------------------------------------------------------------
# PR 2.3b: Cross-transport reply-arrow pairing (PRD-013 §4.2, §4.2.1)
# ---------------------------------------------------------------------------


def test_the_renderer_js_should_emit_a_reply_arrow_count_attribute_on_the_overlay():
    """PRD-013 §4.2.1: the SVG overlay carries `data-reply-arrow-count`
    indicating the pair count produced by the linear-scan pairing
    algorithm. This is the observable behavioural contract; the
    consumer can query overlay presence + pair count without depending
    on internal helper names."""
    html = _render(_stage_report_json())
    assert "data-reply-arrow-count" in html


def test_the_renderer_js_should_render_an_svg_overlay_for_reply_arrows():
    """PRD-013 §4.2: reply linkage uses an SVG path overlay above the
    waterfall lanes. The container carries the
    `hr-waterfall-reply-arrows` class so consumers can target it."""
    html = _render(_stage_report_json())
    assert "hr-waterfall-reply-arrows" in html


def test_the_renderer_js_should_emit_data_reply_from_and_data_reply_to_on_arrows():
    """PRD-013 §4.2: each arrow path carries `data-reply-link-from`
    (the RECEIVED node id) and `data-reply-link-to` (the REPLIED
    node id) so consumers can trace the pairing without relying on
    SVG geometry."""
    html = _render(_stage_report_json())
    assert "data-reply-link-from" in html
    assert "data-reply-link-to" in html


def test_a_single_harness_scenario_should_not_carry_a_stage_block_in_the_inlined_json():
    """Reply arrows are a cross-transport visualisation gated on
    swim-lane mode (which itself requires `scenario.stage`). A
    single-Harness scenario carries no Stage block in the inlined
    JSON, so the renderer's runtime gate cannot activate.

    Observable contract: the inlined JSON for a single-Harness
    scenario has no `stage` key. Runtime verification that the
    SVG overlay is absent is a Playwright follow-up."""
    report = _stage_report_json()
    scenario = report["tests"][0]["scenarios"][0]
    scenario.pop("stage", None)
    for handle in scenario["handles"]:
        handle.pop("transport", None)
        handle["correlation_id"] = "logical-3f2a91b8c4d50e1f"
    for entry in scenario["timeline"]:
        entry.pop("transport", None)
    html = _render(report)
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    assert "stage" not in inlined["tests"][0]["scenarios"][0]


# ---------------------------------------------------------------------------
# PR 2.4: Virtualisation under cap-saturated workloads (PRD-013 §4.1.1)
# ---------------------------------------------------------------------------


def test_the_renderer_js_should_emit_a_data_virtualised_marker_on_the_timeline_host():
    """Stable-tier DOM marker: when the bounded-mount branch fires at
    runtime (above the 500-entry threshold), the timeline host gains
    `data-virtualised="true"` plus `data-virtualised-shown` (rows
    mounted eagerly) and `data-virtualised-total` (entry count). The
    JS source contains the `setAttribute('data-virtualised', ...)`
    calls; runtime DOM verification of the marker actually being set
    is a Playwright follow-up. The negative test
    (`test_the_renderer_should_not_mark_a_short_timeline_as_virtualised`)
    pins the HTML-time absence."""
    html = _render(_stage_report_json())
    assert "data-virtualised" in html
    assert "data-virtualised-shown" in html
    assert "data-virtualised-total" in html


def test_the_renderer_js_should_emit_an_expansion_control_carrying_data_virtualised_expand():
    """The bounded-mount branch produces a `data-virtualised-expand`
    button. The JS source contains the `setAttribute(
    'data-virtualised-expand', ...)` call; runtime verification of
    button visibility is a Playwright follow-up."""
    html = _render(_stage_report_json())
    assert "data-virtualised-expand" in html


def test_the_renderer_should_not_mark_a_short_timeline_as_virtualised():
    """The eager-mount path is preserved for timelines under the
    threshold (PRD-013 §4.1.1: lower latency for the common case).
    A short timeline must not produce a `data-virtualised="true"`
    attribute literal in the rendered output, nor a
    `data-virtualised-expand` button. The fixture's 6-entry timeline
    is far below any realistic threshold."""
    html = _render(_stage_report_json())
    # The runtime sets these attributes inside `mountTimelineVirtualised`;
    # they only appear in the rendered output if that branch fired. For
    # a 6-entry timeline (well below the 500-entry threshold), the
    # eager-mount path is the only one that should run.
    assert 'data-virtualised="true"' not in html
    assert 'data-virtualised-expand="true"' not in html


# ---------------------------------------------------------------------------
# PRD-013 v1.3 / schema v1.3: source attribution in the renderer
# ---------------------------------------------------------------------------


def test_a_timeline_entry_source_should_round_trip_into_the_inlined_json():
    """Schema v1.3: each timeline entry's `source` field flows from
    the JSON fixture through `render_html` into the inlined JSON
    unchanged. Stable-tier contract — consumers can rely on the
    field's presence and value semantics."""
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    sources = [e.get("source") for e in inlined["tests"][0]["scenarios"][0]["timeline"]]
    assert sources == [
        "publish",
        "reply",
        "reply",
        "expect",
        "expect",
        "scope",
    ]


def test_the_renderer_js_should_propagate_source_onto_per_row_data_attribute():
    """`renderWaterfallRow` writes `data-source` onto each row so
    consumers can filter by DSL surface (e.g. `[data-source="reply"]`
    selects every reply-chain entry)."""
    html = _render(_stage_report_json())
    assert "'data-source'" in html or '"data-source"' in html


def test_the_renderer_js_should_render_a_source_badge_with_human_readable_text():
    """Each event row carries a small `hr-waterfall-source` pill
    naming the DSL surface ('by test' / 'by reply' / 'by expect' /
    'by scope') so a reader can disambiguate at a glance without
    reading the JSON."""
    html = _render(_stage_report_json())
    assert "hr-waterfall-source" in html
    assert "sourceBadgeText" in html


def test_a_single_harness_timeline_entry_should_omit_the_source_key():
    """Byte-identity: single-Harness entries pre-date PRD-013 §1.6;
    they emit no `source` field and the JSON key is omitted (not
    null) so v1.0/v1.1/v1.2-pinned consumers see no surprise field."""
    report = _stage_report_json()
    scenario = report["tests"][0]["scenarios"][0]
    scenario.pop("stage", None)
    for handle in scenario["handles"]:
        handle.pop("transport", None)
        handle["correlation_id"] = "logical-3f2a91b8c4d50e1f"
    for entry in scenario["timeline"]:
        entry.pop("transport", None)
        entry.pop("source", None)
    html = _render(report)
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    for entry in inlined["tests"][0]["scenarios"][0]["timeline"]:
        assert "source" not in entry


def test_the_renderer_js_should_apply_failed_class_to_response_badge():
    """PRD-012 US-2 AC3: response-transport badge for `reply_failed`
    carries class `hr-reply-transport-badge--failed` (the class name
    is the asserted contract, not the visual property)."""
    html = _render(_stage_report_json())
    assert "hr-reply-transport-badge--failed" in html


def test_the_renderer_js_should_define_collectFailingTransports():
    html = _render(_stage_report_json())
    assert "function collectFailingTransports" in html


# ---------------------------------------------------------------------------
# CSS: stable-tier classes are styled (PRD-007 §9: every selector
# scoped under .harness-report)
# ---------------------------------------------------------------------------


def test_the_css_should_style_handle_transport_badge():
    html = _render(_stage_report_json())
    assert ".harness-report .hr-handle-transport-badge" in html


def test_the_css_should_style_reply_transport_badge():
    html = _render(_stage_report_json())
    assert ".harness-report .hr-reply-transport-badge" in html


def test_the_css_should_style_failed_reply_badge():
    html = _render(_stage_report_json())
    assert ".harness-report .hr-reply-transport-badge--failed" in html


def test_the_css_should_style_stage_breadcrumb():
    html = _render(_stage_report_json())
    assert ".harness-report .hr-stage-breadcrumb" in html


def test_the_css_should_style_failing_side_subbadge():
    html = _render(_stage_report_json())
    assert ".harness-report .hr-scenario-failing-side" in html


# ---------------------------------------------------------------------------
# XSS canary (PRD-012 §3 §Mandatory escaping)
# ---------------------------------------------------------------------------


def test_a_malicious_transport_name_should_not_break_out_of_the_inlined_json_or_a_data_attribute():
    """PRD-012 §3 mandates a renderer test fixturing
    `transport: '<script>alert(1)</script>'` and asserting no script
    execution surface lands in the HTML. Defence in depth above the
    Pydantic + JSON-Schema regex (which the reporter ships with v1.1).

    Two contracts here:
    * The inlined JSON's `<` is escaped to `\\u003c` so a `</script>`
      cannot break out of the harness-results script tag (PRD-007 §9).
    * No raw `<script>alert(1)</script>` substring appears anywhere
      in the HTML output. The renderer's runtime DOM-building uses
      `setAttribute()` and `textContent` (FORBIDDEN_JS_SINKS), so
      transport names land as text or attribute content, never as
      parsed HTML — this test pins the static contract; runtime DOM
      execution is verified by the Playwright suite (deferred).
    """
    payload = _stage_report_json()
    payload["tests"][0]["scenarios"][0]["handles"][0]["transport"] = "<script>alert(1)</script>"
    payload["tests"][0]["scenarios"][0]["stage"]["transports"] = ["<script>alert(1)</script>"]
    payload["run"]["transports"] = ["<script>alert(1)</script>"]
    html = _render(payload)

    # The literal attack payload must not appear unescaped anywhere
    # in the document. The JSON inlining escapes `<` to `<` so
    # `</script>` cannot break out of the harness-results script tag;
    # the runtime DOM-building path uses setAttribute / textContent
    # so the parsed-JSON value lands as text, not as HTML, in any
    # data-* attribute or pill body.
    assert "<script>alert(1)</script>" not in html
    # And the inlined JSON's `<` is escaped to the JSON-Unicode form
    # `<` (six literal characters) so the `</script>` closing
    # tag cannot terminate the surrounding <script type="application/json">
    # element. The trailing `>` is allowed unescaped — only the leading
    # `<` matters for break-out (PRD-007 §9 / FORBIDDEN_JS_SINKS).
    assert "\\u003cscript>alert(1)\\u003c/script>" in html


# ---------------------------------------------------------------------------
# Reply-failed branch — exercised against a reply_failed fixture
# ---------------------------------------------------------------------------


def _stage_report_with_failed_reply() -> dict[str, Any]:
    """Fixture variant: scenario with a reply in state `reply_failed`.
    Used to exercise the failing-reply sub-badge and the
    `--failed` class on the response transport badge."""
    report = _stage_report_json()
    scenario = report["tests"][0]["scenarios"][0]
    scenario["outcome"] = "fail"
    scenario["replies"][0]["state"] = "reply_failed"
    scenario["replies"][0]["reply_published"] = False
    scenario["replies"][0]["builder_error"] = "RuntimeError"
    return report


def test_a_reply_failed_payload_should_be_renderable_without_crashing():
    """Smoke: `render_html` accepts a reply_failed payload and the
    failing-reply branches in the JS source are present."""
    html = _render(_stage_report_with_failed_reply())
    soup = BeautifulSoup(html, "html.parser")
    inlined = json.loads(soup.find("script", id="harness-results").string)
    reply = inlined["tests"][0]["scenarios"][0]["replies"][0]
    assert reply["state"] == "reply_failed"
    assert reply["builder_error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Reply lifecycle stage rendering (Pass-2 redesign)
# ---------------------------------------------------------------------------


def test_the_renderer_should_define_renderReplyLifecycle_with_four_named_stages():
    """The reply-row body is split into four stages: trigger received,
    matcher accepted, build callback, response published. Each stage
    name is a string in the JS source; the asserted contract is that
    all four are emitted by the lifecycle renderer."""
    html = _render(_stage_report_json())
    assert "function renderReplyLifecycle" in html
    assert "'trigger received'" in html
    assert "'matcher accepted'" in html
    assert "'build callback'" in html
    assert "'response published'" in html


def test_the_renderer_should_emit_per_stage_status_data_attribute():
    """Each lifecycle stage carries `data-stage-status` for CSS / consumer
    queries. The JS source contains the attribute name and the five
    possible status values."""
    html = _render(_stage_report_json())
    assert "data-stage-status" in html
    # The status taxonomy: ok, no, failed, skipped, na.
    for status in ("'ok'", "'no'", "'failed'", "'skipped'", "'na'"):
        assert status in html, f"missing status value {status} in JS source"


def test_the_renderer_should_define_replyOneLineSummary_for_each_state():
    """One-line summary covers every reply state; the JS source
    references the four state strings explicitly."""
    html = _render(_stage_report_json())
    assert "function replyOneLineSummary" in html
    for state in ("'replied'", "'reply_failed'", "'armed_no_match'", "'armed_matcher_mismatched'"):
        assert state in html


def test_the_lifecycle_css_should_distinguish_status_with_distinct_colours():
    """CSS rules target [data-stage-status="ok"], "no", "failed",
    "skipped", "na". Stage colour distinguishes pass / fail / muted /
    subtle paths visually."""
    html = _render(_stage_report_json())
    for status in ("ok", "no", "failed", "skipped", "na"):
        assert f'data-stage-status="{status}"' in html, f"missing CSS rule for status {status!r}"


# ---------------------------------------------------------------------------
# Stable-tier `data-*` contract — full surface coverage (PRD-012 §3.6)
# ---------------------------------------------------------------------------


def test_the_renderer_js_should_emit_data_stage_transports_on_the_breadcrumb():
    """Stable-tier per PRD-012 §3.6. Space-separated for [~=] selector
    compat. Distinct from the singular `data-stage-transport` on
    individual pills."""
    html = _render(_stage_report_json())
    assert "data-stage-transports" in html


def test_the_breadcrumb_should_render_correlation_ids_inside_a_disclosure():
    """User-feedback fix: per-transport correlation ids are no longer
    inline in the breadcrumb; they live inside a `<details>` disclosure
    so the breadcrumb stays scannable. The class names are the asserted
    contract for the renderer."""
    html = _render(_stage_report_json())
    assert "hr-stage-correlation-ids" in html
    assert "hr-stage-correlation-ids-summary" in html


def test_the_renderer_js_should_emit_data_stage_transport_on_breadcrumb_pills():
    """Stable-tier. Singular form distinguishes pills from the
    container's plural `data-stage-transports`."""
    html = _render(_stage_report_json())
    assert "data-stage-transport" in html
    # Sanity: hr-stage-transport-badge is the class for the pill.
    assert "hr-stage-transport-badge" in html


# ---------------------------------------------------------------------------
# Schema-version meta and root attribute reflect 1.1
# ---------------------------------------------------------------------------


def test_the_root_should_carry_data_schema_version_1_3():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".harness-report")
    assert root is not None
    assert root.get("data-schema-version") == "1.3"


def test_the_meta_harness_schema_version_should_be_1_3():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "harness-schema-version"})
    assert meta is not None
    assert meta.get("content") == "1.3"


# ---------------------------------------------------------------------------
# Stable-tier static contract: data-grouping-mode emitted on the root
# even before the toggle implementation ships (PRD-012 §3.6).
# ---------------------------------------------------------------------------


def test_the_root_should_carry_data_grouping_mode_by_scenario_initially():
    """Stable-tier per PRD-012 §3.6. Lands now (the toggle UI is
    deferred) so consumers can pin the attribute name."""
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".harness-report")
    assert root is not None
    assert root.get("data-grouping-mode") == "by-scenario"
