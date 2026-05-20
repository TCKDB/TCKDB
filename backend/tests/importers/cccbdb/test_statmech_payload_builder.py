"""Builder tests for the CCCBDB statmech payload."""

from __future__ import annotations

from app.importers.cccbdb.builders.species_payload import (
    build_species_entry_identity_payload,
)
from app.importers.cccbdb.builders.statmech_payload import (
    build_statmech_payload,
)


def _build(record):
    warnings: list[str] = []
    refs: dict[str, dict[str, object]] = {}
    unparsed: dict[str, object] = {}
    species_payload, _ = build_species_entry_identity_payload(
        record.identity, warnings
    )
    payload = build_statmech_payload(
        record, species_payload, warnings, refs, unparsed
    )
    return payload, warnings, refs, unparsed


class TestH2OStatmech:
    def test_point_group_and_symmetry_first_class(self, h2o_record):
        payload, *_ = _build(h2o_record)
        assert payload is not None
        assert payload["scientific_origin"] == "experimental"
        assert payload["point_group"] == "C2v"
        assert payload["external_symmetry"] == 2

    def test_frequencies_preserved_in_unparsed(self, h2o_record):
        _, warnings, _refs, unparsed = _build(h2o_record)
        modes = unparsed["statmech_frequencies"]
        assert len(modes) == 3
        assert modes[0]["frequency_cm1"] == 3657
        assert modes[0]["raw_units"] == "cm^-1"
        assert modes[0]["symmetry_label"] == "A1"
        # Warning text must point a reader at the side-channel.
        assert any(
            "calc_freq_mode" in w and "statmech_frequencies" in w
            for w in warnings
        )

    def test_rotational_constants_preserved_in_unparsed(self, h2o_record):
        _, warnings, _refs, unparsed = _build(h2o_record)
        rc = unparsed["statmech_rotational_constants"]
        assert rc["a_ghz"] == 835.840
        assert rc["b_ghz"] == 435.351
        assert rc["c_ghz"] == 278.140
        assert rc["raw_units"] == "GHz"
        assert any(
            "rotational constants" in w
            and "statmech_rotational_constants" in w
            for w in warnings
        )


class TestH2Statmech:
    def test_d_star_h_point_group_preserved(self, h2_record):
        payload, *_ = _build(h2_record)
        # Linear-molecule "D*h" preserved verbatim.
        assert payload["point_group"] == "D*h"
        assert payload["external_symmetry"] == 2

    def test_single_mode_in_unparsed(self, h2_record):
        _, _, _, unparsed = _build(h2_record)
        modes = unparsed["statmech_frequencies"]
        assert len(modes) == 1
        assert modes[0]["frequency_cm1"] == 4401.21


class TestBenzeneStatmech:
    def test_point_group_and_symmetry(self, benzene_record):
        payload, *_ = _build(benzene_record)
        assert payload["point_group"] == "D6h"
        assert payload["external_symmetry"] == 12

    def test_no_frequencies_or_rotational(self, benzene_record):
        _, warnings, _refs, unparsed = _build(benzene_record)
        assert "statmech_frequencies" not in unparsed
        assert "statmech_rotational_constants" not in unparsed
        # Benzene fixture has no freqs/RC — should produce no
        # side-channel warnings from statmech.
        assert not any("statmech" in w for w in warnings)


class TestStatmechEdgeCases:
    def test_returns_none_when_no_statmech_fields(self):
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
