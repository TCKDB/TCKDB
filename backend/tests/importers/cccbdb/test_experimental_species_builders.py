"""Integration tests for the top-level CCCBDB experimental builder.

Where the existing TCKDB upload schemas can be instantiated cleanly
(no DB side effects on import), the built payloads are validated
against them. Failing those instantiations would mean the importer
output drifted away from the workflow contract.
"""

from __future__ import annotations

import pytest

from app.importers.cccbdb.builders import build_experimental_species_payload


def _build(record):
    return build_experimental_species_payload(record)


# ---------------------------------------------------------------------------
# H2 — no SMILES, so species identity is partial and not validatable.
# ---------------------------------------------------------------------------


class TestH2TopLevel:
    def test_species_payload_partial_with_warning(self, h2_record):
        result = _build(h2_record)
        assert result.species_entry_payload is not None
        assert result.species_entry_payload_is_valid is False
        # term_symbol_raw should hold the raw electronic state.
        assert (
            result.species_entry_payload["term_symbol_raw"] == "X 1Sigmag+"
        )
        assert any(
            "SpeciesEntryIdentityPayload" in w and "smiles" in w
            for w in result.warnings
        )

    def test_thermo_and_statmech_payloads_present(self, h2_record):
        result = _build(h2_record)
        assert result.thermo_payload is not None
        assert result.statmech_payload is not None

    def test_geometry_payload_absent(self, h2_record):
        result = _build(h2_record)
        assert result.geometry_payload is None

    def test_external_source_provenance(self, h2_record):
        result = _build(h2_record)
        es = result.external_source
        assert es.name == "CCCBDB"
        assert es.release == "22"
        assert es.doi == "10.18434/T47C7Z"
        assert es.page_kind == "experimental_species"
        assert len(es.content_sha256) == 64
        assert es.parser_version


# ---------------------------------------------------------------------------
# H2O — full identity, exercises schema-level validation.
# ---------------------------------------------------------------------------


class TestH2OTopLevel:
    def test_species_payload_validates(self, h2o_record):
        result = _build(h2o_record)
        assert result.species_entry_payload_is_valid is True
        assert result.species_entry_payload["smiles"] == "O"
        assert result.species_entry_payload["charge"] == 0
        assert result.species_entry_payload["multiplicity"] == 1
        assert result.species_entry_payload["term_symbol_raw"] == "X 1A1"

    def test_species_entry_payload_against_schema(self, h2o_record):
        """The built identity dict must validate against the real
        ``SpeciesEntryIdentityPayload`` model, otherwise the importer
        and the upload workflow have drifted."""
        from tckdb_schemas.fragments.identity import (
            SpeciesEntryIdentityPayload,
        )

        result = _build(h2o_record)
        SpeciesEntryIdentityPayload.model_validate(
            result.species_entry_payload
        )

    def test_thermo_payload_against_thermo_upload_request(self, h2o_record):
        from app.schemas.workflows.thermo_upload import ThermoUploadRequest

        result = _build(h2o_record)
        # Real workflow request must accept the built payload.
        req = ThermoUploadRequest.model_validate(result.thermo_payload)
        assert req.scientific_origin.value == "experimental"
        assert req.h298_kj_mol == pytest.approx(-241.826)
        assert req.s298_j_mol_k == pytest.approx(188.834)
        assert len(req.points) == 1
        assert req.points[0].temperature_k == pytest.approx(298.15)
        assert req.points[0].cp_j_mol_k == pytest.approx(33.590)

    def test_statmech_payload_against_statmech_upload_request(self, h2o_record):
        from app.schemas.workflows.statmech_upload import (
            StatmechUploadRequest,
        )

        result = _build(h2o_record)
        req = StatmechUploadRequest.model_validate(result.statmech_payload)
        assert req.point_group == "C2v"
        assert req.external_symmetry == 2
        assert req.scientific_origin.value == "experimental"

    def test_geometry_xyz_well_formed(self, h2o_record):
        result = _build(h2o_record)
        assert result.geometry_payload is not None
        # First line is the natoms count, integer parseable.
        first = result.geometry_payload["xyz_text"].splitlines()[0]
        assert int(first) == 3

    def test_unparsed_holds_rotational_and_frequencies(self, h2o_record):
        result = _build(h2o_record)
        assert "statmech_rotational_constants" in result.external_source.unparsed
        assert "statmech_frequencies" in result.external_source.unparsed

    def test_per_value_references_keyed_by_property_kind(self, h2o_record):
        result = _build(h2o_record)
        refs = result.external_source.per_value_references
        assert refs["hf_298"]["reference_label"] == "Gurvich"
        assert refs["s_298"]["reference_label"] == "Gurvich"


