import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text
from alembic import context

config = context.config

# Override URL from environment
db_url = os.environ.get(
    "DATABASE_URL",
    "postgresql://ctuser:ctpass@postgres:5432/clinical_trials"
)
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()