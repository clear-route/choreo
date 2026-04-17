"""pytest plugin entry point — PRD-007 §1.

Hooks:

  pytest_addoption        — CLI flags
  pytest_configure        — build reporter state, start git thread
  pytest_sessionstart     — prepare output directory (master only)
  pytest_runtest_protocol — hookwrapper: set current_test_nodeid
                            contextvar + record per-test metadata
  pytest_runtest_logreport — consume setup/call/teardown TestReports
  pytest_sessionfinish    — write results.json (master) or worker partial

Activation: the package ships an entry-point under `pytest11`, so
`pip install core-reporter` is sufficient. Explicit opt-in via
`-p core_reporter.plugin` is also supported.

Failure containment: every hook catches unexpected exceptions and emits
a pytest warning; the reporter is never a test-failure source (§8).
"""
from __future__ import annotations

import json
import os
import time
import warnings
from pathlib import Path
from typing import Any, Generator

import pytest

from core._reporting import (
    current_test_nodeid,
    register_observer,
    unregister_observer,
)

from . import __version__ as reporter_version
from ._collect import Collector, compute_totals
from ._git import GitMetadataLookup
from ._safepath import (
    UnsafeReportPath,
    atomic_write_text,
    prepare_output_dir,
    validate_report_path,
)
from ._template import render_html
from ._xdist import (
    cleanup_partial_dir,
    merge_partials,
    write_partial,
)


_HARD_FILE_CAP_BYTES = 10 * 1024 * 1024


