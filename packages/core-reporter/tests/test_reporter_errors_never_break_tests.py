"""The reporter must never be a test-failure source — PRD-007 §8."""
from __future__ import annotations

import pytest


def test_a_broken_observer_should_not_break_the_outer_pytest_run(
    pytester: pytest.Pytester,
) -> None:
    """Simulate a reporter bug by registering a broken observer from within
    the inner session; the session itself must still report the tests
    as passing."""
    pytester.makepyfile(
        conftest="""
        import pytest

        @pytest.fixture(autouse=True)
        def _register_broken_observer():
            from core._reporting import register_observer, unregister_observer
            def bad_cb(result, nodeid, completed_normally):
                raise RuntimeError('observer blew up')
            register_observer(bad_cb)
            yield
            unregister_observer(bad_cb)
        """,
        test_broken_observer_scenario='''
import pytest_asyncio
from core import Harness
from core.matchers import field_equals
from core.transports import MockTransport


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def harness(tmp_path_factory):
    alist = tmp_path_factory.mktemp("allow") / "allowlist.yaml"
    alist.write_text("lbm_resolvers: [\\"lbmrd:15380\\"]\\n")
    transport = MockTransport(allowlist_path=alist, lbm_resolver="lbmrd:15380")
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_still_passes(harness):
    async with harness.scenario("survives") as s:
        s.expect("t.broken", field_equals("k", "v"))
        s = s.publish("t.broken", {"k": "v"})
        result = await s.await_all(timeout_ms=200)
    result.assert_passed()
''',
    )
    report_dir = pytester.path / "report"
    result = pytester.runpytest(
        f"--harness-report={report_dir}", "-W", "ignore::RuntimeWarning"
    )
    assert result.ret == 0


def test_when_serialisation_fails_the_session_exit_code_should_be_unaffected(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the sessionfinish serialiser raises, the exit code stays green.
    We simulate by pointing the plugin at a path whose parent becomes
    unwritable between validation and write — easiest approximation is a
    monkeypatch on atomic_write_text that raises."""
    pytester.makepyfile(
        conftest="""
        def pytest_sessionstart(session):
            from core_reporter import _safepath

            original = _safepath.atomic_write_text

            def blow_up(*args, **kwargs):
                raise OSError('disk on fire')

            _safepath.atomic_write_text = blow_up

            # Restore at the very end via a teardown hook.
            import atexit
            atexit.register(lambda: setattr(_safepath, 'atomic_write_text', original))
        """,
        test_still_green="""
        def test_passes():
            assert True
        """,
    )
    report_dir = pytester.path / "report"
    result = pytester.runpytest(
        f"--harness-report={report_dir}", "-W", "ignore::RuntimeWarning"
    )
    # Test collection ran clean, and the reporter's failure must not
    # convert a green run into a red one.
    assert result.ret == 0
