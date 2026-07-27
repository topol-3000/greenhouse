import asyncio
from collections.abc import Callable

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from ai_greenhouse.core.config import get_settings
from ai_greenhouse.infrastructure.database.metadata import metadata

# Importing the domain models registers their tables on the shared metadata.
# Without this, autogenerate and `alembic check` compare the database against
# an empty metadata and report no changes. Every new module's models.py must be
# added here.
from ai_greenhouse.topology import models as topology_models  # noqa: F401

config = context.config
settings = get_settings()
config.set_main_option(
    "sqlalchemy.url",
    settings.database_url_value().replace("%", "%%"),
)
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


runner: Callable[[], None] = (
    run_migrations_offline if context.is_offline_mode() else run_migrations_online
)
runner()