class _State:
    """Per-session reporter state. Stored on `config._core_reporter_state`."""

    def __init__(
        self,
        *,
        report_dir: Path,
        collector: Collector,
        git: GitMetadataLookup,
        disabled: bool,
        stream_redact: bool,
        is_worker: bool,
        worker_id: str | None,
    ) -> None:
        self.report_dir = report_dir
        self.collector = collector
        self.git = git
        self.disabled = disabled
        self.stream_redact = stream_redact
        self.is_worker = is_worker
        self.worker_id = worker_id
        self.observer = self._make_observer()

    def _make_observer(self):
        collector = self.collector

        def cb(result, nodeid, completed_normally):
            collector.record_scenario(result, nodeid, completed_normally)

        return cb


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("core-reporter", "Harness test report (PRD-007)")
    group.addoption(
        "--harness-report",
        action="store",
        dest="harness_report",
        default=None,
        metavar="PATH",
        help=(
            "Directory to write the harness test report into. Defaults to "
            "./test-report or $HARNESS_REPORT_DIR."
        ),
    )
    group.addoption(
        "--harness-report-disable",
        action="store_true",
        dest="harness_report_disable",
        default=False,
        help="Skip writing a harness report for this run.",
    )
    group.addoption(
        "--harness-report-no-stream-redact",
        action="store_true",
        dest="harness_report_no_stream_redact",
        default=False,
        help=(
            "Disable credential-shape redaction of stdout/stderr/log "
            "content. Key-based field redaction still applies."
        ),
    )
    group.addoption(
        "--harness-report-project-name",
        action="store",
        dest="harness_report_project_name",
        default=None,
        metavar="NAME",
        help=(
            "Label the report with this project name. Defaults to the "
            "$HARNESS_PROJECT_NAME env var, otherwise the rootdir basename."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    global _active_config
    _active_config = config

    try:
        state = _build_state(config)
    except UnsafeReportPath as e:
        raise pytest.UsageError(f"[core-reporter] {e}") from e
    except Exception as e:
        warnings.warn(
            f"core-reporter: configure failed "
            f"({type(e).__name__}: {e}); disabling reporter for this run",
            RuntimeWarning,
            stacklevel=2,
        )
        state = _disabled_state()

    config._core_reporter_state = state  # type: ignore[attr-defined]

    if state.disabled:
        return

    state.git.start()
    register_observer(state.observer)


def _build_state(config: pytest.Config) -> _State:
    disabled_flag = bool(config.getoption("harness_report_disable", False))
    report_dir_opt = config.getoption("harness_report", None)
    env_dir = os.environ.get("HARNESS_REPORT_DIR")

    if disabled_flag or report_dir_opt == "":
        return _disabled_state()

    raw_path = report_dir_opt or env_dir or "./test-report"
    rootpath = Path(str(getattr(config, "rootpath", Path.cwd())))
    report_path = Path(raw_path)
    if not report_path.is_absolute():
        report_path = rootpath / report_path

    # Validate now so `UsageError` fires before any test runs.
    validate_report_path(report_path)

    worker_input = getattr(config, "workerinput", None)
    is_worker = worker_input is not None
    worker_id = worker_input.get("workerid") if is_worker else None

    collector = Collector()
    collector.environment = os.environ.get("HARNESS_ENV")
    collector.project_name = _resolve_project_name(config, rootpath)

    stream_redact = not bool(
        config.getoption("harness_report_no_stream_redact", False)
    )

    return _State(
        report_dir=report_path,
        collector=collector,
        git=GitMetadataLookup(cwd=rootpath),
        disabled=False,
        stream_redact=stream_redact,
        is_worker=is_worker,
        worker_id=worker_id,
    )


def _resolve_project_name(config: pytest.Config, rootpath: Path) -> str | None:
    override = config.getoption("harness_report_project_name", None)
    if override:
        return str(override).strip() or None
    env = os.environ.get("HARNESS_PROJECT_NAME")
    if env:
        return env.strip() or None
    name = rootpath.name
    return name or None


def _disabled_state() -> _State:
    return _State(
        report_dir=Path(""),
        collector=Collector(),
        git=GitMetadataLookup(cwd=Path.cwd()),
        disabled=True,
        stream_redact=False,
        is_worker=False,
        worker_id=None,
    )


def _get_state(config: pytest.Config) -> _State | None:
    return getattr(config, "_core_reporter_state", None)


def pytest_sessionstart(session: pytest.Session) -> None:
    state = _get_state(session.config)
    if state is None or state.disabled:
        return
    try:
        if not state.is_worker:
            prepare_output_dir(state.report_dir)
        state.collector.start_run(mono=time.monotonic())
    except UnsafeReportPath as e:
        raise pytest.UsageError(f"[core-reporter] {e}") from e
    except Exception as e:
        warnings.warn(
            f"core-reporter: sessionstart failed "
            f"({type(e).__name__}: {e}); disabling for this run",
            RuntimeWarning,
            stacklevel=2,
        )
        state.disabled = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, None, None]:
    state = _get_state(item.config)
    if state is None or state.disabled:
        yield
        return

    try:
        _note_item_metadata(state, item)
    except Exception as e:
        warnings.warn(
            f"core-reporter: metadata capture failed for {item.nodeid}: {e}",
            RuntimeWarning,
            stacklevel=2,
        )

    token = current_test_nodeid.set(item.nodeid)
    try:
        yield
    finally:
        current_test_nodeid.reset(token)


def _relative_file(item: pytest.Item) -> str:
    """Return the test file path relative to pytest's rootpath.

    `item.location[0]` is inconsistent across invocation cwds — running
    from a subdirectory with a wider rootdir can yield `../../...`
    prefixes. Deriving from `item.path` against `item.config.rootpath`
    always gives a stable, project-relative string without traversal.
    """
    try:
        rootpath = Path(str(item.config.rootpath))
        abs_path = Path(str(item.path)).resolve()
        rel = abs_path.relative_to(rootpath.resolve())
        return str(rel)
    except (ValueError, AttributeError, OSError):
        pass
    location = getattr(item, "location", ("", 0, ""))
    return str(location[0]) if location and location[0] else str(item.path)


def _note_item_metadata(state: _State, item: pytest.Item) -> None:
    file = _relative_file(item)
    name = getattr(item, "name", item.nodeid.split("::")[-1])
    cls = getattr(item, "cls", None)
    class_name = cls.__name__ if cls is not None else None
    markers = [m.name for m in item.iter_markers()]
    state.collector.note_test_metadata(
        nodeid=item.nodeid,
        file=file,
        name=name,
        class_name=class_name,
        markers=markers,
        worker_id=state.worker_id,
    )


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_logreport(
    report: pytest.TestReport,
) -> Generator[None, None, None]:
    """Route the report through the collector.

    Implemented as a hookwrapper so we can reach the active `Config` via
    `report.node.session.config` without relying on module globals.
    pytest does not pass Config to this hook directly.
    """
    yield
    # After the default handlers ran.
    config = _config_for_report(report)
    if config is None:
        return
    state = _get_state(config)
    if state is None or state.disabled:
        return
    try:
        state.collector.handle_report(report)
    except Exception as e:
        warnings.warn(
            f"core-reporter: logreport handler failed: {e}",
            RuntimeWarning,
            stacklevel=2,
        )


def _config_for_report(report: pytest.TestReport) -> pytest.Config | None:
    # `TestReport.node` is present for phase=='call' but not always for
    # setup/teardown. Fall back to the module-level stash populated in
    # `pytest_configure`.
    node = getattr(report, "node", None)
    if node is not None:
        session = getattr(node, "session", None)
        if session is not None:
            return session.config
    return _active_config


_active_config: pytest.Config | None = None


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int
) -> None:
    state = _get_state(session.config)
    if state is None or state.disabled:
        return
    try:
        _write_report(session, state)
    except Exception as e:
        warnings.warn(
            f"core-reporter: sessionfinish failed "
            f"({type(e).__name__}: {e}); report not written",
            RuntimeWarning,
            stacklevel=2,
        )
    finally:
        try:
            unregister_observer(state.observer)
        except Exception:
            pass


