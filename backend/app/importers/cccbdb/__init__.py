"""CCCBDB importer package (Phase 1 prototype).

Phase 1 ships a fixture-driven parser for CCCBDB experimental species
pages and produces normalized in-memory records. It does **not** write
to the database and does **not** crawl CCCBDB live.

See ``backend/docs/specs/cccbdb_importer.md`` for the design spec and
``backend/app/importers/cccbdb/README.md`` for usage and roadmap.
"""

from __future__ import annotations

PARSER_VERSION = "cccbdb-experimental-species-parser/0.1.0"
SOURCE_NAME = "CCCBDB"
SOURCE_RELEASE = "22"
SOURCE_DATABASE_DOI = "10.18434/T47C7Z"

__all__ = [
    "PARSER_VERSION",
    "SOURCE_DATABASE_DOI",
    "SOURCE_NAME",
    "SOURCE_RELEASE",
]
