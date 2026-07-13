"""Alembic environment: builds its connection from the service's own Settings.

This keeps a single source of truth for the database DSN (env vars / .env),
per CLAUDE.md §5 ("configuration via typed config + environment ... no config
values hard-coded"). Migrations run against the same async engine machinery
the app uses, via Alembic's async-engine pattern.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from aqros_market_data.adapters.orm import Base
from aqros_market_data.config import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = Settings()
# Alembic's autogenerate/offline mode needs a plain URL string.
config.set_main_option("sqlalchemy.url", str(settings.database_url))


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB using the async engine."""
    from aqros_market_data.adapters.db import create_engine

    connectable: AsyncEngine = create_engine(settings)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
