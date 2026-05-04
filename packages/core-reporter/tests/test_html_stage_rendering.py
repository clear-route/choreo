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
    """Compact v1.1 Stage report; one Stage scenario, two transports,
    one Stage handle, one cross-transport reply."""
    return {
        "schema_version": "1.1",
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
                        "timeline": [],
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
    payload["tests"][0]["scenarios"][0]["handles"][0]["transport"] = (
        "<script>alert(1)</script>"
    )
    payload["tests"][0]["scenarios"][0]["stage"]["transports"] = [
        "<script>alert(1)</script>"
    ]
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
        assert f'data-stage-status="{status}"' in html, (
            f"missing CSS rule for status {status!r}"
        )


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


def test_the_root_should_carry_data_schema_version_1_1():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".harness-report")
    assert root is not None
    assert root.get("data-schema-version") == "1.1"


def test_the_meta_harness_schema_version_should_be_1_1():
    html = _render(_stage_report_json())
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "harness-schema-version"})
    assert meta is not None
    assert meta.get("content") == "1.1"


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
