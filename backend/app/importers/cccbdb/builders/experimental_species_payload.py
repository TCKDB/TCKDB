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

    # A thermo or statmech upload payload is only workflow-ready when
    # its embedded ``species_entry`` is itself workflow-ready. The
    # builder still emits the partial payload (so callers can inspect
    # scientific values and per-value references), but the validity
    # flag is the single source of truth for round-trip validation.
    thermo_valid = thermo_payload is not None and species_valid
    statmech_valid = statmech_payload is not None and species_valid

    return BuildResult(
        species_entry_payload=species_entry_payload,
        species_entry_payload_is_valid=species_valid,
        thermo_payload=thermo_payload,
        molecular_property_observation_payloads=_enthalpy_observations(record),
        thermo_payload_is_valid=thermo_valid,
        statmech_payload=statmech_payload,
        statmech_payload_is_valid=statmech_valid,
        geometry_payload=geometry_payload,
        external_source=external_source,
        warnings=warnings,
    )


def _enthalpy_observations(record: CCCBDBExperimentalSpeciesRecord) -> list[dict]:
    """Route explicitly labelled sensible increments without relabelling them."""
    from app.schemas.entities.molecular_property_observation import MolecularPropertyObservationCreate

    meta = record.source_metadata
    observations = []
    for value in record.thermo.values:
        if value.property_kind != "h_298_minus_h_0":
            continue
        observation = MolecularPropertyObservationCreate(
            scientific_origin="experimental",
            property_kind="other",
            property_label="H(298.15 K) - H(0 K), sensible enthalpy increment",
            scalar_value=value.value,
            scalar_unit="kJ/mol",
            scalar_uncertainty=value.uncertainty,
            temperature_k=298.15,
            external_source_name=meta.source,
            external_source_release=meta.source_release,
            external_source_doi=meta.source_database_doi,
            external_source_url=meta.source_url,
            external_source_record_key=meta.source_record_key,
            external_source_parser_version=meta.parser_version,
            raw_payload_json={
                "identity_hint": record.identity.model_dump(mode="json"),
                "datum": value.model_dump(mode="json"),
            },
        )
        observations.append(observation.model_dump(mode="json"))
    return observations
