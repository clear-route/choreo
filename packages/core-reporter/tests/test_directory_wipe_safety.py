"""Directory-wipe safety — PRD-007 §10 / Decision #29.

These tests exercise `_safepath` directly (unit-level) and through the
plugin (integration-level via pytester).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choreo_reporter._safepath import (
    SENTINEL_FILENAME,
    UnsafeReportPath,
    contains_only_known_entries,
    is_existing_report_dir,
    prepare_output_dir,
    validate_report_path,
)

# ---------------------------------------------------------------------------
# validate_report_path
# ---------------------------------------------------------------------------


def test_the_filesystem_root_should_be_rejected() -> None:
    with pytest.raises(UnsafeReportPath, match="filesystem root"):
        validate_report_path(Path("/"))


def test_the_user_home_directory_should_be_rejected() -> None:
    with pytest.raises(UnsafeReportPath, match="user home"):
        validate_report_path(Path.home())


def test_a_path_inside_a_git_root_should_be_rejected(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(UnsafeReportPath, match="VCS root"):
        validate_report_path(tmp_path)


def test_a_path_whose_parent_does_not_exist_should_be_rejected(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "never" / "made" / "report"
    with pytest.raises(UnsafeReportPath, match="does not exist"):
        validate_report_path(missing)


def test_a_valid_path_should_resolve_and_return(tmp_path: Path) -> None:
    target = tmp_path / "report"
    resolved = validate_report_path(target)
    assert resolved == target.resolve()


# ---------------------------------------------------------------------------
# prepare_output_dir
# ---------------------------------------------------------------------------


def test_preparing_a_fresh_directory_should_create_it_with_the_sentinel(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report"
    prepared = prepare_output_dir(target)
    assert prepared.is_dir()
    assert (prepared / SENTINEL_FILENAME).is_file()


def test_preparing_an_existing_report_dir_should_wipe_and_recreate(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report"
    prepare_output_dir(target)
    (target / "results.json").write_text("{}")
    prepare_output_dir(target)
    assert (target / SENTINEL_FILENAME).is_file()
    assert not (target / "results.json").exists()


def test_preparing_a_non_report_directory_should_refuse(
    tmp_path: Path,
) -> None:
    target = tmp_path / "user-data"
    target.mkdir()
    (target / "important.txt").write_text("user content")

    with pytest.raises(UnsafeReportPath, match="sentinel"):
        prepare_output_dir(target)

    assert (target / "important.txt").is_file()


def test_preparing_a_report_dir_containing_unknown_files_should_refuse(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report-with-cruft"
    target.mkdir()
    (target / SENTINEL_FILENAME).write_text("x")
    (target / "unexpected.data").write_text("should not be wiped")

    with pytest.raises(UnsafeReportPath, match="known set"):
        prepare_output_dir(target)

    assert (target / "unexpected.data").is_file()


def test_preparing_a_file_path_should_refuse(tmp_path: Path) -> None:
    file_path = tmp_path / "a-file.txt"
    file_path.write_text("hi")
    with pytest.raises(UnsafeReportPath):
        prepare_output_dir(file_path)


# ---------------------------------------------------------------------------
# Plugin-level integration
# ---------------------------------------------------------------------------


def test_pytest_should_abort_when_harness_report_points_at_git_root(
    pytester: pytest.Pytester,
) -> None:
    (pytester.path / ".git").mkdir()
    pytester.makepyfile(
        test_sample="""
        def test_passes():
            assert True
        """
    )
    result = pytester.runpytest(f"--harness-report={pytester.path}")
    # UsageError exits 4 in pytest. The run should not be green.
    assert result.ret != 0
    combined = result.stderr.str() + result.stdout.str()
    assert "VCS root" in combined or "refusing" in combined


# ---------------------------------------------------------------------------
# Sentinel / known-entries helpers
# ---------------------------------------------------------------------------


def test_is_existing_report_dir_should_require_the_sentinel_file(
    tmp_path: Path,
) -> None:
    assert is_existing_report_dir(tmp_path) is False
    (tmp_path / SENTINEL_FILENAME).write_text("x")
    assert is_existing_report_dir(tmp_path) is True


def test_contains_only_known_entries_should_reject_unknown_names(
    tmp_path: Path,
) -> None:
    (tmp_path / SENTINEL_FILENAME).write_text("x")
    (tmp_path / "results.json").write_text("{}")
    assert contains_only_known_entries(tmp_path) is True
    (tmp_path / "other.txt").write_text("x")
    assert contains_only_known_entries(tmp_path) is False
