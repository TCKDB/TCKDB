"""Builder tests for the CCCBDB statmech payload."""

from __future__ import annotations

import pytest

from app.importers.cccbdb.builders.species_payload import (
    build_species_entry_identity_payload,
)
from app.importers.cccbdb.builders.statmech_payload import (
    _GHZ_PER_CM1,
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

    def test_rotational_constants_mapped_to_first_class_cm1(self, h2o_record):
        payload, _, _, unparsed = _build(h2o_record)
        # Asymmetric top: all three axes convert GHz→cm⁻¹.
        assert payload["rotational_constant_a_cm1"] == pytest.approx(
            835.840 / _GHZ_PER_CM1
        )
        assert payload["rotational_constant_b_cm1"] == pytest.approx(
            435.351 / _GHZ_PER_CM1
        )
        assert payload["rotational_constant_c_cm1"] == pytest.approx(
            278.140 / _GHZ_PER_CM1
        )
        # Independent literal pin (not derived from _GHZ_PER_CM1): a wrong
        # constant *value*, not just a wrong direction, must fail. 835.840
        # GHz / 29.9792458 = 27.8807 cm⁻¹ (matches H₂O's known A₀ ≈ 27.88).
        assert payload["rotational_constant_a_cm1"] == pytest.approx(
            27.8807, abs=1e-3
        )
        # Raw GHz values remain in the unparsed side-channel.
        assert unparsed["statmech_rotational_constants"]["a_ghz"] == 835.840


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


def _synthetic_record(rotational_constants):
    from app.importers.cccbdb.models import (
        CCCBDBExperimentalSpeciesRecord,
        CCCBDBSourceMetadata,
        CCCBDBSpeciesIdentity,
        CCCBDBStatmechRecord,
    )

    return CCCBDBExperimentalSpeciesRecord(
        identity=CCCBDBSpeciesIdentity(),
        source_metadata=CCCBDBSourceMetadata(
            source="CCCBDB",
            source_release="22",
            source_database_doi="10.18434/T47C7Z",
            source_url="https://example.invalid/synthetic",
            source_record_key="synthetic",
            content_sha256="0" * 64,
            parser_version="test",
        ),
        statmech=CCCBDBStatmechRecord(
            rotational_constants=rotational_constants,
        ),
    )


class TestStatmechRotationalLinear:
    def test_linear_only_b_maps_single_axis(self):
        from app.importers.cccbdb.models import CCCBDBRotationalConstants

        record = _synthetic_record(
            CCCBDBRotationalConstants(
                b_ghz=57.635,
                raw_units="GHz",
                raw_values=["57.635"],
            )
        )
        payload, _warnings, _refs, unparsed = _build(record)
        # Linear molecule: only the B axis is present → only _b_cm1 set.
        assert payload["rotational_constant_b_cm1"] == pytest.approx(
            57.635 / _GHZ_PER_CM1
        )
        assert "rotational_constant_a_cm1" not in payload
        assert "rotational_constant_c_cm1" not in payload
        # Raw GHz still carried in the unparsed side-channel.
        assert unparsed["statmech_rotational_constants"]["b_ghz"] == 57.635
        assert unparsed["statmech_rotational_constants"]["a_ghz"] is None


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