# ---------------------------------------------------------------------------
# Benzene — already-converted kcal-derived values must not be re-converted.
# ---------------------------------------------------------------------------


class TestBenzeneTopLevel:
    def test_thermo_value_is_parser_converted_value(self, benzene_record):
        from app.schemas.workflows.thermo_upload import ThermoUploadRequest

        result = _build(benzene_record)
        req = ThermoUploadRequest.model_validate(result.thermo_payload)
        # Value already in kJ/mol from Phase 1. Builder must not re-convert.
        assert req.h298_kj_mol == pytest.approx(19.820 * 4.184)
        assert req.h298_uncertainty_kj_mol == pytest.approx(0.120 * 4.184)

    def test_state_label_preserved_in_term_symbol_raw(self, benzene_record):
        result = _build(benzene_record)
        assert (
            result.species_entry_payload["term_symbol_raw"]
            == "X 1A1g (planar, D6h)"
        )

    def test_per_value_references_distinct_for_pedley_and_trc(
        self, benzene_record
    ):
        result = _build(benzene_record)
        refs = result.external_source.per_value_references
        assert refs["hf_298"]["reference_label"] == "Pedley"
        assert refs["s_298"]["reference_label"] == "TRC"
        assert refs["cp_298"]["reference_label"] == "TRC"

    def test_no_unparsed_for_benzene(self, benzene_record):
        # Benzene fixture has no rotational/freqs/hf_0, so nothing
        # should leak into the unparsed side-channel.
        result = _build(benzene_record)
        assert result.external_source.unparsed == {}


# ---------------------------------------------------------------------------
# General builder invariants
# ---------------------------------------------------------------------------


class TestBuilderInvariants:
    def test_deterministic_output(self, h2o_record):
        a = _build(h2o_record).model_dump()
        b = _build(h2o_record).model_dump()
        assert a == b

    def test_warnings_are_deterministic(self, h2o_record):
        w_a = _build(h2o_record).warnings
        w_b = _build(h2o_record).warnings
        assert w_a == w_b

    def test_warnings_explain_every_unparsed_key(self, h2o_record):
        result = _build(h2o_record)
        for key in result.external_source.unparsed:
            # Every unparsed entry must have at least one corresponding
            # warning so a downstream maintainer notices it.
            assert any(key in w for w in result.warnings), key

    def test_missing_optional_sections_do_not_crash(self):
        from app.importers.cccbdb.models import (
            CCCBDBExperimentalSpeciesRecord,
            CCCBDBSourceMetadata,
            CCCBDBSpeciesIdentity,
        )

        record = CCCBDBExperimentalSpeciesRecord(
            identity=CCCBDBSpeciesIdentity(),
            source_metadata=CCCBDBSourceMetadata(
                source="CCCBDB",
                source_release="22",
                source_database_doi="10.18434/T47C7Z",
                source_url="https://example.invalid/empty",
                source_record_key="empty",
                content_sha256="0" * 64,
                parser_version="test",
            ),
        )
        result = build_experimental_species_payload(record)
        assert result.thermo_payload is None
        assert result.statmech_payload is None
        assert result.geometry_payload is None
        # External source metadata is still populated.
        assert result.external_source.name == "CCCBDB"
