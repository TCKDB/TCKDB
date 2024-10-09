"""
TCKDB session module
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker, Session

from tckdb.backend.app.core import config
from tckdb.backend.app.db.query import SoftDeleteQuery


engine = create_engine(config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
# db_session = scoped_session(
#     sessionmaker(autocommit=False, autoflush=False, bind=engine)
# )
SessionLocal = sessionmaker(autocommit=False,
                            autoflush=False,
                            bind=engine,
                            query_cls=SoftDeleteQuery)

db_session = scoped_session(SessionLocal)


def get_db():
    """
    Dependency function to provide a SQLAlchemy database session

    Yields:
        Session: A SQLAlchemy database session object
    """
    db = db_session()
    try:
        yield db
    finally:
        db.close()
