"""pytest-xdist support — PRD-007 §1 / User Story 5.

The merge logic is unit-tested here with hand-written partials to
exercise the controller path. A sanity end-to-end with `-n 2` at the
bottom runs a real xdist session to catch controller/worker wiring
regressions the hand-written partials cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from choreo_reporter._xdist import (
    PARTIAL_SUBDIR,
    cleanup_partial_dir,
    merge_partials,
    write_partial,
)


def _seed_worker(report_dir: Path, worker_id: str, tests: list[dict]) -> None:
    write_partial(
        report_dir,
        worker_id,
        {
            "schema_version": "1",
            "run": {"workers": 2, "incomplete_workers": []},
            "tests": tests,
        },
    )


def test_merging_two_worker_partials_should_concatenate_their_tests(
    tmp_path: Path,
) -> None:
    _seed_worker(tmp_path, "gw0", [{"nodeid": "t::a", "outcome": "passed"}])
    _seed_worker(tmp_path, "gw1", [{"nodeid": "t::b", "outcome": "failed"}])

    merge = merge_partials(tmp_path, expected_workers=["gw0", "gw1"])
    nodeids = {t["nodeid"] for t in merge.merged_tests}
    assert nodeids == {"t::a", "t::b"}
    assert merge.incomplete_workers == []


def test_a_missing_worker_partial_should_appear_in_incomplete_workers(
    tmp_path: Path,
) -> None:
    _seed_worker(tmp_path, "gw0", [{"nodeid": "t::a", "outcome": "passed"}])
    merge = merge_partials(tmp_path, expected_workers=["gw0", "gw1"])
    assert merge.incomplete_workers == ["gw1"]


def test_cleanup_should_remove_the_partial_subdirectory(
    tmp_path: Path,
) -> None:
    _seed_worker(tmp_path, "gw0", [])
    assert (tmp_path / PARTIAL_SUBDIR).is_dir()
    cleanup_partial_dir(tmp_path)
    assert not (tmp_path / PARTIAL_SUBDIR).exists()


def test_a_corrupt_partial_file_should_be_skipped_not_fatal(
    tmp_path: Path,
) -> None:
    (tmp_path / PARTIAL_SUBDIR).mkdir()
    (tmp_path / PARTIAL_SUBDIR / "worker-gw0.json").write_text("{not json")
    # The controller should treat this as if the worker produced nothing;
    # it is reported as incomplete.
    merge = merge_partials(tmp_path, expected_workers=["gw0"])
    assert merge.incomplete_workers == ["gw0"]
    assert merge.merged_tests == []


# ---------------------------------------------------------------------------
# End-to-end with real xdist — catches controller/worker wiring regressions
# (partial write → merge → cleanup) that hand-written partials skip over.
# ---------------------------------------------------------------------------


def test_a_real_xdist_run_should_produce_a_merged_results_json(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        test_multi="""
        def test_a():
            assert True
        def test_b():
            assert True
        def test_c():
            assert True
        def test_d():
            assert True
        """
    )
    report_dir = pytester.path / "report"
    result = pytester.runpytest(f"--harness-report={report_dir}", "-n", "2")
    assert result.ret == 0
    doc = json.loads((report_dir / "results.json").read_text())
    assert doc["run"]["xdist"] is not None
    assert doc["run"]["xdist"]["workers"] == 2
    assert not (report_dir / "_partial").exists()
    nodeids = {t["nodeid"] for t in doc["tests"]}
    assert any("test_a" in n for n in nodeids)
    assert any("test_d" in n for n in nodeids)
