"""TCKDB API entry point.

Start with::

    uvicorn main:app --reload
"""

from dotenv import load_dotenv

from app.api.config import ENV_FILE

# ``Settings`` reads ``backend/.env`` itself; this also reaches the values read straight from the environment
# (``S3_*``, ``TCKDB_INLINE_WORKER``). A variable already set in the environment wins.
load_dotenv(ENV_FILE, override=False)

from app.api.app import create_app  # noqa: E402

app = create_app()
