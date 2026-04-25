"""Workflow-layer tests for standalone statmech upload persistence.

Targets ``persist_statmech_upload`` and verifies that statmech rows,
provenance references, source-calculation links, and torsions/coordinate
definitions all persist through the canonical statmech resolution service,
that append-only semantics hold across repeated uploads, and that the
upload boundary stays FK-free at the schema layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
)
from app.db.models.literature import Literature
from app.db.models.software import SoftwareRelease
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.workflows.statmech import persist_statmech_upload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _freq_calc_payload() -> dict:
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def _sp_calc_payload() -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


def _scan_calc_payload() -> dict:
    return {
        "type": "scan",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
    }


def _basic_request(**overrides) -> StatmechUploadRequest:
    base: dict = {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "point_group": "C2v",
        "is_linear": False,
        "note": "basic standalone statmech",
    }
    base.update(overrides)
    return StatmechUploadRequest(**base)


# ---------------------------------------------------------------------------
# Test 1 — basic upload
# ---------------------------------------------------------------------------


def test_persist_statmech_upload_creates_row_linked_to_species_entry(db_engine) -> None:
    """A minimal standalone upload creates one ``Statmech`` row correctly
    linked to the resolved ``species_entry``."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=2201, username="statmech_tester_basic"))
        session.flush()

        statmech = persist_statmech_upload(
            session, _basic_request(), created_by=2201
        )

        assert statmech.id is not None
        assert statmech.species_entry_id is not None
        assert statmech.created_by == 2201
        assert statmech.scientific_origin == ScientificOriginKind.computed
        assert statmech.statmech_treatment == StatmechTreatmentKind.rrho
        assert statmech.external_symmetry == 2
        assert statmech.point_group == "C2v"
        assert statmech.is_linear is False
        assert statmech.note == "basic standalone statmech"

        # No source calculations or torsions attached by default.
        assert statmech.source_calculations == []
        assert statmech.torsions == []


# ---------------------------------------------------------------------------
# Test 2 — provenance resolution
# ---------------------------------------------------------------------------


