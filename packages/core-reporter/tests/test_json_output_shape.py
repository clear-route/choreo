"""JSON output structure — PRD-007 §3.

Drives the plugin with a pytester inner session that actually uses the
harness, so the output contains scenarios, handles, and timelines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _run_harness_session(pytester: pytest.Pytester, tiny_test_module: str, args: list[str]) -> Path:
    pytester.makepyfile(test_fill=tiny_test_module)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}", *args)
    return report_dir


def test_a_passing_scenario_should_appear_in_the_test_record(
    pytester: pytest.Pytester, tiny_test_module: str
) -> None:
    report_dir = _run_harness_session(pytester, tiny_test_module, [])
    doc = json.loads((report_dir / "results.json").read_text())
    tests = [t for t in doc["tests"] if "test_a_scenario_passes" in t["nodeid"]]
    assert len(tests) == 1
    test = tests[0]
    assert len(test["scenarios"]) == 1
    scenario = test["scenarios"][0]
    assert scenario["name"] == "fill"
    assert scenario["outcome"] == "pass"
    assert scenario["completed_normally"] is True


def test_a_passing_scenarios_handles_should_carry_matcher_metadata(
    pytester: pytest.Pytester, tiny_test_module: str
) -> None:
    report_dir = _run_harness_session(pytester, tiny_test_module, [])
    doc = json.loads((report_dir / "results.json").read_text())
    scenario = doc["tests"][0]["scenarios"][0]
    handle = scenario["handles"][0]
    assert handle["outcome"] == "pass"
    assert handle["topic"] == "t.test"
    assert handle["matcher_description"]
    assert handle["expected"] == {"k": "v"}


def test_a_failing_scenario_should_be_reported_with_fail_outcome(
    pytester: pytest.Pytester,
) -> None:
    module_src = """
