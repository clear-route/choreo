"""Shared test fixtures for Chronicle."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from chronicle.app import create_app
from chronicle.config import Settings
from chronicle.dependencies import get_session
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

DB_URL = "postgresql+asyncpg://chronicle:chronicle@localhost:5433/chronicle"

_REPORT_PATH = Path(__file__).resolve().parents[2] / "core" / "test-report" / "results.json"


def db_available() -> bool:
    """Check if TimescaleDB is reachable at the expected URL."""
    try:
        import asyncio

        import asyncpg

        async def _check():
            conn = await asyncpg.connect(
                "postgresql://chronicle:chronicle@localhost:5433/chronicle"
            )
            await conn.close()

        asyncio.run(_check())
        return True
    except Exception:
        return False


skip_no_db = pytest.mark.skipif(
    not db_available(),
    reason="TimescaleDB not reachable at localhost:5433",
)


@pytest.fixture(scope="session")
def real_report() -> dict:
    """Load the real core test report (session-scoped)."""
    if not _REPORT_PATH.exists():
        pytest.skip("Real test report not available")
    return json.loads(_REPORT_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def settings() -> Settings:
    """Test settings -- uses a dummy database URL since the session is mocked."""
    return Settings(database_url="postgresql+asyncpg://test:test@localhost:5432/test")


@pytest.fixture()
def mock_session() -> AsyncMock:
    """A mock AsyncSession that simulates a connected database.

    The ``execute`` method returns a mock result whose ``scalar_one()``
    returns ``1`` (simulating ``SELECT 1``).
    """
    session = AsyncMock(spec=AsyncSession)
    result = AsyncMock()
    result.scalar_one.return_value = 1
    session.execute.return_value = result
    return session


@pytest.fixture()
def client(settings: Settings, mock_session: AsyncMock) -> TestClient:
    """A ``TestClient`` wired to a Chronicle app with the database session
    replaced by a mock.

    This allows testing route handlers, error handling, and response
    shapes without a running database.
    """
    app = create_app(settings=settings)

    # Override the session dependency so no real DB connection is needed.
    app.dependency_overrides[get_session] = lambda: mock_session

    return TestClient(app)


@pytest.fixture()
def db_client():
    """A TestClient pointing at a real TimescaleDB instance.

    Uses ``with TestClient(app)`` to trigger the lifespan context, so
    ``app.state.sessionmaker`` is populated before requests are made.

    Truncates all tables after each test to ensure isolation.
    """
    settings = Settings(database_url=DB_URL)
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c
        # Teardown: truncate all tables
        import asyncio

        import asyncpg

        async def _cleanup():
            conn = await asyncpg.connect(
                "postgresql://chronicle:chronicle@localhost:5433/chronicle"
            )
            await conn.execute(
                "TRUNCATE runs, scenarios, handle_measurements, anomalies, topics, tenants CASCADE"
            )
            await conn.close()

        asyncio.run(_cleanup())
