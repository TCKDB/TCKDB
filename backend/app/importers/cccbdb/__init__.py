"""CCCBDB importer package (Phase 1 prototype).

Phase 1 ships a fixture-driven parser for CCCBDB experimental species
pages and produces normalized in-memory records. It does **not** write
to the database and does **not** crawl CCCBDB live.

See ``backend/docs/specs/cccbdb_importer.md`` for the design spec and
``backend/app/importers/cccbdb/README.md`` for usage and roadmap.
"""

from __future__ import annotations

from app.services.parser_registry import PARSERS

PARSER_NAME = "cccbdb_experimental_species"
PARSER_VERSION = str(PARSERS.get(PARSER_NAME).version)
SOURCE_NAME = "CCCBDB"
SOURCE_RELEASE = "22"
SOURCE_DATABASE_DOI = "10.18434/T47C7Z"

__all__ = [
    "PARSER_NAME",
    "PARSER_VERSION",
    "SOURCE_DATABASE_DOI",
    "SOURCE_NAME",
    "SOURCE_RELEASE",
]
