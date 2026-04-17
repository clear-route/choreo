"""pytest-xdist support — PRD-007 §1 User Story 5 / Decision #15.

Each worker writes a partial JSON under `<report-dir>/_partial/worker-<id>.json`.
The controller (master) reads every partial at `pytest_sessionfinish`,
merges the `tests[]` arrays into its own in-memory run document, and
writes the final `results.json`.

Workers that started but never wrote a partial (crash mid-run) are
surfaced via `run.xdist.incomplete_workers`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARTIAL_SUBDIR = "_partial"


@dataclass
class MergeOutcome:
    merged_tests: list[dict[str, Any]]
    workers_seen: list[str]
    expected_workers: list[str]
    incomplete_workers: list[str]


def partial_path(report_dir: Path, worker_id: str) -> Path:
    return report_dir / PARTIAL_SUBDIR / f"worker-{worker_id}.json"


def write_partial(report_dir: Path, worker_id: str, payload: dict[str, Any]) -> None:
    (report_dir / PARTIAL_SUBDIR).mkdir(parents=True, exist_ok=True)
    dest = partial_path(report_dir, worker_id)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, default=str, indent=2),
        encoding="utf-8",
    )
    tmp.replace(dest)


def merge_partials(report_dir: Path, expected_workers: list[str]) -> MergeOutcome:
    """Collect every worker partial present under `_partial/`.

    Workers that were expected (per `expected_workers`) but did not
    flush a partial file are included in `incomplete_workers`.
    """
    partial_dir = report_dir / PARTIAL_SUBDIR
    if not partial_dir.exists():
        return MergeOutcome(
            merged_tests=[],
            workers_seen=[],
            expected_workers=expected_workers,
            incomplete_workers=list(expected_workers),
        )

    seen_ids: list[str] = []
    merged: list[dict[str, Any]] = []
    for entry in sorted(partial_dir.iterdir()):
        if not entry.name.startswith("worker-") or not entry.name.endswith(".json"):
            continue
        worker_id = entry.name[len("worker-") : -len(".json")]
        try:
            content = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        seen_ids.append(worker_id)
        for t in content.get("tests", []):
            merged.append(t)

    incomplete = [w for w in expected_workers if w not in seen_ids]
    return MergeOutcome(
        merged_tests=merged,
        workers_seen=seen_ids,
        expected_workers=expected_workers,
        incomplete_workers=incomplete,
    )


def cleanup_partial_dir(report_dir: Path) -> None:
    partial_dir = report_dir / PARTIAL_SUBDIR
    if not partial_dir.exists():
        return
    for entry in partial_dir.iterdir():
        try:
            entry.unlink()
        except OSError:
            pass
    try:
        partial_dir.rmdir()
    except OSError:
        pass
