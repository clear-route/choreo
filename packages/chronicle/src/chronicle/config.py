"""Application settings following 12-factor app principles (Factor III).

Configuration sources, in priority order (highest wins):

1. Constructor kwargs (used in tests)
2. Environment variables (``CHRONICLE_*`` prefix, or ``DATABASE_URL``)
3. Kubernetes mounted secrets (``/run/secrets/<field_name>``)
4. ``.env`` file (local development only; never baked into images)
5. Field defaults (safe for local dev, validated against in production)

Production deployments supply config via Kubernetes ConfigMaps (non-secret
values) and Secrets (credentials).  See the Helm chart for the mapping.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Chronicle configuration.

    ``database_url`` accepts both the industry-standard ``DATABASE_URL``
    and the prefixed ``CHRONICLE_DATABASE_URL``.  All other fields use
    the ``CHRONICLE_`` prefix.

    In production (``environment`` is ``"staging"`` or ``"production"``),
    startup validation rejects dev defaults.  Call
    ``validate_production_config()`` during lifespan startup.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHRONICLE_",
        env_nested_delimiter="__",
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",
    )

    # ── Environment ──
    environment: str = "local"  # local | dev | staging | production

    # ── Core ──
    database_url: str = Field(
        default="postgresql+asyncpg://chronicle:chronicle@localhost:5432/chronicle",
        validation_alias=AliasChoices("DATABASE_URL", "CHRONICLE_DATABASE_URL"),
    )
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_format: str = "text"  # "json" in production

    # ── Connection pool ──
    db_pool_size: int = 5
    db_pool_max: int = 20
    db_pool_timeout: int = 30  # seconds

    # ── Request limits ──
    max_body_size: int = 20_971_520  # 20 MB

    # ── SSE ──
    max_sse_connections: int = 100

    # ── Anomaly detection ──
    baseline_window: int = 10
    baseline_min_samples: int = 5
    baseline_sigma: float = 2.0
    budget_violation_pct: float = 20.0
    outcome_shift_pct: float = 5.0

    def is_production(self) -> bool:
        """Return True if running in a production-like environment."""
        return self.environment in ("staging", "production")

    def validate_production_config(self) -> None:
        """Fail fast if production config uses local development defaults.

        Called during application lifespan startup.  Does nothing in
        local/dev environments.
        """
        if not self.is_production():
            return
        if "chronicle:chronicle@localhost" in self.database_url:
            raise ValueError(
                "DATABASE_URL contains default local credentials in a "
                f"{self.environment} environment. Set DATABASE_URL to a "
                "real connection string."
            )
        if self.log_format != "json":
            raise ValueError(
                f"Production environment '{self.environment}' requires "
                "CHRONICLE_LOG_FORMAT=json for structured logging."
            )
