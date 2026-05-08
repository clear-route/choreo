"""Unit checks for migration `005_timeline_events`.

Pin the migration's identity, linear-chain extension, and the schema
shape it ships (column set, index set, hypertable conversion,
compression + retention policies). Real-database execution of the
up/down path lives in the e2e suite at
`tests/e2e/test_timeline_db.py` (Phase 3 PR 3.3) — that's
where catalog-level facts (hypertable creation, chunk interval
enforcement, index existence in `pg_indexes`) belong.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "src" / "chronicle" / "migrations" / "versions"


def _load_migration_module(slug: str):
    """Import a migration file by its filename slug (e.g. '005_timeline_events')."""
    path = VERSIONS_DIR / f"{slug}.py"
    spec = importlib.util.spec_from_file_location(slug, path)
    assert spec is not None and spec.loader is not None, f"could not load migration {slug}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alembic_should_treat_005_as_a_descendant_of_004():
    """The migration's identity (revision id + down_revision link)
    is what alembic uses to walk the chain. Both pinned together so
    a typo in either surfaces immediately."""
    module = _load_migration_module("005_timeline_events")
    assert module.revision == "005"
    assert module.down_revision == "004"


def test_migration_005_upgrade_should_create_the_timeline_events_hypertable():
    """The migration creates a `timeline_events` table and converts
    it to a TimescaleDB hypertable. The catalog-level assertion
    (hypertable actually present after `upgrade()`) is in the e2e
    suite; here we pin only that the source contains the necessary
    DDL calls."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    assert "create_table(" in source
    assert '"timeline_events"' in source
    assert "create_hypertable(" in source


def test_migration_005_upgrade_should_create_three_indexes():
    """ §5 names three required indexes on `timeline_events`:
    per-scope (tenant/run/scenario), per-transport, and per-action."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    assert "idx_timeline_events_scope" in source
    assert "idx_timeline_events_transport" in source
    assert "idx_timeline_events_action" in source


def test_migration_005_should_include_columns_for_every_v1_3_optional_field():
    """The hypertable shape must accommodate every optional field
    introduced by schema v1.2 / v1.3: `topic` (relaxed v1.2), `transport`
    (v1.2), `logical_topic` (v1.2), `source` (v1.3)."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    for column in ["topic", "transport", "logical_topic", "source"]:
        assert f'sa.Column("{column}"' in source, f"timeline_events missing column {column!r}"


def test_migration_005_should_set_a_compression_policy():
    """Mirrors `handle_measurements`'s compression precedent (migration
    001 §137-144). At cap-saturated workload (1.5GB/day uncompressed),
    absence of compression dominates storage cost; presence is
    operational table-stakes for the hypertable."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    assert "timescaledb.compress" in source
    assert "compress_segmentby" in source
    assert "add_compression_policy" in source


def test_migration_005_should_set_a_retention_policy():
    """Hypertables cannot FK to regular tables; runs deleted at the
    application layer leave orphan timeline rows. A retention policy
    is the cleanup mechanism that bounds storage."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    assert "add_retention_policy" in source


def test_migration_005_downgrade_should_drop_the_timeline_events_table():
    """Downgrade is the inverse of upgrade: remove the policies, drop
    the indexes, drop the table. Required for safe rollback on a
    botched upgrade."""
    source = (VERSIONS_DIR / "005_timeline_events.py").read_text()
    assert 'drop_table("timeline_events")' in source
    assert 'drop_index("idx_timeline_events_scope"' in source
    assert 'drop_index("idx_timeline_events_transport"' in source
    assert 'drop_index("idx_timeline_events_action"' in source
    assert "remove_compression_policy" in source
    assert "remove_retention_policy" in source


def test_the_migration_chain_should_be_linear():
    """End-to-end chain check: every shipped migration's
    `down_revision` points at its parent in a straight line. Updating
    this list is expected on each new migration — the maintenance
    cost is the cost of an explicit chain assertion that catches
    multi-head accidents at PR review time."""
    chain_pairs = [
        ("001_initial_schema", None),
        ("06014e2d1b77_add_run_stats_columns", "001"),
        ("002_add_topics_table", "06014e2d1b77"),
        ("003_stage_transports", "002"),
        ("004_relax_handle_transport", "003"),
        ("005_timeline_events", "004"),
    ]
    for slug, expected_down in chain_pairs:
        module = _load_migration_module(slug)
        assert module.down_revision == expected_down, (
            f"{slug}.down_revision = {module.down_revision!r}, expected {expected_down!r}"
        )
