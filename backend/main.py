"""TCKDB API entry point.

Start with::

    uvicorn main:app --reload
"""

from app.api.app import create_app

app = create_app()
