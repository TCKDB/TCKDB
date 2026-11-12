"""The Alembic revision chain as it exists on disk.

Tests that drive ``alembic.command`` ask the script directory for the revision ids they need rather than writing them
down. The chain is a single revision, so base is empty and head is that revision.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def script_directory() -> ScriptDirectory:
    """The migration chain on disk, read once per process."""
    return ScriptDirectory.from_config(Config(str(_BACKEND_ROOT / "alembic.ini")))


def current_head() -> str:
    """The revision ``alembic upgrade head`` stops at."""
    return script_directory().get_current_head()


def base_revision() -> str:
    """The revision with no parent: the first one ``alembic upgrade`` applies."""
    (base,) = script_directory().get_bases()
    return base
