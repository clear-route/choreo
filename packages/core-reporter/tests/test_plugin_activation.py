"""Plugin activation and CLI-surface tests — PRD-007 §1, §6.

Uses pytester to spin up inner pytest sessions so the outer session's
reporter state is isolated.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _run_inner(
    pytester: pytest.Pytester,
    *,
    args: list[str],
) -> tuple[pytest.RunResult, Path]:
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            assert 1 == 1
        """
    )
    report_dir = pytester.path / "report"
    result = pytester.runpytest(f"--harness-report={report_dir}", *args)
    return result, report_dir


def test_running_pytest_with_the_plugin_should_write_results_json(
    pytester: pytest.Pytester,
) -> None:
    result, report_dir = _run_inner(pytester, args=[])
    assert result.ret == 0
    assert (report_dir / "results.json").is_file()
    assert (report_dir / ".harness-report").is_file()


def test_the_report_should_not_be_written_when_disable_flag_is_set(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            assert 1 == 1
        """
    )
    report_dir = pytester.path / "report-should-not-exist"
    result = pytester.runpytest(
        f"--harness-report={report_dir}", "--harness-report-disable"
    )
    assert result.ret == 0
    assert not report_dir.exists()


def test_the_cli_flag_should_override_the_env_var(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = pytester.path / "env-report"
    cli_path = pytester.path / "cli-report"
    monkeypatch.setenv("HARNESS_REPORT_DIR", str(env_path))
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            assert 1 == 1
        """
    )
    result = pytester.runpytest(f"--harness-report={cli_path}")
    assert result.ret == 0
    assert (cli_path / "results.json").is_file()
    assert not env_path.exists()


def test_the_env_var_should_be_used_when_the_cli_flag_is_absent(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = pytester.path / "env-report-used"
    monkeypatch.setenv("HARNESS_REPORT_DIR", str(env_path))
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            assert 1 == 1
        """
    )
    result = pytester.runpytest()
    assert result.ret == 0
    assert (env_path / "results.json").is_file()


def test_the_json_output_should_declare_schema_version_1(
    pytester: pytest.Pytester,
) -> None:
    _, report_dir = _run_inner(pytester, args=[])
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["schema_version"] == "1"


def test_the_json_output_should_record_one_test_record_per_nodeid(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_multi="""
        def test_a():
            assert True
        def test_b():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    result = pytester.runpytest(f"--harness-report={report_dir}")
    assert result.ret == 0
    doc = json.loads((report_dir / "results.json").read_text())
    nodeids = {t["nodeid"] for t in doc["tests"]}
    assert any("test_a" in n for n in nodeids)
    assert any("test_b" in n for n in nodeids)
    assert doc["run"]["totals"]["passed"] >= 2


def test_the_json_output_should_validate_against_the_schema(
    pytester: pytest.Pytester, schema: dict
) -> None:
    from jsonschema import Draft202012Validator

    _, report_dir = _run_inner(pytester, args=[])
    doc = json.loads((report_dir / "results.json").read_text())
    Draft202012Validator(schema).validate(doc)


def test_a_test_that_errors_in_setup_should_be_recorded_as_errored(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_setup_fail="""
        import pytest
        @pytest.fixture
        def broken():
            raise RuntimeError("setup blew up")
        def test_uses_broken(broken):
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    outcomes = {t["outcome"] for t in doc["tests"]}
    assert "errored" in outcomes


def test_a_skipped_test_should_carry_its_skip_reason(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_skip="""
        import pytest
        @pytest.mark.skip(reason="deliberate for test")
        def test_skipped():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    skipped = [t for t in doc["tests"] if t["outcome"] == "skipped"]
    assert skipped
    assert any("deliberate" in (t["skip_reason"] or "") for t in skipped)


def test_the_output_should_include_the_hostname_and_python_version(
    pytester: pytest.Pytester,
) -> None:
    _, report_dir = _run_inner(pytester, args=[])
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["run"]["hostname"]
    assert doc["run"]["python_version"]


def test_a_run_with_zero_collected_tests_should_still_emit_a_valid_report(
    pytester: pytest.Pytester, schema: dict
) -> None:
    from jsonschema import Draft202012Validator

    pytester.makepyfile(
        test_empty="""
        # no tests here
        x = 1
        """
    )
    report_dir = pytester.path / "report"
    pytester.runpytest(f"--harness-report={report_dir}")
    doc = json.loads((report_dir / "results.json").read_text())
    Draft202012Validator(schema).validate(doc)
    assert doc["run"]["totals"]["total"] == 0
