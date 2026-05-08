"""Schema validation tests for `test-report-v1.1.json`.

Asserts the additive evolution from v1.0 to v1.1 holds at the schema
level: every v1.0 report (after `schema_version` substitution) is
v1.1-valid; v1.1 reports carrying Stage fields are rejected by the
v1.0 schema (so strict-validator consumers can detect the bump);
`anyOf` constraint covers transport / transports presence; transport
names are regex-constrained; redaction_version is recorded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "schemas"
_V1_0_PATH = _SCHEMA_DIR / "test-report-v1.0.json"
_V1_1_PATH = _SCHEMA_DIR / "test-report-v1.1.json"


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Test fixtures (minimal valid reports)
# ---------------------------------------------------------------------------


def _minimal_run() -> dict[str, Any]:
    """A minimal v1.0-valid `run` dict."""
    return {
        "started_at": "2026-05-04T10:00:00Z",
        "finished_at": "2026-05-04T10:00:01Z",
        "duration_ms": 1.0,
        "totals": {
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "skipped": 0,
            "slow": 0,
            "total": 0,
        },
        "project_name": "demo",
        "transport": "MockTransport",
        "allowlist_path": "config/allowlist.yaml",
        "python_version": "3.13",
        "harness_version": "0.1.0",
        "reporter_version": "0.1.0",
        "git_sha": None,
        "git_branch": None,
        "environment": "dev",
        "hostname": "test-host",
        "xdist": None,
        "truncated": False,
        "redactions": {"fields": 0, "stream_matches": 0},
    }


def _minimal_report(schema_version: str, run: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": schema_version, "run": run, "tests": []}


def _stage_scenario() -> dict[str, Any]:
    """A scenario carrying a Stage block — for v1.1 validation."""
    return {
        "name": "bridge",
        "correlation_id": "logical-3f2a91b8c4d50e1f",
        "outcome": "pass",
        "duration_ms": 12.5,
        "completed_normally": True,
        "handles": [
            {
                "topic": "results",
                "outcome": "pass",
                "latency_ms": 5.0,
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
            }
        ],
        "timeline": [],
        "timeline_dropped": 0,
        "replies": [],
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


def _stage_test() -> dict[str, Any]:
    return {
        "nodeid": "tests/test_bridge.py::test_round_trip",
        "file": "tests/test_bridge.py",
        "name": "test_round_trip",
        "class": None,
        "markers": [],
        "admiral_meta": None,
        "outcome": "passed",
        "duration_ms": 12.5,
        "traceback": None,
        "stdout": "",
        "stderr": "",
        "log": "",
        "skip_reason": None,
        "worker_id": None,
        "scenarios": [_stage_scenario()],
    }


# ---------------------------------------------------------------------------
# Schema document is well-formed
# ---------------------------------------------------------------------------


def test_v1_1_schema_should_be_valid_json_schema_draft_2020_12():
    schema = _load(_V1_1_PATH)
    jsonschema.Draft202012Validator.check_schema(schema)


def test_v1_1_schema_should_declare_schema_version_1_1():
    schema = _load(_V1_1_PATH)
    assert schema["properties"]["schema_version"]["const"] == "1.1"


# ---------------------------------------------------------------------------
# Backward compatibility: v1.0-valid → v1.1-valid (after version substitution)
# ---------------------------------------------------------------------------


def test_a_v1_0_valid_report_should_validate_against_v1_1_after_version_substitution():
    schema = _load(_V1_1_PATH)
    report = _minimal_report("1.1", _minimal_run())
    jsonschema.validate(report, schema)


def test_a_v1_1_report_with_stage_block_should_fail_against_v1_0_schema():
    """v1.0's `additionalProperties: false` rejects the new `stage` block.
    Strict-validator consumers can detect the bump deterministically."""
    schema_v1_0 = _load(_V1_0_PATH)
    run = _minimal_run()
    report = {
        "schema_version": "1",  # v1.0 schema still accepts only "1"
        "run": run,
        "tests": [_stage_test()],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema_v1_0)


# ---------------------------------------------------------------------------
# anyOf constraint: at least one of transport / transports
# ---------------------------------------------------------------------------


def test_a_v1_1_run_missing_both_transport_and_transports_should_fail_validation():
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    del run["transport"]
    report = _minimal_report("1.1", run)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_a_v1_1_run_with_only_transports_should_validate():
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    del run["transport"]
    run["transports"] = ["nats", "kafka"]
    report = _minimal_report("1.1", run)
    jsonschema.validate(report, schema)


def test_a_v1_1_run_with_both_transport_and_transports_should_validate():
    """Mixed-mode runs emit both: transport=null + transports=[union]."""
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    run["transport"] = None
    run["transports"] = ["kafka", "nats"]
    report = _minimal_report("1.1", run)
    jsonschema.validate(report, schema)


# ---------------------------------------------------------------------------
# Transport-name regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "<script>",
        "abc def",  # space
        "abc/def",  # slash
        "",  # empty
        "x" * 65,  # too long
        "abc.def",  # dot
    ],
)
def test_a_v1_1_run_with_a_malformed_transport_name_should_fail_validation(
    bad_name: str,
) -> None:
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    del run["transport"]
    run["transports"] = [bad_name]
    report = _minimal_report("1.1", run)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


# ---------------------------------------------------------------------------
# scenario.correlation_id null
# ---------------------------------------------------------------------------


def test_a_v1_1_scenario_with_a_null_correlation_id_should_validate():
    """v1.0 declared `correlation_id` non-nullable; v1.1 relaxes."""
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    test = _stage_test()
    test["scenarios"][0]["correlation_id"] = None
    report = {"schema_version": "1.1", "run": run, "tests": [test]}
    jsonschema.validate(report, schema)


# ---------------------------------------------------------------------------
# Stage scenario validates
# ---------------------------------------------------------------------------


def test_a_v1_1_report_with_a_stage_scenario_should_validate():
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    run["transport"] = None
    run["transports"] = ["kafka", "nats"]
    report = {"schema_version": "1.1", "run": run, "tests": [_stage_test()]}
    jsonschema.validate(report, schema)


def test_a_stage_block_missing_required_subfield_should_fail_validation():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    del test["scenarios"][0]["stage"]["correlation_ids"]
    report = {
        "schema_version": "1.1",
        "run": _minimal_run(),
        "tests": [test],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_a_stage_block_with_a_malformed_bridge_class_should_fail_validation():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    test["scenarios"][0]["stage"]["bridge_class"] = "<script>"
    report = {
        "schema_version": "1.1",
        "run": _minimal_run(),
        "tests": [test],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


def test_a_stage_correlation_id_not_in_sha256_form_should_fail_validation():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    test["scenarios"][0]["stage"]["correlation_ids"]["nats"] = "kafka-3f2a91b8c4d50e1f"
    report = {
        "schema_version": "1.1",
        "run": _minimal_run(),
        "tests": [test],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


# ---------------------------------------------------------------------------
# Reply transports + topic mapping
# ---------------------------------------------------------------------------


def test_a_reply_report_should_accept_optional_trigger_and_response_transports():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    test["scenarios"][0]["replies"] = [
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
    ]
    report = {
        "schema_version": "1.1",
        "run": _minimal_run(),
        "tests": [test],
    }
    jsonschema.validate(report, schema)


def test_a_reply_report_state_should_not_introduce_fired_or_fired_builder_error():
    """ §1.3 — no enum extension. The wire format keeps the
    v1.0 four values; framework `StageReplyState.FIRED` maps to
    `"replied"`."""
    schema = _load(_V1_1_PATH)
    enum = schema["$defs"]["reply_report"]["properties"]["state"]["enum"]
    assert sorted(enum) == sorted(
        ["armed_no_match", "armed_matcher_mismatched", "replied", "reply_failed"]
    )


# ---------------------------------------------------------------------------
# redaction_version
# ---------------------------------------------------------------------------


def test_a_v1_1_run_should_accept_a_redaction_version_field():
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    run["redactions"]["redaction_version"] = "v1"
    report = _minimal_report("1.1", run)
    jsonschema.validate(report, schema)


@pytest.mark.parametrize("bad_value", ["version-1", "1", "v", "v1.0", "vee1"])
def test_a_redaction_version_violating_the_pattern_should_fail_validation(
    bad_value: str,
) -> None:
    schema = _load(_V1_1_PATH)
    run = _minimal_run()
    run["redactions"]["redaction_version"] = bad_value
    report = _minimal_report("1.1", run)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(report, schema)


# ---------------------------------------------------------------------------
# handle.transport optional
# ---------------------------------------------------------------------------


def test_a_v1_1_handle_with_a_transport_should_validate():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    # Default _stage_test() already sets handle.transport='nats'.
    jsonschema.validate(
        {"schema_version": "1.1", "run": _minimal_run(), "tests": [test]},
        schema,
    )


def test_a_v1_1_handle_without_a_transport_should_validate_for_single_harness_paths():
    schema = _load(_V1_1_PATH)
    test = _stage_test()
    del test["scenarios"][0]["handles"][0]["transport"]
    del test["scenarios"][0]["stage"]  # single-Harness scenarios have no stage
    jsonschema.validate(
        {"schema_version": "1.1", "run": _minimal_run(), "tests": [test]},
        schema,
    )
