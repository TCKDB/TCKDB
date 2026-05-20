"""Builder tests for the CCCBDB thermo payload."""

from __future__ import annotations

import pytest

from app.importers.cccbdb.builders.species_payload import (
    build_species_entry_identity_payload,
)
from app.importers.cccbdb.builders.thermo_payload import build_thermo_payload


def _build(record):
    warnings: list[str] = []
    per_value_refs: dict[str, dict[str, object]] = {}
    unparsed: dict[str, object] = {}
    species_payload, _ = build_species_entry_identity_payload(
        record.identity, warnings
    )
    payload = build_thermo_payload(
        record, species_payload, warnings, per_value_refs, unparsed
    )
    return payload, warnings, per_value_refs, unparsed


class TestH2Thermo:
    def test_scientific_origin_experimental(self, h2_record):
        payload, *_ = _build(h2_record)
        assert payload is not None
        assert payload["scientific_origin"] == "experimental"

    def test_h298_and_s298_first_class_fields(self, h2_record):
        payload, *_ = _build(h2_record)
        assert payload["h298_kj_mol"] == pytest.approx(0.0)
        assert payload["s298_j_mol_k"] == pytest.approx(130.680)
        assert payload["s298_uncertainty_j_mol_k"] == pytest.approx(0.003)

    def test_cp_298_lands_on_thermo_point(self, h2_record):
        payload, *_ = _build(h2_record)
        points = payload["points"]
        assert len(points) == 1
        assert points[0]["temperature_k"] == pytest.approx(298.15)
        assert points[0]["cp_j_mol_k"] == pytest.approx(28.836)

    def test_h_298_minus_h_0_lands_on_same_point(self, h2_record):
        payload, *_ = _build(h2_record)
        point = payload["points"][0]
        assert point["h_kj_mol"] == pytest.approx(8.468)

    def test_hf_0_preserved_in_unparsed(self, h2_record):
        payload, warnings, _refs, unparsed = _build(h2_record)
        assert "hf_0" in unparsed
        assert unparsed["hf_0"]["canonical_units"] == "kJ/mol"
        assert any("hf_0" in w for w in warnings)

    def test_per_value_refs_preserved(self, h2_record):
        _, _, refs, _ = _build(h2_record)
        assert refs["hf_298"]["reference_label"] == "Gurvich"
        assert refs["s_298"]["reference_label"] == "Gurvich"
        assert refs["cp_298"]["reference_label"] == "Gurvich"


class TestBenzeneThermo:
    def test_kcal_converted_value_lands_in_kj(self, benzene_record):
        payload, *_ = _build(benzene_record)
        # 19.820 kcal/mol → 82.928 kJ/mol (already converted by parser).
        assert payload["h298_kj_mol"] == pytest.approx(19.820 * 4.184)

    def test_uncertainty_also_converted_to_kj(self, benzene_record):
        payload, *_ = _build(benzene_record)
        assert payload["h298_uncertainty_kj_mol"] == pytest.approx(
            0.120 * 4.184
        )

    def test_cal_per_mol_per_k_converted(self, benzene_record):
        payload, *_ = _build(benzene_record)
        assert payload["s298_j_mol_k"] == pytest.approx(64.340 * 4.184)

    def test_per_value_refs(self, benzene_record):
        _, _, refs, _ = _build(benzene_record)
        assert refs["hf_298"]["reference_label"] == "Pedley"
        assert refs["s_298"]["reference_label"] == "TRC"

    def test_no_hf_0_no_unparsed(self, benzene_record):
        _, _, _, unparsed = _build(benzene_record)
        assert "hf_0" not in unparsed


class TestThermoEdgeCases:
    def test_returns_none_when_no_values(self):
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
        payload, *_ = _build(record)
        assert payload is None
