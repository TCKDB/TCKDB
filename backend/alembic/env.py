import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# Load in the environment variables
load_dotenv(verbose=True)  # Will make true for now

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

## Custom set env vars
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST", "127.0.0.1")
port = os.getenv("DB_PORT", 5432)
database = os.getenv("DB_NAME")
encoding = os.getenv("DB_CLIENT_ENCODING", "utf8")

if not all([user, password, database]):
    raise RuntimeError("Must set DB_USER, DB_PASSWORD, DB_NAME - missing in .env")

database_url = (
    f"postgresql+psycopg://{user}:{password}"
    f"@{host}:{port}/{database}"
    f"?client_encoding={encoding}"
)

config.set_main_option("sqlalchemy.url", database_url)
###

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import Base and all model modules so autogenerate can detect changes
import app.db.models  # noqa: F401, E402 — registers all models with Base.metadata
from app.db.base import Base  # noqa: E402

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
