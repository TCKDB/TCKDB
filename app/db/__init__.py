"""Database package — ORM model registrations live in ``app.db.models``.

Intentionally empty: SQLAlchemy model modules must be imported in
``app/db/models/__init__.py`` so that ``Base.metadata`` sees the full mapper
graph. Alembic picks this up via ``alembic/env.py`` (``import app.db.models``).
"""
