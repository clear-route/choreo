"""Git metadata lookup — PRD-007 §11.

Runs in a background thread started at `pytest_sessionstart` so the git
shell-out is already resolved by `pytest_sessionfinish` — the critical
path does not block on a slow or hung git.

Environment is scrubbed for the subprocess so a malicious repo-local
`.git/config` (e.g. `core.fsmonitor`, `core.sshCommand`) cannot execute
arbitrary code during `git rev-parse`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class GitMetadata:
    sha: str | None
    branch: str | None


def _scrubbed_env() -> dict[str, str]:
    # Use a throwaway HOME so $HOME/.gitconfig is not read. Unset
    # GIT_CONFIG_* and disable fsmonitor/optional locks. We preserve
    # PATH so `git` can be found.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": tempfile.gettempdir(),
    }
    return env


def _one_shot(cwd: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            shell=False,
            cwd=str(cwd),
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    # Cap the length defensively — a malicious branch name or SHA shell
    # escape should not land a 10MB string in the report header.
    if len(out) > 256:
        return None
    return out or None


class GitMetadataLookup:
    """Async-ish wrapper: start at sessionstart, collect at sessionfinish."""

    def __init__(self, cwd: Path) -> None:
        self._cwd = cwd
        self._thread: threading.Thread | None = None
        self._result = GitMetadata(sha=None, branch=None)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="choreo-reporter-git", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        sha = _one_shot(self._cwd, ["rev-parse", "HEAD"])
        branch = _one_shot(self._cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD":
            # Detached HEAD — no branch.
            branch = None
        self._result = GitMetadata(sha=sha, branch=branch)

    def collect(self) -> GitMetadata:
        if self._thread is not None:
            self._thread.join(timeout=_TIMEOUT_SECONDS)
        return self._result
