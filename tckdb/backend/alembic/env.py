# /code/tckdb/backend/app/alembic/env.py

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the app directory to the Python path to allow imports
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))
# Add at the top of env.py
print("Alembic is configuring migrations...")
from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt

print(f"Imported models: {Base.metadata.tables.keys()}")
# This is the Alembic Config object, which provides access to the values within the .ini file
config = context.config

# Interpret the config file for Python logging.
fileConfig(config.config_file_name)

# Set the SQLAlchemy URL from environment variables if not already set
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", os.getenv("SQLALCHEMY_DATABASE_URI"))

# Set target_metadata to your models' metadata
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """
    Custom render function to handle custom types
    """
    if type_ == "type" and isinstance(obj, MsgpackExt):
        autogen_context.imports.add(
            "from tckdb.backend.app.models.common import MsgpackExt"
        )
        return "MsgpackExt()"
    return False


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