import pytest_asyncio
from choreo import Harness
from choreo.matchers import field_equals
from choreo.transports import MockTransport


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text("mock_endpoints: [\\"mock://localhost\\"]\\n")
    transport = MockTransport(allowlist_path=alist, endpoint="mock://localhost")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_a_scenario_fails(harness):
    async with harness.scenario("bad") as s:
        s.expect("t.bad", field_equals("status", "ACCEPTED"))
        s = s.publish("t.bad", {"status": "REJECTED"})
        result = await s.await_all(timeout_ms=50)
    assert result.passed is False
"""
    pytester.makepyfile(test_failing=module_src)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    scenarios = [s for t in doc["tests"] for s in t["scenarios"] if s["name"] == "bad"]
    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario["outcome"] == "fail"
    handle = scenario["handles"][0]
    assert handle["outcome"] == "fail"
    assert handle["actual"]["status"] == "REJECTED"
    assert handle["expected"] == {"status": "ACCEPTED"}

    # Structured failure replaces the free-form `reason` string. The UI
    # renders from `failure` + `failures` + `diagnosis`; `reason` is
    # transitional and removed in the breaking commit (design doc step 5).
    assert handle["failure"] == {
        "kind": "mismatch",
        "path": "status",
        "expected": "ACCEPTED",
        "actual": "REJECTED",
    }
    assert handle["failures"] == [handle["failure"]]
    assert handle["failures_dropped"] == 0
    assert handle["diagnosis"]["kind"] == "near_miss"
    assert handle["diagnosis"]["attempts"] == 1


def test_a_silent_timeout_should_carry_a_silent_timeout_diagnosis(
    pytester: pytest.Pytester,
) -> None:
    """No message on the scope's correlation → `diagnosis.kind == silent_timeout`.
    Confirms the TIMEOUT-vs-FAIL distinction the README promises is expressed
    in the report data, not inferred from prose."""
    module_src = """
import pytest_asyncio
from choreo import Harness
from choreo.matchers import field_equals
from choreo.transports import MockTransport


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text("mock_endpoints: [\\"mock://localhost\\"]\\n")
    transport = MockTransport(allowlist_path=alist, endpoint="mock://localhost")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_silent(harness):
    async with harness.scenario("silent") as s:
        s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("somewhere.else", b"x")
        result = await s.await_all(timeout_ms=30)
    assert result.passed is False
"""
    pytester.makepyfile(test_silent=module_src)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    scenario = next(s for t in doc["tests"] for s in t["scenarios"] if s["name"] == "silent")
    handle = scenario["handles"][0]
    assert handle["outcome"] == "timeout"
    assert handle["failure"] is None
    assert handle["failures"] == []
    assert handle["diagnosis"]["kind"] == "silent_timeout"
    assert handle["diagnosis"]["topic"] == "never.arrives"


def test_run_totals_should_sum_the_test_outcomes(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_totals="""
        import pytest
        def test_a():
            assert True
        def test_b():
            assert True
        def test_c():
            assert False
        @pytest.mark.skip
        def test_d():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    totals = doc["run"]["totals"]
    assert totals["passed"] == 2
    assert totals["failed"] == 1
    assert totals["skipped"] == 1
    assert totals["total"] == 4


def test_a_scenario_with_replies_should_carry_reply_reports_and_timeline_events(
    pytester: pytest.Pytester,
) -> None:
    """PRD-008 / ADR-0017: reply reports must flow through the JSON
    alongside handles, and reply lifecycle events must appear on the
    scope's timeline so the HTML renders them inline."""
    module_src = """
import pytest_asyncio
from choreo import Harness
from choreo.matchers import field_equals
from choreo.transports import MockTransport


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text("mock_endpoints: [\\"mock://localhost\\"]\\n")
    transport = MockTransport(allowlist_path=alist, endpoint="mock://localhost")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_reply_flow(harness):
    async with harness.scenario("reply-scenario") as s:
        s.expect("reply.t", field_equals("echo", 42))
        s.on("trigger.t").publish(
            "reply.t",
            lambda message_received: {"echo": message_received["v"]},
        )
        s = s.publish("trigger.t", {"v": 42})
        result = await s.await_all(timeout_ms=100)
    assert result.passed is True
"""
    pytester.makepyfile(test_reply_flow=module_src)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    scenario = next(
        s for t in doc["tests"] for s in t["scenarios"] if s["name"] == "reply-scenario"
    )

    # Reply report appears in the JSON.
    assert len(scenario["replies"]) == 1
    r = scenario["replies"][0]
    assert r["trigger_topic"] == "trigger.t"
    assert r["reply_topic"] == "reply.t"
    assert r["state"] == "replied"
    assert r["match_count"] == 1
    assert r["candidate_count"] == 1
    assert r["reply_published"] is True
    assert r["correlation_overridden"] is False
    assert r["builder_error"] is None
    assert r["matcher_description"]  # non-empty

    # Timeline captures the reply event even though the scenario passed.
    actions = [e["action"] for e in scenario["timeline"]]
    assert "replied" in actions


def test_a_scenario_with_a_reply_builder_error_should_report_reply_failed(
    pytester: pytest.Pytester,
) -> None:
    module_src = """
import pytest_asyncio
from choreo import Harness
from choreo.transports import MockTransport


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text("mock_endpoints: [\\"mock://localhost\\"]\\n")
    transport = MockTransport(allowlist_path=alist, endpoint="mock://localhost")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


def bad(message_received):
    raise ValueError("boom")


async def test_reply_builder_error(harness):
    async with harness.scenario("reply-err-scenario") as s:
        s.on("trigger.t").publish("reply.t", bad)
        s = s.publish("trigger.t", {})
        await s.await_all(timeout_ms=100)
"""
    pytester.makepyfile(test_reply_err=module_src)
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    scenario = next(
        s for t in doc["tests"] for s in t["scenarios"] if s["name"] == "reply-err-scenario"
    )

    r = scenario["replies"][0]
    assert r["state"] == "reply_failed"
    assert r["builder_error"] == "ValueError"
    assert r["reply_published"] is False

    actions = [e["action"] for e in scenario["timeline"]]
    assert "reply_failed" in actions


def test_the_project_name_should_default_to_the_rootdir_basename(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_simple="""
        def test_a():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["run"]["project_name"] == pytester.path.name


def test_the_project_name_flag_should_override_the_default(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_simple="""
        def test_a():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(
        f"--harness-report={report_dir}",
        "--harness-report-project-name=choreo",
    )
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["run"]["project_name"] == "choreo"


def test_the_project_name_env_var_should_apply_when_no_flag_is_given(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_PROJECT_NAME", "my-service")
    pytester.makepyfile(
        test_simple="""
        def test_a():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["run"]["project_name"] == "my-service"


def test_markers_should_be_serialised_for_filtering(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_markers="""
        import pytest

        @pytest.mark.smoke
        def test_smoke_one():
            assert True

        @pytest.mark.slow_path
        def test_slow_one():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    markers_per_test = {t["name"]: set(t["markers"]) for t in doc["tests"]}
    assert "smoke" in markers_per_test["test_smoke_one"]
    assert "slow_path" in markers_per_test["test_slow_one"]
