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
# Hosted migrations use a dedicated non-superuser owner.  Local development
# remains backward compatible with the ordinary DB_* credentials.
owner_user = os.getenv("DB_OWNER_USER")
owner_password = os.getenv("DB_OWNER_PASSWORD")
if bool(owner_user) != bool(owner_password):
    raise RuntimeError("DB_OWNER_USER and DB_OWNER_PASSWORD must be set together")
if os.getenv("DEPLOYMENT_MODE") in {"shared_private", "hosted_public"} and not owner_user:
    raise RuntimeError("Hosted migrations require DB_OWNER_USER and DB_OWNER_PASSWORD")

user = owner_user or os.getenv("DB_USER")
password = owner_password or os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST", "127.0.0.1")
port = os.getenv("DB_PORT", 5432)
database = os.getenv("DB_NAME")
encoding = os.getenv("DB_CLIENT_ENCODING", "utf8")

if not all([user, password, database]):
    raise RuntimeError("Must set database user/password and DB_NAME - missing in environment")

database_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}?client_encoding={encoding}"

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


#: Indexes that exist in the database but deliberately not in
#: ``Base.metadata``, so autogenerate has nothing to compare them against and
#: reports each as a spurious ``remove_index`` on every run.
#:
#: Both are built on RDKit cartridge functions and cannot be round-tripped:
#: ``ix_species_entry_mol_gist`` is a GiST index over a ``mol`` column whose
#: type autogenerate cannot determine, and ``ix_species_formula_lookup`` is an
#: expression index whose DDL-time inlining quirk makes declaring it via
#: ``Index(..., text(...))`` risk autogenerate recreating it in a form that
#: fails. Each migration that creates one says so and asks that it never be
#: auto-dropped -- see ``d4e5f6a7b8c9`` and ``94daa2c345fb``.
#:
#: Excluding them is what lets ``alembic check`` return clean, which is what
#: lets schema drift be wired to readiness (stage 5). The cost is real and
#: bounded: genuine drift in *these two* indexes is now invisible to
#: autogenerate. That is an acceptable trade only because neither is generated
#: from the models -- their definition lives in one migration each and changes
#: only by someone writing a new one -- and the alternative is a permanent
#: false positive that makes the check unusable as a gate, which is strictly
#: worse than a narrow blind spot nobody can act on anyway.
MIGRATION_ONLY_INDEXES: frozenset[str] = frozenset(
    {
        "ix_species_entry_mol_gist",
        "ix_species_formula_lookup",
    }
)


def include_object(object_, name, type_, reflected, compare_to):
    """Keep migration-only RDKit indexes out of autogenerate comparison.

    Scoped as narrowly as it can be: only indexes, only these two names, and
    only when reflected from the database (a same-named object appearing in
    the models is a real change and must still be reported).
    """
    if type_ == "index" and name in MIGRATION_ONLY_INDEXES and reflected:
        return False
    return True

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
        include_object=include_object,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
