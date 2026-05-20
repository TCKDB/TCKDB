"""Top-level builder: CCCBDB experimental species record → all payloads.

Assembles the per-section builders into a single :class:`BuildResult`.
"""

from __future__ import annotations

from app.importers.cccbdb.builders.common import (
    BuildResult,
    external_source_from_record,
)
from app.importers.cccbdb.builders.geometry_payload import (
    build_geometry_payload,
)
from app.importers.cccbdb.builders.species_payload import (
    build_species_entry_identity_payload,
)
from app.importers.cccbdb.builders.statmech_payload import (
    build_statmech_payload,
)
from app.importers.cccbdb.builders.thermo_payload import (
    build_thermo_payload,
)
from app.importers.cccbdb.models import CCCBDBExperimentalSpeciesRecord


def build_experimental_species_payload(
    record: CCCBDBExperimentalSpeciesRecord,
) -> BuildResult:
    """Build all TCKDB-compatible payloads for one CCCBDB record.

    :param record: A parser record produced by
        :func:`app.importers.cccbdb.parsers.parse_experimental_species_page`.
    :returns: A :class:`BuildResult` with optional ``species_entry``,
        ``thermo``, ``statmech``, ``geometry`` payload dicts, plus
        :class:`ExternalSourceMetadata` carrying CCCBDB-level
        provenance and per-value reference labels, plus a list of
        ``warnings`` enumerating parsed values that have no
        first-class TCKDB destination.

    The builder is pure: it does not write to the database, does not
    contact CCCBDB, and produces deterministic output for the same
    input record.
    """

    warnings: list[str] = []
    per_value_refs: dict[str, dict[str, object]] = {}
    unparsed: dict[str, object] = {}

    species_entry_payload, species_valid = build_species_entry_identity_payload(
        record.identity, warnings
    )

    thermo_payload = build_thermo_payload(
        record,
        species_entry_payload,
        warnings,
        per_value_refs,
        unparsed,
    )
    statmech_payload = build_statmech_payload(
        record,
        species_entry_payload,
        warnings,
        per_value_refs,
        unparsed,
    )
    geometry_payload = build_geometry_payload(record)

    external_source = external_source_from_record(record)
    external_source.per_value_references = per_value_refs
    external_source.unparsed = unparsed

    return BuildResult(
        species_entry_payload=species_entry_payload,
        species_entry_payload_is_valid=species_valid,
        thermo_payload=thermo_payload,
        statmech_payload=statmech_payload,
        geometry_payload=geometry_payload,
        external_source=external_source,
        warnings=warnings,
    )