def _write_report(session: pytest.Session, state: _State) -> None:
    state.collector.finish_run()
    final_duration_ms = (
        (time.monotonic() - state.collector.started_mono) * 1000
    )
    git = state.git.collect()

    xdist_info = _xdist_info(session, state)
    payload = state.collector.to_dict(
        reporter_version=reporter_version,
        harness_version=_harness_version(),
        git_sha=git.sha,
        git_branch=git.branch,
        xdist=xdist_info,
        final_duration_ms=final_duration_ms,
        stream_redact=state.stream_redact,
    )

    if state.is_worker:
        assert state.worker_id is not None
        write_partial(state.report_dir, state.worker_id, payload)
        return

    expected_workers = _expected_worker_ids(session)
    if expected_workers:
        merge = merge_partials(
            state.report_dir, expected_workers=expected_workers
        )
        payload["tests"].extend(merge.merged_tests)
        if xdist_info is not None:
            xdist_info["incomplete_workers"] = merge.incomplete_workers
            payload["run"]["xdist"] = xdist_info
        payload["run"]["totals"] = compute_totals(payload["tests"])
        cleanup_partial_dir(state.report_dir)

    encoded = json.dumps(payload, default=str, indent=2)
    if len(encoded.encode("utf-8")) > _HARD_FILE_CAP_BYTES:
        warnings.warn(
            f"core-reporter: report exceeds {_HARD_FILE_CAP_BYTES} bytes; "
            f"refusing to write (PRD-007 §5)",
            RuntimeWarning,
            stacklevel=2,
        )
        return
    atomic_write_text(state.report_dir, "results.json", encoded)
    try:
        html = render_html(encoded)
        atomic_write_text(state.report_dir, "index.html", html)
    except Exception as e:
        warnings.warn(
            f"core-reporter: HTML render failed "
            f"({type(e).__name__}: {e}); results.json was still written",
            RuntimeWarning,
            stacklevel=2,
        )


def _harness_version() -> str:
    try:
        import core

        return getattr(core, "__version__", "unknown")
    except Exception:
        return "unknown"


def _expected_worker_ids(session: pytest.Session) -> list[str]:
    num = getattr(session.config.option, "numprocesses", None)
    if not isinstance(num, int) or num <= 0:
        return []
    return [f"gw{i}" for i in range(num)]


def _xdist_info(
    session: pytest.Session, state: _State
) -> dict[str, Any] | None:
    num = getattr(session.config.option, "numprocesses", None)
    if state.is_worker:
        return {
            "workers": num if isinstance(num, int) and num > 0 else 1,
            "incomplete_workers": [],
        }
    if isinstance(num, int) and num > 0:
        return {"workers": num, "incomplete_workers": []}
    return None


def pytest_unconfigure(config: pytest.Config) -> None:  # pragma: no cover
    global _active_config
    if _active_config is config:
        _active_config = None