def test_persist_statmech_upload_resolves_all_provenance_refs(
    db_engine, monkeypatch,
) -> None:
    """Literature, software release, and workflow tool release all resolve
    via the canonical resolvers used by the nested path."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Statmech of small molecules",
            "container-title": ["J. Chem. Phys."],
            "issued": 2010,
            "URL": f"https://doi.org/{doi}",
        },
    )

    request = _basic_request(
        species_entry={"smiles": "CC", "charge": 0, "multiplicity": 1},
        literature={
            "doi": "10.1063/statmech.42",
            "title": "Fallback title",
        },
        software_release={"name": "Gaussian", "version": "16", "revision": "C.01"},
        workflow_tool_release={"name": "ARC", "version": "1.1.0"},
    )

    with Session(db_engine) as session, session.begin():
        statmech = persist_statmech_upload(session, request)

        assert statmech.literature_id is not None
        assert statmech.software_release_id is not None
        assert statmech.workflow_tool_release_id is not None

        lit = session.get(Literature, statmech.literature_id)
        assert lit is not None
        assert lit.title == "Statmech of small molecules"

        sr = session.get(SoftwareRelease, statmech.software_release_id)
        assert sr is not None
        assert sr.software.name == "Gaussian"
        assert sr.version == "16"

        wtr = session.get(WorkflowToolRelease, statmech.workflow_tool_release_id)
        assert wtr is not None
        assert wtr.workflow_tool.name == "ARC"


# ---------------------------------------------------------------------------
# Test 3 — source calculations persist
# ---------------------------------------------------------------------------


def test_persist_statmech_upload_persists_source_calculations(db_engine) -> None:
    """Inline calcs + source_calculations persist (statmech, calc, role) links
    and preserve the declared roles."""
    request = _basic_request(
        species_entry={"smiles": "CCO", "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "freq1", "calculation": _freq_calc_payload()},
            {"key": "sp1", "calculation": _sp_calc_payload()},
        ],
        source_calculations=[
            {"calculation_key": "freq1", "role": "freq"},
            {"calculation_key": "sp1", "role": "sp"},
        ],
    )

    with Session(db_engine) as session, session.begin():
        statmech = persist_statmech_upload(session, request)

        links = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == statmech.id
            )
        ).all()
        assert len(links) == 2
        by_role = {lk.role: lk for lk in links}
        assert set(by_role) == {
            StatmechCalculationRole.freq,
            StatmechCalculationRole.sp,
        }
        # All supporting calcs are scoped to the statmech target species entry.
        freq_calc = session.get(
            Calculation, by_role[StatmechCalculationRole.freq].calculation_id
        )
        sp_calc = session.get(
            Calculation, by_role[StatmechCalculationRole.sp].calculation_id
        )
        assert freq_calc.type == CalculationType.freq
        assert sp_calc.type == CalculationType.sp
        assert freq_calc.species_entry_id == statmech.species_entry_id
        assert sp_calc.species_entry_id == statmech.species_entry_id


# ---------------------------------------------------------------------------
# Test 4 — torsions and coordinate definitions persist
# ---------------------------------------------------------------------------


def test_persist_statmech_upload_persists_torsions_and_definitions(
    db_engine,
) -> None:
    """Torsion rows, coordinate definitions, ordering, and the local-key
    resolution of ``source_scan_calculation_key`` all persist correctly."""
    request = _basic_request(
        species_entry={"smiles": "CCCC", "charge": 0, "multiplicity": 1},
        calculations=[
            {"key": "scan_t1", "calculation": _scan_calc_payload()},
        ],
        torsions=[
            {
                "torsion_index": 1,
                "dimension": 1,
                "symmetry_number": 3,
                "treatment_kind": "free_rotor",
                "top_description": "methyl top",
                "source_scan_calculation_key": "scan_t1",
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    }
                ],
            },
            {
                "torsion_index": 2,
                "dimension": 2,
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    },
                    {
                        "coordinate_index": 2,
                        "atom1_index": 2,
                        "atom2_index": 3,
                        "atom3_index": 4,
                        "atom4_index": 5,
                    },
                ],
            },
        ],
    )

    with Session(db_engine) as session, session.begin():
        statmech = persist_statmech_upload(session, request)

        torsions = session.scalars(
            select(StatmechTorsion)
            .where(StatmechTorsion.statmech_id == statmech.id)
            .order_by(StatmechTorsion.torsion_index)
        ).all()
        assert [t.torsion_index for t in torsions] == [1, 2]
        assert torsions[0].dimension == 1
        assert torsions[0].symmetry_number == 3
        assert torsions[0].top_description == "methyl top"
        # source_scan_calculation_key resolved through the local-key map.
        assert torsions[0].source_scan_calculation_id is not None
        scan_calc = session.get(
            Calculation, torsions[0].source_scan_calculation_id
        )
        assert scan_calc.type == CalculationType.scan
        assert scan_calc.species_entry_id == statmech.species_entry_id

        # Coordinate definitions for each torsion are persisted and ordered.
        coords_t1 = session.scalars(
            select(StatmechTorsionDefinition)
            .where(StatmechTorsionDefinition.torsion_id == torsions[0].id)
            .order_by(StatmechTorsionDefinition.coordinate_index)
        ).all()
        assert [c.coordinate_index for c in coords_t1] == [1]
        assert coords_t1[0].atom1_index == 1
        assert coords_t1[0].atom4_index == 4

        coords_t2 = session.scalars(
            select(StatmechTorsionDefinition)
            .where(StatmechTorsionDefinition.torsion_id == torsions[1].id)
            .order_by(StatmechTorsionDefinition.coordinate_index)
        ).all()
        assert [c.coordinate_index for c in coords_t2] == [1, 2]


# ---------------------------------------------------------------------------
# Test 5 — append-only behavior
# ---------------------------------------------------------------------------


def test_repeated_statmech_uploads_are_append_only(db_engine) -> None:
    """Two uploads against the same species entry yield two distinct
    statmech rows; statmech is a provenance-bearing result table and must
    never silently dedupe."""
    species = {"smiles": "N", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        first = persist_statmech_upload(
            session,
            _basic_request(species_entry=dict(species), note="first"),
        )
        second = persist_statmech_upload(
            session,
            _basic_request(species_entry=dict(species), note="second"),
        )

        assert first.id != second.id
        assert first.species_entry_id == second.species_entry_id

        rows = session.scalars(
            select(Statmech)
            .where(Statmech.species_entry_id == first.species_entry_id)
            .order_by(Statmech.id)
        ).all()
        assert len(rows) == 2
        assert [r.note for r in rows] == ["first", "second"]


# ---------------------------------------------------------------------------
# Test 6 — raw FK leakage at the upload boundary is rejected
# ---------------------------------------------------------------------------


class TestStatmechUploadRejectsRawFKs:
    """The standalone path must never accept raw DB ids. ``SchemaBase`` has
    ``extra='forbid'``, so each raw-FK field surfaces as a validation error."""

    def test_literature_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="literature_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                literature_id=42,
            )

    def test_software_release_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="software_release_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                software_release_id=7,
            )

    def test_workflow_tool_release_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="workflow_tool_release_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                workflow_tool_release_id=13,
            )

    def test_frequency_scale_factor_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="frequency_scale_factor_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                frequency_scale_factor_id=21,
            )

    def test_species_entry_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="species_entry_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                species_entry_id=1,
            )

    def test_source_calculation_raw_id_rejected(self) -> None:
        """Standalone source calcs must go through local keys, not calc ids."""
        with pytest.raises(ValidationError, match="calculation_id"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                source_calculations=[{"calculation_id": 9, "role": "freq"}],
            )

    def test_torsion_raw_source_scan_id_rejected(self) -> None:
        """Standalone torsions must go through a local key, not a raw id."""
        with pytest.raises(
            ValidationError, match="source_scan_calculation_id"
        ):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                torsions=[
                    {
                        "torsion_index": 1,
                        "dimension": 1,
                        "source_scan_calculation_id": 12,
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 1,
                                "atom2_index": 2,
                                "atom3_index": 3,
                                "atom4_index": 4,
                            }
                        ],
                    }
                ],
            )


# ---------------------------------------------------------------------------
# Supporting validators for the upload schema
# ---------------------------------------------------------------------------


class TestStatmechUploadSchemaValidation:
    """The upload schema guards cross-reference consistency so workflows
    never see unreachable keys or duplicate link pairs."""

    def test_duplicate_calculation_keys_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique keys"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                calculations=[
                    {"key": "dup", "calculation": _freq_calc_payload()},
                    {"key": "dup", "calculation": _sp_calc_payload()},
                ],
            )

    def test_source_calculation_with_undefined_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="undefined calculation_key"):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                calculations=[
                    {"key": "freq1", "calculation": _freq_calc_payload()},
                ],
                source_calculations=[
                    {"calculation_key": "ghost", "role": "freq"},
                ],
            )

    def test_torsion_with_undefined_scan_key_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="source_scan_calculation_key"
        ):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                torsions=[
                    {
                        "torsion_index": 1,
                        "dimension": 1,
                        "source_scan_calculation_key": "ghost",
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 1,
                                "atom2_index": 2,
                                "atom3_index": 3,
                                "atom4_index": 4,
                            }
                        ],
                    }
                ],
            )

    def test_duplicate_torsion_indices_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="Torsion indices must be unique"
        ):
            StatmechUploadRequest(
                species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
                torsions=[
                    {
                        "torsion_index": 1,
                        "dimension": 1,
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 1,
                                "atom2_index": 2,
                                "atom3_index": 3,
                                "atom4_index": 4,
                            }
                        ],
                    },
                    {
                        "torsion_index": 1,
                        "dimension": 1,
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 2,
                                "atom2_index": 3,
                                "atom3_index": 4,
                                "atom4_index": 5,
                            }
                        ],
                    },
                ],
            )
