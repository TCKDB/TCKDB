"""Builder tests for ``build_molecular_property_payloads_from_property_table``.

Covers: property-kind mapping (incl. hf_0 → enthalpy_of_formation NOT
atomization_energy), unit + uncertainty propagation, catalog
identity-enrichment, ambiguous-match policy, raw-payload forensic
fields, and the missing-value / missing-unit guards.

All tests offline; no DB writes; uses Phase 1 fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.importers.cccbdb.builders import (
    build_molecular_property_payloads_from_property_table,
)
from app.importers.cccbdb.models import (
    CCCBDBExperimentalPropertyRow,
    CCCBDBExperimentalPropertyTable,
    CCCBDBMoleculeCatalog,
    CCCBDBPropertyTableSourceMetadata,
)
from app.importers.cccbdb.parsers import (
    parse_experimental_property_table_page,
    parse_molecule_catalog_page,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


@pytest.fixture(scope="module")
def catalog() -> CCCBDBMoleculeCatalog:
    return parse_molecule_catalog_page(
        (FIXTURES_DIR / "catalog_inchix.html").read_text(encoding="utf-8"),
        source_url="https://cccbdb.nist.gov/inchix.asp",
    )


def _table(fixture: str, kind: str):
    return parse_experimental_property_table_page(
        (FIXTURES_DIR / fixture).read_text(encoding="utf-8"),
        property_kind=kind,
        source_url=f"https://cccbdb.nist.gov/{fixture}",
    )


# ---------------------------------------------------------------------------
# Property-kind mapping
# ---------------------------------------------------------------------------


class TestPropertyKindMapping:
    def test_hf_0_maps_to_enthalpy_of_formation_not_atomization(self):
        """hf_0 is enthalpy of formation at 0 K, NOT atomization energy.
        Conflating these would corrupt downstream science."""

        table = _table("property_hf_0.html", "hf_0")
        results = build_molecular_property_payloads_from_property_table(table)
        for r in results:
            assert r.payload is not None
            assert (
                r.payload.property_kind
                == MolecularPropertyKind.enthalpy_of_formation
            )
            assert (
                r.payload.property_kind
                != MolecularPropertyKind.atomization_energy
            )
            # First-class kind, no override label needed.
            assert r.payload.property_label is None

    def test_hf_0_with_uncertainty_also_enthalpy_of_formation(self):
        table = _table(
            "property_hf_0_with_uncertainty.html", "hf_0_with_uncertainty"
        )
        results = build_molecular_property_payloads_from_property_table(table)
        for r in results:
            assert (
                r.payload.property_kind
                == MolecularPropertyKind.enthalpy_of_formation
            )

    def test_dipole_maps_to_dipole_moment(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        for r in results:
            assert r.payload.property_kind == MolecularPropertyKind.dipole_moment
            assert r.payload.scalar_unit == "Debye"

    def test_diatomic_maps_to_spectroscopic_constant(self):
        table = _table(
            "property_diatomic_spectroscopic.html", "diatomic_spectroscopic"
        )
        results = build_molecular_property_payloads_from_property_table(table)
        for r in results:
            assert (
                r.payload.property_kind
                == MolecularPropertyKind.spectroscopic_constant
            )
            assert r.payload.scalar_unit == "cm^-1"

    def test_unknown_property_kind_falls_through_to_other_with_label(self):
        """An unknown kind should land on ``other`` with the raw token
        preserved as ``property_label`` so a downstream maintainer
        can see what arrived."""

        table = CCCBDBExperimentalPropertyTable(
            property_kind="some_new_kind",
            raw_units="kJ/mol",
            canonical_unit="kJ/mol",
            rows=[
                CCCBDBExperimentalPropertyRow(
                    row_index=0,
                    formula="X",
                    value=1.0,
                    unit="kJ/mol",
                    normalized_value=1.0,
                    normalized_unit="kJ/mol",
                )
            ],
            source_metadata=CCCBDBPropertyTableSourceMetadata(
                source="CCCBDB",
                source_release="22",
                source_database_doi="10.18434/T47C7Z",
                source_url="https://example.invalid/x",
                source_record_key="x",
                property_kind="some_new_kind",
                content_sha256="0" * 64,
                parser_version="test",
            ),
        )
        results = build_molecular_property_payloads_from_property_table(table)
        assert len(results) == 1
        assert results[0].payload.property_kind == MolecularPropertyKind.other
        assert results[0].payload.property_label == "some_new_kind"


# ---------------------------------------------------------------------------
# Unit + uncertainty propagation
# ---------------------------------------------------------------------------


class TestUnitsAndUncertainty:
    def test_dipole_value_in_debye(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        h2o = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "H2O"
        )
        assert h2o.payload.scalar_value == pytest.approx(1.855)
        assert h2o.payload.scalar_unit == "Debye"

    def test_goodlist_uncertainty_propagates(self):
        table = _table(
            "property_hf_0_with_uncertainty.html", "hf_0_with_uncertainty"
        )
        results = build_molecular_property_payloads_from_property_table(table)
        lih = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "LiH"
        )
        assert lih.payload.scalar_value == pytest.approx(140.804)
        assert lih.payload.scalar_uncertainty == pytest.approx(0.040)
        assert lih.payload.scalar_unit == "kJ/mol"


# ---------------------------------------------------------------------------
# Forensic raw_payload_json
# ---------------------------------------------------------------------------


class TestRawPayloadForensics:
    def test_diatomic_preserves_secondary_constants(self):
        """For diatomic spectroscopic rows, ωe is the value column,
        but ωexe / Be / De / αe carry along in raw_payload_json.raw_row
        so callers can reconstruct the full row without re-parsing."""

        table = _table(
            "property_diatomic_spectroscopic.html", "diatomic_spectroscopic"
        )
        results = build_molecular_property_payloads_from_property_table(table)
        h2 = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "H2"
        )
        # After the Phase 5e configured_column_names fix, the
        # diatomic table uses stable ASCII column tokens (not the
        # Unicode "ωexe" / "De" that the old fixture's <th> row
        # supplied). The values still flow through unchanged.
        raw_row = h2.payload.raw_payload_json["raw_row"]
        assert raw_row["wexe"] == "121.336"
        assert raw_row["Be"] == "60.853"
        # Position 7 in the H2 row carries 3.0622 (configured as "re";
        # the live page's column key here is uncertain — see the
        # PROPERTY_CONFIGS comment).
        assert raw_row["re"] == "3.0622"

    def test_raw_payload_contains_normalized_and_raw_fields(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        h2 = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "H2"
        )
        payload = h2.payload.raw_payload_json
        assert payload["raw_value"] == 0.0
        assert payload["raw_unit"] == "Debye"
        assert payload["normalized_value"] == 0.0
        assert payload["normalized_unit"] == "Debye"


# ---------------------------------------------------------------------------
# Row-level references + external source provenance
# ---------------------------------------------------------------------------


class TestReferencePreservation:
    def test_dipole_row_carries_squib_reference(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        h2 = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "H2"
        )
        assert h2.payload.reference_label == "NSRDS-NBS10"

    def test_lih_row_carries_comment(self):
        """LiH in the dipole fixture has comment='MB'. The builder
        must preserve it on ``reference_comment``."""

        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        lih = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "LiH"
        )
        assert lih.payload.reference_label == "NSRDS-NBS10"
        assert lih.payload.reference_comment == "MB"

    def test_external_source_metadata_lifted_from_table(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        r = results[0]
        assert r.payload.external_source_name == "CCCBDB"
        assert r.payload.external_source_release == "22"
        assert r.payload.external_source_doi == "10.18434/T47C7Z"
        assert r.payload.external_source_url == (
            "https://cccbdb.nist.gov/property_dipoles.html"
        )
        assert r.payload.external_source_page_kind == (
            "experimental_property_table"
        )
        assert len(r.payload.external_source_content_sha256) == 64


# ---------------------------------------------------------------------------
# Catalog identity enrichment
# ---------------------------------------------------------------------------


class TestCatalogEnrichment:
    def test_unambiguous_match_surfaces_identity_hint(self, catalog):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=catalog
        )
        h2o = next(
            r
            for r in results
            if r.payload and r.payload.raw_payload_json["row_formula"] == "H2O"
        )
        assert h2o.identity_hint is not None
        assert h2o.identity_hint["formula"] == "H2O"
        assert h2o.identity_hint["name"] == "Water"
        assert (
            h2o.identity_hint["inchikey"] == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        )
        assert h2o.identity_hint["score"] == "high"
        # Identity hint rides along inside raw_payload_json too.
        assert (
            h2o.payload.raw_payload_json["identity_hint"]["inchi"]
            == "InChI=1S/H2O/h1H2"
        )

    def test_unambiguous_match_does_not_set_species_entry_id(self, catalog):
        """The builder hints identity but does NOT resolve it. The
        workflow layer is responsible for translating an InChIKey
        into a species_entry_id, and only after de-duplication."""

        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=catalog
        )
        for r in results:
            if r.payload is not None:
                assert r.payload.species_entry_id is None

    def test_no_catalog_means_no_identity_hint(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=None
        )
        for r in results:
            assert r.identity_hint is None
            assert "identity_hint" not in (
                r.payload.raw_payload_json if r.payload else {}
            )

    def test_ambiguous_catalog_match_does_not_enrich(self, catalog):
        """A property row claiming ``C2H6O`` (no name) has two catalog
        candidates (ethanol, dimethyl ether). The builder must NOT
        pick one — it must keep identity_hint=None and emit a warning."""

        table = CCCBDBExperimentalPropertyTable(
            property_kind="dipole",
            raw_units="Debye",
            canonical_unit="Debye",
            rows=[
                CCCBDBExperimentalPropertyRow(
                    row_index=0,
                    formula="C2H6O",
                    value=1.0,
                    unit="Debye",
                    normalized_value=1.0,
                    normalized_unit="Debye",
                )
            ],
            source_metadata=CCCBDBPropertyTableSourceMetadata(
                source="CCCBDB",
                source_release="22",
                source_database_doi="10.18434/T47C7Z",
                source_url="https://example.invalid/ambiguous-dipole",
                source_record_key="ambiguous",
                property_kind="dipole",
                content_sha256="1" * 64,
                parser_version="test",
            ),
        )
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=catalog
        )
        r = results[0]
        assert r.identity_hint is None
        assert any(
            "ambiguous" in w.lower() for w in r.warnings
        ), r.warnings
        # All candidates preserved in raw_payload_json for forensics.
        candidates = r.payload.raw_payload_json["catalog_candidates"]
        names = {c["catalog_entry"]["name"] for c in candidates}
        assert names == {"Ethanol", "Dimethyl ether"}
        # And no candidate is flagged unambiguous.
        assert not any(c["is_unambiguous"] for c in candidates)

    def test_catalog_match_never_uses_raw_href(self, catalog):
        """Phase 3b contract: raw catalog hrefs are audit only. The
        builder must not embed them in any payload field."""

        # CH4 in the catalog has raw_href="exp1.asp?casno=64175".
        table = CCCBDBExperimentalPropertyTable(
            property_kind="dipole",
            raw_units="Debye",
            canonical_unit="Debye",
            rows=[
                CCCBDBExperimentalPropertyRow(
                    row_index=0,
                    formula="CH4",
                    name="Methane",
                    value=0.0,
                    unit="Debye",
                    normalized_value=0.0,
                    normalized_unit="Debye",
                )
            ],
            source_metadata=CCCBDBPropertyTableSourceMetadata(
                source="CCCBDB",
                source_release="22",
                source_database_doi="10.18434/T47C7Z",
                source_url="https://example.invalid/ch4",
                source_record_key="ch4",
                property_kind="dipole",
                content_sha256="2" * 64,
                parser_version="test",
            ),
        )
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=catalog
        )
        r = results[0]
        # The identity hint was surfaced (formula+name match catalog
        # exactly), but raw_href appears nowhere in the payload.
        as_dict = r.payload.model_dump()
        assert "exp1.asp?casno=64175" not in str(as_dict)
        assert r.identity_hint is not None
        # And no field on the payload references a URL that isn't the
        # source page URL itself.
        assert "exp1.asp" not in (r.payload.external_source_url or "")


# ---------------------------------------------------------------------------
# Real-schema round-trip
# ---------------------------------------------------------------------------


class TestRealSchemaValidation:
    def test_dipole_payload_validates_against_real_schema(self, catalog):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(
            table, catalog=catalog
        )
        for r in results:
            if r.payload is None:
                continue
            # The builder returns Pydantic objects already, but
            # round-tripping through model_dump → model_validate
            # catches encoder drift the same way Phase 2c does.
            payload_dict = r.payload.model_dump()
            MolecularPropertyObservationCreate.model_validate(payload_dict)

    def test_all_payloads_carry_experimental_origin(self):
        table = _table("property_dipoles.html", "dipole")
        results = build_molecular_property_payloads_from_property_table(table)
        for r in results:
            if r.payload is not None:
                assert (
                    r.payload.scientific_origin
                    == ScientificOriginKind.experimental
                )


# ---------------------------------------------------------------------------
# Missing-value guard
# ---------------------------------------------------------------------------


class TestMissingValueGuard:
    def test_row_without_value_produces_no_payload_but_no_crash(self):
        table = CCCBDBExperimentalPropertyTable(
            property_kind="dipole",
            raw_units="Debye",
            canonical_unit="Debye",
            rows=[
                CCCBDBExperimentalPropertyRow(
                    row_index=0,
                    formula="H2O",
                    name="Water",
                    value=None,
                    unit="Debye",
                    normalized_value=None,
                    normalized_unit="Debye",
                )
            ],
            source_metadata=CCCBDBPropertyTableSourceMetadata(
                source="CCCBDB",
                source_release="22",
                source_database_doi="10.18434/T47C7Z",
                source_url="https://example.invalid/no-value",
                source_record_key="x",
                property_kind="dipole",
                content_sha256="3" * 64,
                parser_version="test",
            ),
        )
        results = build_molecular_property_payloads_from_property_table(table)
        assert len(results) == 1
        assert results[0].payload is None
        assert results[0].is_workflow_ready is False
        assert any("no scalar value" in w for w in results[0].warnings)
