# tckdb/backend/app/tests/conftest.py

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.level import Level
from tckdb.backend.app.models.ess import ESS
from tckdb.backend.app.models.encorr import EnCorr
from tckdb.backend.app.models.freq import Freq
from tckdb.backend.app.db.session import get_db
from tckdb.backend.app.main import app
from fastapi.testclient import TestClient
from dotenv import load_dotenv
import os
from pathlib import Path
from alembic.config import Config
from alembic import command

# Load environment variables from .env.test
load_dotenv(dotenv_path="./tckdb/backend/app/core/.env.test")

API_V1_STR = "/api/v1"
POSTGRES_USER = os.getenv("POSTGRES_USER", "test_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "test_pass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "test_db")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5434")

SQLALCHEMY_DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session")
def setup_database():
    """
    Fixture to set up and tear down the database using Alembic migrations.
    It runs automatically for the module.
    """
    # Construct the absolute path to alembic.ini
    BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # Adjust based on your directory structure
    ALEMBIC_INI = BASE_DIR / "alembic.ini"

    # Ensure the alembic.ini file exists
    assert ALEMBIC_INI.exists(), f"Alembic config file not found at {ALEMBIC_INI}"

    # Configure Alembic
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)  # Override URL

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
            result = connection.execute("SELECT to_regclass('public.level');")
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
def test_level(db_session):
    """Create a temporary Level entry."""
    level = Level(
        method="B3LYP",
        basis="6-31G(d,p)",
        dispersion="gd3bj"
    )
    db_session.add(level)
    db_session.commit()
    db_session.refresh(level)
    return level

@pytest.fixture(scope="class")
def test_ess(db_session):
    """Create a temporary ESS entry."""
    ess = ESS(
        name="TestESS",
        version="1.0",
        revision="A",
        url="http://testess.example.com"
    )
    db_session.add(ess)
    db_session.commit()
    db_session.refresh(ess)
    return ess

@pytest.fixture(scope="class")
def test_encorr(db_session, test_level: Level):
    """
    Fixture to create a test EnCorr record.
    """
    encorr = EnCorr(
        level_id=test_level.id,
        supported_elements=['H', 'C', 'N', 'O', 'S'],
        energy_unit='Hartree',
        aec={'H': -0.499459, 'C': -37.786694, 'N': -54.524279,
             'O': -74.992097, 'S': -397.648733},
        bac={'C-H': -0.46, 'C-C': -0.68, 'C=C': -1.9, 'C#C': -3.13,
             'O-H': -0.51, 'C-O': -0.23, 'C=O': -0.69, 'O-O': -0.02,
             'C-N': -0.67, 'C=N': -1.46, 'C#N': -2.79, 'N-O': 0.74,
             'N_O': -0.23, 'N=O': -0.51, 'N-H': -0.69, 'N-N': -0.47,
             'N=N': -1.54, 'N#N': -2.05, 'S-H': 0.87, 'C-S': 0.42,
             'C=S': 0.51, 'S-S': 0.86, 'O-S': 0.23, 'O=S': -0.53},
        reviewer_flags={}
    )
    db_session.add(encorr)
    db_session.commit()
    db_session.refresh(encorr)
    return encorr

@pytest.fixture(scope="class")
def test_freq(db_session, test_level):
    """
    Create a temporary Freq entry.
    """
    freq = Freq(
        factor=1.0,
        level_id=test_level.id,
        source="Test source",
        reviewer_flags={}
    )
    db_session.add(freq)
    db_session.commit()
    db_session.refresh(freq)
    return freq

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
