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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
)
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    FrequencyScaleFactor,
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


# ---------------------------------------------------------------------------
# Frequency-scale-factor unification tests
#
# Exercise the unified FreqScaleFactorRef through the standalone statmech
# upload path. The same resolver is shared with the computed-species and
# computed-reaction bundle paths, so identity/dedupe behavior is verified
# once here.
# ---------------------------------------------------------------------------


_FSF_LOT = {"method": "wB97X-D", "basis": "def2-TZVP"}


def _statmech_request_with_fsf(
    *,
    smiles: str,
    note: str | None = None,
    fsf_overrides: dict | None = None,
) -> StatmechUploadRequest:
    fsf: dict = {
        "level_of_theory": dict(_FSF_LOT),
        "scale_kind": "fundamental",
        "value": 0.988,
    }
    if fsf_overrides:
        fsf.update(fsf_overrides)
    return _basic_request(
        species_entry={"smiles": smiles, "charge": 0, "multiplicity": 1},
        freq_scale_factor=fsf,
        note=note,
    )


def test_unified_fsf_ref_supports_full_identity(db_engine, monkeypatch) -> None:
    """A FreqScaleFactorRef carrying every identity field — LoT, software,
    scale_kind, value, source_literature, workflow_tool_release — and a
    descriptive note round-trips into a single FrequencyScaleFactor row,
    with all FK fields populated through the unified resolver."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Scale factors for harmonic frequencies",
            "container-title": ["J. Chem. Phys."],
            "issued": 2018,
            "URL": f"https://doi.org/{doi}",
        },
    )
    request = _statmech_request_with_fsf(
        smiles="CC",
        fsf_overrides={
            "software": {"name": "Gaussian"},
            "source_literature": {
                "doi": "10.1063/fsf.full",
                "title": "Fallback title",
            },
            "workflow_tool_release": {"name": "ARC", "version": "1.1.0"},
            "note": "wB97X-D/def2-TZVP fundamental factor",
        },
    )

    with Session(db_engine) as session, session.begin():
        statmech = persist_statmech_upload(session, request)

        assert statmech.frequency_scale_factor_id is not None
        fsf = session.get(FrequencyScaleFactor, statmech.frequency_scale_factor_id)
        assert fsf is not None
        assert fsf.value == 0.988
        assert fsf.scale_kind.value == "fundamental"
        assert fsf.level_of_theory is not None
        assert fsf.level_of_theory.method.lower() == "wb97x-d"
        assert fsf.software is not None
        assert fsf.software.name == "Gaussian"
        assert fsf.source_literature_id is not None
        assert fsf.source_literature.title == "Scale factors for harmonic frequencies"
        assert fsf.workflow_tool_release_id is not None
        assert fsf.workflow_tool_release.workflow_tool.name == "ARC"
        assert fsf.note == "wB97X-D/def2-TZVP fundamental factor"


def test_bare_citation_string_lives_in_note_no_fake_literature(db_engine) -> None:
    """When a producer has only a citation string, it goes into the FSF
    note. No Literature row is synthesized from raw prose, and the
    ``source_literature_id`` stays NULL."""
    citation = "Truhlar et al., 2010 (in-house tabulation, DOI unknown)"
    request = _statmech_request_with_fsf(
        smiles="C",
        fsf_overrides={"note": citation},
    )

    with Session(db_engine) as session, session.begin():
        lit_count_before = session.scalar(select(func.count()).select_from(Literature))
        statmech = persist_statmech_upload(session, request)
        lit_count_after = session.scalar(select(func.count()).select_from(Literature))

        assert lit_count_after == lit_count_before  # nothing new in literature

        fsf = session.get(FrequencyScaleFactor, statmech.frequency_scale_factor_id)
        assert fsf is not None
        assert fsf.source_literature_id is None
        assert fsf.note == citation


def test_note_does_not_affect_identity_first_writer_wins(db_engine) -> None:
    """Two refs that share every identity field but differ only in
    ``note`` resolve to the SAME FrequencyScaleFactor row. The note from
    the first writer is preserved; subsequent notes are silently
    ignored. This pins the documented contract: notes are descriptive,
    never identity-bearing, never mutate registry rows."""
    smiles = "N"
    request_a = _statmech_request_with_fsf(
        smiles=smiles,
        note="upload A",
        fsf_overrides={"software": {"name": "Gaussian"}, "note": "first writer note"},
    )
    request_b = _statmech_request_with_fsf(
        smiles=smiles,
        note="upload B",
        fsf_overrides={
            "software": {"name": "Gaussian"},
            "note": "different but should be ignored on dedupe",
        },
    )

    with Session(db_engine) as session, session.begin():
        sm_a = persist_statmech_upload(session, request_a)
        sm_b = persist_statmech_upload(session, request_b)

        # Two statmech rows (statmech is append-only) but one FSF row.
        assert sm_a.id != sm_b.id
        assert sm_a.frequency_scale_factor_id == sm_b.frequency_scale_factor_id

        fsf = session.get(
            FrequencyScaleFactor, sm_a.frequency_scale_factor_id
        )
        assert fsf is not None
        assert fsf.note == "first writer note"  # first-writer wins; B's note dropped

        # Exactly one FSF row exists for this identity tuple — the
        # full DB-identity match across all six structural fields.
        matching = session.scalars(
            select(FrequencyScaleFactor).where(
                FrequencyScaleFactor.value == 0.988,
                FrequencyScaleFactor.level_of_theory_id == fsf.level_of_theory_id,
                FrequencyScaleFactor.software_id == fsf.software_id,
                FrequencyScaleFactor.scale_kind == fsf.scale_kind,
                FrequencyScaleFactor.source_literature_id.is_(None),
                FrequencyScaleFactor.workflow_tool_release_id.is_(None),
            )
        ).all()
        assert len(matching) == 1


def test_fsf_in_statmech_does_not_create_applied_energy_correction(db_engine) -> None:
    """Linking a frequency scale factor through ``statmech.frequency_scale_factor_id``
    is statmech provenance, not an applied correction. No
    ``applied_energy_correction`` row should be produced for this path."""
    request = _statmech_request_with_fsf(
        smiles="O",
        fsf_overrides={"software": {"name": "Gaussian"}},
    )

    with Session(db_engine) as session, session.begin():
        aec_before = session.scalar(
            select(func.count()).select_from(AppliedEnergyCorrection)
        )
        statmech = persist_statmech_upload(session, request)
        aec_after = session.scalar(
            select(func.count()).select_from(AppliedEnergyCorrection)
        )

        assert statmech.frequency_scale_factor_id is not None
        assert aec_after == aec_before


# ---------------------------------------------------------------------------
# DR-0033: optical isomers + electronic energy levels
# ---------------------------------------------------------------------------


def test_optical_isomers_and_electronic_levels_persist(db_engine) -> None:
    from app.db.models.statmech import Statmech, StatmechElectronicLevel

    # OH-like: doublet ground state split by spin-orbit coupling (~139 cm-1),
    # each level doubly degenerate; a single chiral center would give
    # optical_isomers=2 (here we just exercise the storage).
    request = _basic_request(
        species_entry={"smiles": "[OH]", "charge": 0, "multiplicity": 2},
        optical_isomers=1,
        electronic_levels=[
            {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 2},
            {"level_index": 2, "energy_cm1": 139.7, "degeneracy": 2},
        ],
    )
    with Session(db_engine) as session, session.begin():
        statmech = persist_statmech_upload(session, request)
        session.flush()

        row = session.get(Statmech, statmech.id)
        assert row.optical_isomers == 1
        levels = session.scalars(
            select(StatmechElectronicLevel)
            .where(StatmechElectronicLevel.statmech_id == statmech.id)
            .order_by(StatmechElectronicLevel.level_index)
        ).all()
        assert [(lvl.energy_cm1, lvl.degeneracy) for lvl in levels] == [
            (0.0, 2),
            (139.7, 2),
        ]


def test_duplicate_electronic_level_index_rejected() -> None:
    with pytest.raises(ValidationError, match="level_index values must be unique"):
        _basic_request(
            electronic_levels=[
                {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 1},
                {"level_index": 1, "energy_cm1": 100.0, "degeneracy": 1},
            ],
        )


def test_optical_isomers_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        _basic_request(optical_isomers=0)


# ---------------------------------------------------------------------------
# Schema audit R11: rotational constants (A/B/C, cm^-1)
# ---------------------------------------------------------------------------


def _water_identity_payload():
    from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

    return SpeciesEntryIdentityPayload.model_validate(
        {"smiles": "O", "charge": 0, "multiplicity": 1}
    )


def test_bundle_statmech_persists_rotational_constants(db_engine) -> None:
    """StatmechInBundle -> _persist_statmech_block writes A/B/C (cm^-1)."""
    from app.db.models.statmech import Statmech
    from app.schemas.workflows.computed_species_upload import StatmechInBundle
    from app.services.species_resolution import resolve_species_entry
    from app.workflows.computed_species import _persist_statmech_block

    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        se = resolve_species_entry(session, _water_identity_payload())
        session.flush()

        block = StatmechInBundle(
            scientific_origin="computed",
            rotational_constant_a_cm1=27.88,
            rotational_constant_b_cm1=14.52,
            rotational_constant_c_cm1=9.28,
        )
        stm = _persist_statmech_block(
            session,
            block,
            species_entry_id=se.id,
            calc_keys_to_id={},
            created_by=None,
        )
        session.flush()

        row = session.get(Statmech, stm.id)
        assert row.rotational_constant_a_cm1 == pytest.approx(27.88)
        assert row.rotational_constant_b_cm1 == pytest.approx(14.52)
        assert row.rotational_constant_c_cm1 == pytest.approx(9.28)
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_rotational_constant_positive_check(db_engine) -> None:
    """The columns exist and the per-column ``> 0`` CHECK rejects
    zero/negative values while accepting positive ones."""
    from sqlalchemy.exc import IntegrityError

    from app.db.models.statmech import Statmech
    from app.services.species_resolution import resolve_species_entry

    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        se = resolve_species_entry(session, _water_identity_payload())
        session.flush()

        good = Statmech(
            species_entry_id=se.id,
            scientific_origin=ScientificOriginKind.computed,
            rotational_constant_a_cm1=27.88,
            rotational_constant_b_cm1=14.52,
            rotational_constant_c_cm1=9.28,
        )
        session.add(good)
        session.flush()
        assert good.rotational_constant_c_cm1 == 9.28

        for bad_value in (0.0, -1.0):
            savepoint = session.begin_nested()
            bad = Statmech(
                species_entry_id=se.id,
                scientific_origin=ScientificOriginKind.computed,
                rotational_constant_a_cm1=bad_value,
            )
            session.add(bad)
            with pytest.raises(IntegrityError):
                session.flush()
            savepoint.rollback()
    finally:
        session.close()
        transaction.rollback()
        connection.close()
