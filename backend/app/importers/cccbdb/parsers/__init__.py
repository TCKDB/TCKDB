"""CCCBDB HTML parsers (Phase 1: experimental species pages only)."""

from app.importers.cccbdb.parsers.experimental_property_table import (
    PROPERTY_CONFIGS,
    parse_experimental_property_table_page,
)
from app.importers.cccbdb.parsers.experimental_species import (
    parse_experimental_species_page,
)

__all__ = [
    "PROPERTY_CONFIGS",
    "parse_experimental_property_table_page",
    "parse_experimental_species_page",
]
