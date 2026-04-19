"""Unit tests for configuration and startup validation.

Verifies 12-factor config behaviour: environment detection, production
validation, and default safety.
"""

from __future__ import annotations

import pytest
from chronicle.config import Settings


class TestEnvironmentDetection:
    """Verify is_production() classifies environments correctly."""

    def test_local_should_not_be_production(self) -> None:
        settings = Settings(environment="local")
        assert settings.is_production() is False

    def test_dev_should_not_be_production(self) -> None:
        settings = Settings(environment="dev")
        assert settings.is_production() is False

    def test_staging_should_be_production(self) -> None:
        settings = Settings(environment="staging")
        assert settings.is_production() is True

    def test_production_should_be_production(self) -> None:
        settings = Settings(environment="production")
        assert settings.is_production() is True


class TestProductionValidation:
    """Verify startup validation rejects unsafe production config."""

    def test_production_with_localhost_database_should_raise(self) -> None:
        settings = Settings(
            environment="production",
            database_url="postgresql+asyncpg://chronicle:chronicle@localhost:5432/chronicle",
            log_format="json",
        )
        with pytest.raises(ValueError, match="default local credentials"):
            settings.validate_production_config()

    def test_production_with_text_log_format_should_raise(self) -> None:
        settings = Settings(
            environment="production",
            database_url="postgresql+asyncpg://user:pass@db.example.com:5432/chronicle",
            log_format="text",
        )
        with pytest.raises(ValueError, match="LOG_FORMAT=json"):
            settings.validate_production_config()

    def test_production_with_valid_config_should_not_raise(self) -> None:
        settings = Settings(
            environment="production",
            database_url="postgresql+asyncpg://user:pass@db.example.com:5432/chronicle",
            log_format="json",
        )
        settings.validate_production_config()  # should not raise

    def test_local_with_defaults_should_not_raise(self) -> None:
        settings = Settings(environment="local")
        settings.validate_production_config()  # should not raise

    def test_dev_with_defaults_should_not_raise(self) -> None:
        settings = Settings(environment="dev")
        settings.validate_production_config()  # should not raise


class TestDefaults:
    """Verify sensible defaults exist for local development."""

    def test_default_environment_should_be_local(self) -> None:
        settings = Settings()
        assert settings.environment == "local"

    def test_default_database_url_should_target_localhost(self) -> None:
        settings = Settings()
        assert "localhost" in settings.database_url

    def test_default_log_format_should_be_text(self) -> None:
        settings = Settings()
        assert settings.log_format == "text"

    def test_default_max_body_size_should_be_20mb(self) -> None:
        settings = Settings()
        assert settings.max_body_size == 20_971_520
