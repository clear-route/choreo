"""Chronicle CLI — run the server or manage the database.

Usage:
    python -m chronicle                # dev server with hot reload
    python -m chronicle --no-reload    # without reload (closer to production)
    python -m chronicle migrate        # run database migrations (alembic upgrade head)
    python -m chronicle migrate status # show current migration revision

For production, use uvicorn directly:
    uvicorn chronicle.app:create_app --factory --host 0.0.0.0 --port 5173
"""

import os
import sys
from pathlib import Path


def _migrate(args: list[str]) -> None:
    """Run Alembic migrations using Chronicle's bundled migration scripts.

    Consumers don't need an alembic.ini — this function configures Alembic
    programmatically using the migrations shipped inside the installed package.
    """
    from alembic import command
    from alembic.config import Config

    # Point Alembic at the migrations inside the installed package
    migrations_dir = Path(__file__).parent / "migrations"
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(migrations_dir))

    # Database URL from environment
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("CHRONICLE_DATABASE_URL")
    if not db_url:
        print(
            "Error: DATABASE_URL or CHRONICLE_DATABASE_URL must be set.\n"
            "Example: DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5433/chronicle",
            file=sys.stderr,
        )
        sys.exit(1)

    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    subcommand = args[0] if args else "head"

    if subcommand == "status":
        command.current(alembic_cfg, verbose=True)
    elif subcommand == "head":
        print(f"Running migrations against {db_url.split('@')[-1]}...")
        command.upgrade(alembic_cfg, "head")
        print("Migrations complete.")
    elif subcommand == "downgrade":
        target = args[1] if len(args) > 1 else "-1"
        command.downgrade(alembic_cfg, target)
    else:
        print(f"Unknown migrate subcommand: {subcommand}", file=sys.stderr)
        print("Usage: python -m chronicle migrate [head|status|downgrade]", file=sys.stderr)
        sys.exit(1)


def _serve() -> None:
    """Start the Chronicle dev server with hot reload."""
    import uvicorn

    reload = "--no-reload" not in sys.argv
    uvicorn.run(
        "chronicle.app:create_app",
        factory=True,
        reload=reload,
        reload_dirs=["src/chronicle"] if reload else None,
        host="0.0.0.0",
        port=5173,
    )


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        _migrate(sys.argv[2:])
    else:
        _serve()


if __name__ == "__main__":
    main()
