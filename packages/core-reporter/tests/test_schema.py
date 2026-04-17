"""JSON Schema tests — PRD-007 §3 / testing strategy.

The schema is the hand-off contract to any future aggregator; these
tests catch drift between the schema file, the appendix example, and
the serialised output.
"""

from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator

APPENDIX_A_EXAMPLE: dict = {
    "schema_version": "1",
    "run": {
        "started_at": "2026-04-17T09:12:04.118000+00:00",
        "finished_at": "2026-04-17T09:12:07.942000+00:00",
        "duration_ms": 3824,
        "totals": {
            "passed": 97,
            "failed": 2,
            "errored": 0,
            "skipped": 0,
            "slow": 1,
            "total": 100,
        },
        "project_name": "choreo",
        "transport": "MockTransport",
        "allowlist_path": "config/allowlist.yaml",
        "python_version": "3.13.0",
        "harness_version": "0.5.1",
        "reporter_version": "0.1.0",
        "git_sha": "a1b2c3d4",
        "git_branch": "feat/reporter",
        "environment": "dev",
        "hostname": "laptop-mkl",
        "xdist": None,
        "truncated": False,
        "redactions": {"fields": 0, "stream_matches": 0},
    },
    "tests": [],
}


def test_the_schema_file_should_load_as_a_valid_draft_2020_12_schema(
    schema: dict,
) -> None:
    Draft202012Validator.check_schema(schema)


def test_the_appendix_a_example_should_validate_against_the_schema(
    schema: dict,
) -> None:
    Draft202012Validator(schema).validate(APPENDIX_A_EXAMPLE)


def test_a_document_missing_schema_version_should_fail_validation(
    schema: dict,
) -> None:
    invalid = deepcopy(APPENDIX_A_EXAMPLE)
    invalid.pop("schema_version")
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(invalid))
    assert any("schema_version" in str(e.message) for e in errors)


def test_a_document_with_the_wrong_major_version_should_fail(
    schema: dict,
) -> None:
    invalid = deepcopy(APPENDIX_A_EXAMPLE)
    invalid["schema_version"] = "2"
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(invalid))
    assert errors, "schema_version must be pinned to '1' for v1"


def test_every_required_object_should_list_its_fields(schema: dict) -> None:
    """Regression guard: if a schema edit drops `required` from an object,
    schema validation would pass for a document missing fields. This test
    asserts the object-schemas we rely on enumerate their required keys."""
    required_present_in = []
    for name, defn in schema["$defs"].items():
        if defn.get("type") == "object":
            assert "required" in defn, f"$defs/{name} must declare required"
            required_present_in.append(name)
    assert "run" in required_present_in
    assert "test" in required_present_in
    assert "scenario" in required_present_in
    assert "handle" in required_present_in
    assert "timeline_entry" in required_present_in


def test_a_scenario_missing_a_handle_field_should_fail_validation(
    schema: dict,
) -> None:
    invalid = deepcopy(APPENDIX_A_EXAMPLE)
    invalid["tests"] = [
        {
            "nodeid": "t.py::t",
            "file": "t.py",
            "name": "t",
            "class": None,
            "markers": [],
            "outcome": "passed",
            "duration_ms": 1,
            "traceback": None,
            "stdout": "",
            "stderr": "",
            "log": "",
            "skip_reason": None,
            "worker_id": None,
            "scenarios": [
                {
                    "name": "s",
                    "correlation_id": "c",
                    "outcome": "pass",
                    "duration_ms": 1,
                    "completed_normally": True,
                    "handles": [
                        {
                            # missing `truncated`
                            "topic": "t",
                            "outcome": "pass",
                            "latency_ms": 1,
                            "budget_ms": None,
                            "matcher_description": "m",
                            "expected": None,
                            "actual": None,
                            "attempts": 0,
                            "reason": "",
                        }
                    ],
                    "timeline": [],
                    "timeline_dropped": 0,
                    "summary_text": "",
                }
            ],
        }
    ]
    errors = list(Draft202012Validator(schema).iter_errors(invalid))
    assert any("truncated" in e.message for e in errors)
