"""Parser tests for the CCCBDB experimental-species page parser.

All tests are fully offline and consume the bundled HTML fixtures under
``backend/app/importers/cccbdb/fixtures/``. No network is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.parsers import parse_experimental_species_page

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

H2_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=1333740"
H2O_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=7732185"
BENZENE_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=71432"


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def h2_record():
    return parse_experimental_species_page(
        _load("experimental_h2.html"), source_url=H2_URL
    )


@pytest.fixture(scope="module")
def h2o_record():
    return parse_experimental_species_page(
        _load("experimental_h2o.html"), source_url=H2O_URL
    )


@pytest.fixture(scope="module")
def benzene_record():
    return parse_experimental_species_page(
        _load("experimental_benzene.html"), source_url=BENZENE_URL
    )


# ---------------------------------------------------------------------------
# H2
# ---------------------------------------------------------------------------


class TestH2Identity:
    def test_name(self, h2_record):
        assert h2_record.identity.name == "Hydrogen"

    def test_formula(self, h2_record):
        assert h2_record.identity.formula == "H2"

    def test_inchi(self, h2_record):
        assert h2_record.identity.inchi == "InChI=1S/H2/h1H"

    def test_inchikey_uppercased(self, h2_record):
        assert h2_record.identity.inchikey == "UFHFLCQGNIYNRP-UHFFFAOYSA-N"

    def test_smiles_missing_is_none(self, h2_record):
        # H2 fixture intentionally omits SMILES.
        assert h2_record.identity.smiles is None

    def test_charge_multiplicity(self, h2_record):
        assert h2_record.identity.charge == 0
        assert h2_record.identity.multiplicity == 1

    def test_state_label_preserved(self, h2_record):
        assert h2_record.identity.state_label == "X 1Sigmag+"

    def test_other_names_parsed(self, h2_record):
        assert "Dihydrogen" in h2_record.identity.other_names
        assert "molecular hydrogen" in h2_record.identity.other_names


class TestH2Thermo:
    def test_has_expected_property_kinds(self, h2_record):
        kinds = {v.property_kind for v in h2_record.thermo.values}
        assert {"hf_298", "hf_0", "s_298", "cp_298", "h_298_minus_h_0"} <= kinds

    def test_hf_298_value_and_units(self, h2_record):
        hf = next(
            v for v in h2_record.thermo.values if v.property_kind == "hf_298"
        )
        assert hf.value == pytest.approx(0.0)
        assert hf.canonical_units == "kJ/mol"
        assert hf.raw_units == "kJ/mol"
        assert hf.temperature_k == pytest.approx(298.15)

    def test_s_298_units(self, h2_record):
        s = next(
            v for v in h2_record.thermo.values if v.property_kind == "s_298"
        )
        assert s.canonical_units == "J/mol/K"
        assert s.value == pytest.approx(130.680)
        assert s.uncertainty == pytest.approx(0.003)

    def test_missing_uncertainty_is_none(self, h2_record):
        cp = next(
            v for v in h2_record.thermo.values if v.property_kind == "cp_298"
        )
        assert cp.uncertainty is None

    def test_value_level_reference(self, h2_record):
        hf = next(
            v for v in h2_record.thermo.values if v.property_kind == "hf_0"
        )
        assert hf.reference is not None
        assert hf.reference.reference_label == "Gurvich"
        assert hf.reference.raw_reference_text == "Gurvich"


class TestH2Statmech:
    def test_point_group_and_symmetry(self, h2_record):
        assert h2_record.statmech.point_group == "D*h"
        assert h2_record.statmech.symmetry_number == 2

    def test_frequencies_normalized(self, h2_record):
        freqs = h2_record.statmech.frequencies
        assert len(freqs) == 1
        mode = freqs[0]
        assert mode.frequency_cm1 == pytest.approx(4401.21)
        assert mode.raw_units == "cm^-1"
        assert mode.symmetry_label == "Sigmag"

    def test_no_rotational_constants_for_diatomic_h2(self, h2_record):
        # The H2 fixture deliberately omits the rotational section to
        # exercise the missing-optional-section path.
        assert h2_record.statmech.rotational_constants is None


class TestH2Provenance:
    def test_source_metadata(self, h2_record):
        meta = h2_record.source_metadata
        assert meta.source == SOURCE_NAME
        assert meta.source_release == SOURCE_RELEASE
        assert meta.source_database_doi == SOURCE_DATABASE_DOI
        assert meta.source_url == H2_URL
        assert meta.parser_version == PARSER_VERSION
        assert meta.page_kind == "experimental_species"
        assert meta.content_sha256
        assert len(meta.content_sha256) == 64

    def test_content_sha256_is_deterministic(self):
        html = _load("experimental_h2.html")
        a = parse_experimental_species_page(html, source_url=H2_URL)
        b = parse_experimental_species_page(html, source_url=H2_URL)
        assert (
            a.source_metadata.content_sha256
            == b.source_metadata.content_sha256
        )

    def test_record_key_defaults_to_sha(self, h2_record):
        assert (
            h2_record.source_metadata.source_record_key
            == h2_record.source_metadata.content_sha256
        )


# ---------------------------------------------------------------------------
# H2O
# ---------------------------------------------------------------------------


class TestH2OIdentity:
    def test_inchi_inchikey_smiles(self, h2o_record):
        assert h2o_record.identity.inchi == "InChI=1S/H2O/h1H2"
        assert h2o_record.identity.inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert h2o_record.identity.smiles == "O"

    def test_other_names_split_on_semicolon(self, h2o_record):
        assert "Dihydrogen monoxide" in h2o_record.identity.other_names
        assert "oxidane" in h2o_record.identity.other_names

    def test_state_label(self, h2o_record):
        assert h2o_record.identity.state_label == "X 1A1"


class TestH2OStatmech:
    def test_point_group(self, h2o_record):
        assert h2o_record.statmech.point_group == "C2v"

    def test_rotational_constants(self, h2o_record):
        rc = h2o_record.statmech.rotational_constants
        assert rc is not None
        assert rc.a_ghz == pytest.approx(835.840)
        assert rc.b_ghz == pytest.approx(435.351)
        assert rc.c_ghz == pytest.approx(278.140)
        assert rc.raw_units == "GHz"

    def test_three_vibrational_modes(self, h2o_record):
        assert len(h2o_record.statmech.frequencies) == 3
        modes = [m.frequency_cm1 for m in h2o_record.statmech.frequencies]
        assert modes == pytest.approx([3657, 1595, 3756])


class TestH2OGeometry:
    def test_three_atoms_in_angstrom(self, h2o_record):
        geom = h2o_record.geometry
        assert geom is not None
        assert geom.raw_units == "angstrom"
        elements = [a.element for a in geom.atoms]
        assert elements == ["O", "H", "H"]

    def test_oxygen_z_coordinate(self, h2o_record):
        oxygen = h2o_record.geometry.atoms[0]
        assert oxygen.z_angstrom == pytest.approx(0.1173)


# ---------------------------------------------------------------------------
# Benzene
# ---------------------------------------------------------------------------


class TestBenzeneIdentityAndThermo:
    def test_identity(self, benzene_record):
        assert benzene_record.identity.name == "Benzene"
        assert benzene_record.identity.formula == "C6H6"
        assert benzene_record.identity.smiles == "c1ccccc1"
        # Raw state label kept verbatim, including the parenthetical.
        assert (
            benzene_record.identity.state_label
            == "X 1A1g (planar, D6h)"
        )

    def test_kcal_per_mol_normalized_to_kj_per_mol(self, benzene_record):
        hf = next(
            v
            for v in benzene_record.thermo.values
            if v.property_kind == "hf_298"
        )
        assert hf.raw_units == "kcal/mol"
        assert hf.canonical_units == "kJ/mol"
        assert hf.value == pytest.approx(19.820 * 4.184)
        # Uncertainty also converted into canonical units.
        assert hf.uncertainty == pytest.approx(0.120 * 4.184)

    def test_cal_per_mol_per_k_normalized(self, benzene_record):
        s = next(
            v
            for v in benzene_record.thermo.values
            if v.property_kind == "s_298"
        )
        assert s.raw_units == "cal/mol/K"
        assert s.canonical_units == "J/mol/K"
        assert s.value == pytest.approx(64.340 * 4.184)

    def test_reference_label_pedley(self, benzene_record):
        hf = next(
            v
            for v in benzene_record.thermo.values
            if v.property_kind == "hf_298"
        )
        assert hf.reference is not None
        assert hf.reference.reference_label == "Pedley"

    def test_no_geometry_section_returns_none(self, benzene_record):
        # Benzene fixture omits geometry; parser should not invent one.
        assert benzene_record.geometry is None

    def test_no_frequencies_section(self, benzene_record):
        assert benzene_record.statmech.frequencies == []
        assert benzene_record.statmech.rotational_constants is None


# ---------------------------------------------------------------------------
# Cross-cutting parser behavior
# ---------------------------------------------------------------------------


class TestParserBehavior:
    def test_empty_source_url_rejected(self):
        with pytest.raises(ValueError):
            parse_experimental_species_page(
                "<html></html>", source_url=""
            )

    def test_blank_page_does_not_crash(self):
        record = parse_experimental_species_page(
            "<html><body></body></html>",
            source_url="https://example.invalid/empty",
        )
        assert record.identity.name is None
        assert record.identity.formula is None
        assert record.thermo.values == []
        assert record.geometry is None
        # An empty page should produce a warning about missing identity.
        assert any("identity" in w for w in record.warnings)

    def test_unknown_property_label_produces_warning(self):
        html = """
            <html><body>
              <section id="thermo">
                <table>
                  <tr><th>Property</th><th>Value</th><th>Units</th></tr>
                  <tr><td>WeirdQuantity</td><td>1.0</td><td>kJ/mol</td></tr>
                </table>
              </section>
            </body></html>
        """
        record = parse_experimental_species_page(
            html, source_url="https://example.invalid/weird"
        )
        assert record.thermo.values == []
        assert any("WeirdQuantity" in w for w in record.warnings)

    def test_unsupported_unit_produces_warning_not_crash(self):
        html = """
            <html><body>
              <section id="thermo">
                <table>
                  <tr><th>Property</th><th>Value</th><th>Units</th></tr>
                  <tr><td>Hf(298.15 K)</td><td>1.0</td><td>rydberg</td></tr>
                </table>
              </section>
            </body></html>
        """
        record = parse_experimental_species_page(
            html, source_url="https://example.invalid/badunit"
        )
        assert record.thermo.values == []
        assert any(
            "unsupported unit 'rydberg'" in w for w in record.warnings
        )

    def test_explicit_record_key_overrides_sha(self):
        record = parse_experimental_species_page(
            _load("experimental_h2.html"),
            source_url=H2_URL,
            source_record_key="H2 / experimental",
        )
        assert (
            record.source_metadata.source_record_key == "H2 / experimental"
        )
        # SHA still populated for content addressing.
        assert record.source_metadata.content_sha256
