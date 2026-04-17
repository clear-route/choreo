"""Directory-wipe safety — PRD-007 §10.

The reporter writes to a temp sibling directory and atomic-renames it
into place. The final output directory carries a `.harness-report`
sentinel file so subsequent runs can distinguish a previously-emitted
report from an arbitrary user directory.

The reporter refuses to operate on paths that could reasonably belong
to the user (root, home, VCS metadata dirs) even if the user passed
them explicitly. Silently deleting user data is the class of footgun
this module exists to prevent.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SENTINEL_FILENAME = ".harness-report"
SENTINEL_CONTENT = "choreo-reporter v1 output directory\n"

# Files that the reporter itself emits. Any other file inside an
# existing report directory is a signal that this is not our directory
# and we must not wipe it.
KNOWN_FILENAMES = frozenset(
    {
        SENTINEL_FILENAME,
        "results.json",
        "index.html",
    }
)


class UnsafeReportPath(Exception):
    """Raised when `--harness-report` resolves to a path we refuse to touch."""


def _is_within_home(resolved: Path) -> bool:
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == home


def validate_report_path(path: Path) -> Path:
    """Return the absolute, resolved report path if safe to use.

    Raises `UnsafeReportPath` with a clear reason otherwise. The pytest
    plugin aborts the run on this exception (Decision #29); it does not
    fall back to a default because silently moving the output is more
    surprising than failing fast.
    """
    if not path:
        raise UnsafeReportPath("report path is empty")

    # Resolve symlinks in any existing parent. For a not-yet-created
    # path, `resolve()` still works because it uses `os.path.realpath`.
    resolved = path.expanduser().resolve()

    if resolved == Path(resolved.anchor).resolve():
        raise UnsafeReportPath(f"refusing to write to filesystem root {resolved!s}")

    if _is_within_home(resolved):
        raise UnsafeReportPath(f"refusing to write to user home directory {resolved!s}")

    for vcs in (".git", ".svn", ".hg"):
        if (resolved / vcs).exists():
            raise UnsafeReportPath(f"refusing to write to a VCS root: {resolved / vcs!s} exists")

    parent = resolved.parent
    if not parent.exists():
        raise UnsafeReportPath(f"report path parent does not exist: {parent!s}")

    try:
        parent_stat = parent.lstat()
    except OSError as e:
        raise UnsafeReportPath(f"cannot stat report path parent {parent!s}: {e}") from e

    if hasattr(os, "geteuid"):
        if parent_stat.st_uid != os.geteuid():
            raise UnsafeReportPath(
                f"refusing to write to {resolved!s}: parent directory "
                f"{parent!s} is not owned by the current user"
            )

    return resolved


def is_existing_report_dir(path: Path) -> bool:
    """True when `path` exists, is a directory, and contains the sentinel."""
    if not path.exists() or not path.is_dir():
        return False
    return (path / SENTINEL_FILENAME).is_file()


def contains_only_known_entries(path: Path) -> bool:
    """True when every entry in `path` is in KNOWN_FILENAMES.

    Conservative: any subdirectory, hidden file, symlink, or unknown
    filename disqualifies the directory.
    """
    if not path.is_dir():
        return False
    try:
        for entry in path.iterdir():
            if entry.is_symlink():
                return False
            if entry.name not in KNOWN_FILENAMES:
                return False
            if not entry.is_file():
                return False
        return True
    except OSError:
        return False


def prepare_output_dir(report_path: Path) -> Path:
    """Create (or recreate) the report directory safely.

    - If the path does not exist, creates it with the sentinel.
    - If the path exists and is an existing report directory (has
      sentinel, only known entries), removes it and recreates.
    - If the path exists but is not an existing report directory,
      raises `UnsafeReportPath`.

    Returns the prepared (empty, sentinel-ready) directory.
    """
    safe = validate_report_path(report_path)

    if safe.exists():
        if not safe.is_dir():
            raise UnsafeReportPath(f"{safe!s} exists and is not a directory")
        if not is_existing_report_dir(safe):
            raise UnsafeReportPath(
                f"{safe!s} exists but does not contain the "
                f"{SENTINEL_FILENAME} sentinel; refusing to overwrite. "
                f"If this is the correct location, delete the directory first."
            )
        if not contains_only_known_entries(safe):
            raise UnsafeReportPath(
                f"{safe!s} contains files beyond the reporter's "
                f"known set; refusing to wipe. Known files: "
                f"{sorted(KNOWN_FILENAMES)}"
            )
        shutil.rmtree(safe)

    safe.mkdir(parents=True, exist_ok=False)
    write_sentinel(safe)
    return safe


def write_sentinel(report_path: Path) -> None:
    (report_path / SENTINEL_FILENAME).write_text(SENTINEL_CONTENT, encoding="utf-8")


def atomic_write_text(report_path: Path, filename: str, content: str) -> Path:
    """Write a file atomically: write to a temp sibling then rename.

    The rename is atomic on POSIX filesystems and prevents a partially
    written `results.json` from being observed by anyone reading the
    report directory concurrently.
    """
    if filename not in KNOWN_FILENAMES:
        raise ValueError(f"atomic_write_text may only emit known files; got {filename!r}")
    dest = report_path / filename
    tmp = report_path / f".{filename}.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, dest)
    return dest
