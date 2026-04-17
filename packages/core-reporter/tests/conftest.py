"""Shared fixtures for core-reporter tests.

Every test uses `pytester` to spin up an isolated inner pytest session
so the outer session's plugin state is not polluted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


pytest_plugins = ["pytester"]


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "docs" / "schemas" / "test-report-v1.json"


@pytest.fixture
def schema_path() -> Path:
    return SCHEMA_PATH


@pytest.fixture
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _enable_asyncio_in_pytester(pytester: pytest.Pytester) -> None:
    """Pytester inner sessions do not inherit our `asyncio_mode = auto`
    setting. Write it in every generated session so `async def` tests
    load correctly."""
    pytester.makeini(
        """
        [pytest]
        asyncio_mode = auto
        asyncio_default_fixture_loop_scope = session
        asyncio_default_test_loop_scope = session
        """
    )


_ALLOWLIST_YAML = 'lbm_resolvers: ["lbmrd:15380"]\n'


@pytest.fixture
def tiny_test_module() -> str:
    """A minimal pytest module that drives one harness scenario.

    Used by pytester-based tests that want a realistic report containing
    at least one test with at least one scenario."""
    return f'''
import pytest_asyncio

from core import Harness
from core.matchers import field_equals
from core.transports import MockTransport


_ALLOWLIST_YAML = """{_ALLOWLIST_YAML}"""


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text(_ALLOWLIST_YAML)
    transport = MockTransport(allowlist_path=alist, lbm_resolver="lbmrd:15380")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_a_scenario_passes(harness):
    async with harness.scenario("fill") as s:
        s.expect("t.test", field_equals("k", "v"))
        s = s.publish("t.test", {{"k": "v"}})
        result = await s.await_all(timeout_ms=200)
    result.assert_passed()
'''
