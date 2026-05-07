"""Framework foundation for PRD-012 — Test Report Stage Support.

Red tests for the framework changes promised in PRD-012 §2.8: a `kind`
discriminator on result types, public `StageScenarioResult` (with a
backward-compat alias to the old underscored name),
`StageScenarioResult.correlation_id` carrying the scope's logical id,
and `Harness.transport_name` exposing the transport class name.

Each test names a behaviour the admiral-reporter package depends on.
The tests construct result objects directly where possible; the
Stage-scope-teardown integration is exercised in
`tests/integration/test_stage_result_shape.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from admiral import (
    Harness,
    StageScenarioResult,
)
from admiral.scenario import ScenarioResult
from admiral.transports import MockTransport

# ---------------------------------------------------------------------------
# kind discriminator on ScenarioResult and StageScenarioResult
# ---------------------------------------------------------------------------


def test_a_single_harness_scenario_result_should_carry_kind_single_harness():
    """The reporter dispatches on `result.kind`; ScenarioResult declares
    its kind so duck-typing is not needed."""
    result = ScenarioResult(name="t", correlation_id=None, handles=(), passed=True)
    assert result.kind == "single_harness"


def test_a_stage_scenario_result_should_carry_kind_stage():
    result = StageScenarioResult(handles=(), passed=True, replies=(), correlation_id=None)
    assert result.kind == "stage"


def test_kind_should_not_be_settable_at_construction_on_single_harness_results():
    """`kind` is per-type-fixed; passing it to the constructor is an error."""
    with pytest.raises(TypeError):
        ScenarioResult(  # type: ignore[call-arg]
            name="t",
            correlation_id=None,
            handles=(),
            passed=True,
            kind="stage",
        )


def test_kind_should_not_be_settable_at_construction_on_stage_results():
    with pytest.raises(TypeError):
        StageScenarioResult(  # type: ignore[call-arg]
            handles=(),
            passed=True,
            replies=(),
            correlation_id=None,
            kind="single_harness",
        )


# ---------------------------------------------------------------------------
# Public StageScenarioResult export + backward-compat alias
# ---------------------------------------------------------------------------


def test_StageScenarioResult_should_be_publicly_importable_from_admiral():
    """The reporter imports the public type for isinstance dispatch.
    Must round-trip via the package surface, not just the internal
    module."""
    from admiral import StageScenarioResult as PublicResult
    from admiral.stage import StageScenarioResult as ModuleResult

    assert PublicResult is ModuleResult


def test_underscored_alias_should_remain_for_backward_compat():
    """In-tree code referring to `_StageScenarioResult` continues to
    import. PRD-012 §2.8 commits to a backward-compat alias for two
    minor versions."""
    from admiral.stage import StageScenarioResult, _StageScenarioResult

    assert _StageScenarioResult is StageScenarioResult


# ---------------------------------------------------------------------------
# StageScenarioResult.correlation_id — the scope's logical id
# ---------------------------------------------------------------------------


def test_a_stage_scenario_result_should_carry_a_correlation_id_field():
    """The reporter populates `scenario.correlation_id` from this field
    (PRD-012 §1.4.1, §2.6) so it does not reach into private scope
    attributes. Constructed directly here; Stage-side wire-up is
    exercised in the integration suite."""
    result = StageScenarioResult(
        handles=(),
        passed=True,
        replies=(),
        correlation_id="logical-3f2a91b8c4d50e1f",
    )
    assert result.correlation_id == "logical-3f2a91b8c4d50e1f"


def test_a_stage_scenario_result_should_accept_a_none_correlation_id():
    """Stages constructed with NoCorrelationPolicy / a bridge whose
    fresh() returns None still produce a result. The schema relaxation
    in PRD-012 §1.4.1 (`["string", "null"]`) makes this representable."""
    result = StageScenarioResult(handles=(), passed=True, replies=(), correlation_id=None)
    assert result.correlation_id is None


# ---------------------------------------------------------------------------
# Harness.transport_name property
# ---------------------------------------------------------------------------


def test_a_harness_should_expose_a_transport_name_property(
    allowlist_yaml_path: Path,
) -> None:
    """Reporter §2.5 reads `harness.transport_name` to populate
    `run.transport` for single-Harness runs. The value is the
    transport's class name."""
    harness = Harness(MockTransport(allowlist_path=allowlist_yaml_path))
    assert harness.transport_name == "MockTransport"


def test_harness_transport_name_should_be_read_only(
    allowlist_yaml_path: Path,
) -> None:
    """Mutation breaks the reporter contract; the property has no
    setter."""
    harness = Harness(MockTransport(allowlist_path=allowlist_yaml_path))
    with pytest.raises(AttributeError):
        harness.transport_name = "Spoofed"  # type: ignore[misc]
