"""Shared test fixtures.

The harness reads runtime config from environment variables (CI sets these).
See the repo's CLAUDE.md for the authoritative list.

The default `Harness.for_testing()` resolves the allowlist path via
`HARNESS_ALLOWLIST_PATH` or `./config/environments.yaml` — both of which
already exist in this repo — so most tests do not need a path fixture.

The `allowlist_yaml_path` fixture below remains for tests that need to
reference the same path explicitly (e.g. to inject it into a custom
Harness construction).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# Load pytest's in-process test-runner plugin so tests like the downstream
# consumer check can spawn isolated pytest sessions via the `pytester` fixture.
pytest_plugins = ["pytester"]


# `packages/core/tests/conftest.py` sits three directory levels below the
# repo root (`packages/core/tests/` → `packages/core/` → `packages/` → root).
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def allowlist_yaml_path() -> Path:
    """Path to the allowlist YAML in use for this test run.

    Consumers of the library in other repos will have their own fixture for
    this — there is no library-imposed convention. This fixture exists purely
    for the framework's own tests.
    """
    return REPO_ROOT / "config" / "allowlist.yaml"
