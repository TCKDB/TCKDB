# tckdb/backend/app/tests/conftest.py


import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

# from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tckdb.backend.app.db.query import SoftDeleteQuery
from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.main import app


API_V1_STR = "/api/v1"
POSTGRES_USER = os.getenv("POSTGRES_USER", "test_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "test_pass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "test_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5434")

SQLALCHEMY_DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, query_cls=SoftDeleteQuery
)


@pytest.fixture(scope="session")
def setup_database():
    """
    Fixture to set up and tear down the database using Alembic migrations.
    It runs automatically for the module.
    """
    # Construct the absolute path to alembic.ini
    BASE_DIR = (
        Path(__file__).resolve().parent.parent.parent.parent
    )  # Adjust based on your directory structure
    ALEMBIC_INI = BASE_DIR / "alembic.ini"
    print(BASE_DIR)
    # Ensure the alembic.ini file exists
    assert ALEMBIC_INI.exists(), f"Alembic config file not found at {ALEMBIC_INI}"

    # Configure Alembic
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option(
        "sqlalchemy.url", SQLALCHEMY_DATABASE_URL
    )  # Override URL

    # Run migrations
    try:
        command.upgrade(alembic_cfg, "head")
        print("Alembic migrations applied.")
    except Exception as e:
        print(f"Error applying migrations: {e}")
        raise

    # **Debugging: Check if 'level' table exists**
    try:
        with engine.connect() as connection:
            statement = text("SELECT to_regclass('public.level');")
            result = connection.execute(statement)
            table_exists = result.fetchone()[0] is not None
            if table_exists:
                print("Table 'level' exists in the database.")
            else:
                print("Table 'level' does NOT exist in the database.")
                raise Exception("Migration did not create 'level' table.")
    except Exception as e:
        print(f"Error during migration verification: {e}")
        raise

    yield

    # Downgrade migrations after tests
    try:
        command.downgrade(alembic_cfg, "base")
        print("Alembic migrations downgraded.")
    except Exception as e:
        print(f"Error downgrading migrations: {e}")
        raise


@pytest.fixture(scope="class")
def db_session(setup_database):
    """Create a new database session for a test module."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture(scope="class")
def client(db_session):
    """Provide a TestClient with overridden dependencies."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # Set an environment variable to indicate testing
    os.environ["TESTING"] = "True"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    del os.environ["TESTING"]
