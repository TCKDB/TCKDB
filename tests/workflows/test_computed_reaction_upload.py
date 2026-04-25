"""Workflow-layer tests for the computed reaction upload.

Targets ``persist_computed_reaction_upload`` directly (no HTTP layer) and
verifies that one bundled Arkane-style payload persists the full graph of
related rows: species, species entries, conformer groups and observations,
calculations and results, a reaction + reaction entry, a transition state
with its entry, thermo, kinetics, and provenance links.

Scope follows ``docs/computed_reaction_workflow_tests_spec.md``:

* full round-trip persistence
* species reuse across participants
* artifact persistence
* kinetics source-calculation linkage
* frequency scale factor resolution
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    KineticsCalculationRole,
    ReactionRole,
)
from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo, ThermoNASA
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
from app.workflows.computed_reaction import persist_computed_reaction_upload


# ---------------------------------------------------------------------------
# Geometry strings (minimal but valid XYZ blocks)
# ---------------------------------------------------------------------------

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.74 0.0 0.0"
_XYZ_TS_HHH = "3\nH...H...H\nH 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH 1.8 0.0 0.0"

_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS_CH3H = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)


# ---------------------------------------------------------------------------
# Shared provenance stubs
# ---------------------------------------------------------------------------

_SOFTWARE_GAUSSIAN = {"name": "Gaussian", "version": "16"}
_SOFTWARE_ORCA = {"name": "ORCA", "version": "5.0"}
_LOT_DFT = {"method": "wb97xd", "basis": "def2tzvp"}
_LOT_CC = {"method": "CCSD(T)", "basis": "cc-pVTZ"}


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _species_block(
    key: str,
    smiles: str,
    charge: int,
    multiplicity: int,
    xyz: str,
    *,
    include_sp: bool = True,
    include_thermo: bool = False,
    with_artifact: bool = False,
) -> dict:
    """Build one species block with conformer + optional SP calc / thermo."""
    conf_calc = {
        "key": f"{key}-opt",
        "type": "opt",
        "software_release": _SOFTWARE_GAUSSIAN,
        "level_of_theory": _LOT_DFT,
        "opt_converged": True,
    }
    if with_artifact:
        conf_calc["artifacts"] = [
            {
                "kind": "output_log",
                "filename": f"{key}.log",
                "content_base64": base64.b64encode(
                    # A minimal payload that trips the Gaussian signature check.
                    b"Entering Gaussian System, Link 0\nfake content for test\n"
                ).decode("ascii"),
            }
        ]

    block: dict = {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": charge,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": conf_calc,
            }
        ],
        "calculations": [],
    }
    if include_sp:
        block["calculations"].append(
            {
                "key": f"{key}-sp",
                "type": "sp",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE_ORCA,
                "level_of_theory": _LOT_CC,
                "sp_electronic_energy_hartree": -40.5,
            }
        )
    if include_thermo:
        block["thermo"] = {
            "h298_kj_mol": -10.0,
            "s298_j_mol_k": 200.0,
            "tmin_k": 100.0,
            "tmax_k": 2000.0,
        }
    return block


def _minimal_payload() -> dict:
    """CH3 + H -> CH4 bundle with TS, thermo on each species, one kinetics fit."""
    return {
        "analysis_software_release": {"name": "Arkane", "version": "3.0"},
        "species": [
            _species_block(
                "ch3", "[CH3]", 0, 2, _XYZ_CH3, include_thermo=True
            ),
            _species_block("h", "[H]", 0, 2, _XYZ_H, include_thermo=True),
            _species_block(
                "ch4", "C", 0, 1, _XYZ_CH4, include_thermo=True
            ),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS_CH3H},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE_GAUSSIAN,
                "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                },
            ],
            "label": "ch3+h->ch4 TS",
        },
        "kinetics": [
            {
                "reactant_keys": ["ch3", "h"],
                "product_keys": ["ch4"],
                "a": 1.2e13,
                "a_units": "cm3_mol_s",
                "n": 0.5,
                "reported_ea": 10.0,
                "reported_ea_units": "kj_mol",
                "tmin_k": 300.0,
                "tmax_k": 2500.0,
                "note": "forward TST",
            }
        ],
    }


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Open a session on a connection-bound transaction that is always rolled back.

    The workflow tests run against a shared, session-scoped ``db_engine``
    fixture with no transaction rollback between tests.  The computed reaction
    workflow persists many rows (species, calcs, TS, thermo, kinetics), and
    committing them would pollute other workflow tests that make unqualified
    row counts (e.g. ``len(session.scalars(select(TransitionState)).all())``).
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _patch_artifact_storage(monkeypatch) -> list[str]:
    """Stub out the S3 write so artifact tests don't require MinIO.

    Returns the list that will be appended to with each URI written.
    """
    written: list[str] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append(uri)
        return uri

    monkeypatch.setattr(
        "app.workflows.computed_reaction.store_artifact", _fake_store
    )
    return written


# ---------------------------------------------------------------------------
# 1. Full round-trip persistence
# ---------------------------------------------------------------------------


def test_full_round_trip_persistence(db_engine) -> None:
    """A minimal realistic bundle persists the full graph of related rows."""
    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=501, username="computed_rxn_tester_1"))
        session.flush()

        request = ComputedReactionUploadRequest(**_minimal_payload())
        summary = persist_computed_reaction_upload(
            session, request, created_by=501
        )

        # -- Summary contract --
        assert summary["reaction_entry_id"] is not None
        assert summary["reaction_id"] is not None
        assert summary["transition_state_entry_id"] is not None
        assert summary["species_count"] == 3
        assert len(summary["thermo_ids"]) == 3
        assert len(summary["kinetics_ids"]) == 1

        # -- Species / species entries: 3 distinct identities --
        species_entries = session.scalars(
            select(SpeciesEntry).where(
                SpeciesEntry.id.in_(summary["species_entry_ids"])
            )
        ).all()
        assert len(species_entries) == 3
        species_ids = {se.species_id for se in species_entries}
        assert len(species_ids) == 3

        # -- Conformer group + observation per species --
        conformer_groups = session.scalars(
            select(ConformerGroup).where(
                ConformerGroup.species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert len(conformer_groups) >= 3

        observations = session.scalars(
            select(ConformerObservation).where(
                ConformerObservation.created_by == 501
            )
        ).all()
        assert len(observations) == 3

        # Primary (opt) calcs are anchored to their conformer observations.
        opt_calcs = session.scalars(
            select(Calculation).where(
                Calculation.created_by == 501,
                Calculation.species_entry_id.in_(summary["species_entry_ids"]),
                Calculation.type == CalculationType.opt,
            )
        ).all()
        assert len(opt_calcs) == 3
        assert all(c.conformer_observation_id is not None for c in opt_calcs)

        # SP calcs at the higher LOT exist and are anchored too.
        sp_calcs = session.scalars(
            select(Calculation).where(
                Calculation.created_by == 501,
                Calculation.species_entry_id.in_(summary["species_entry_ids"]),
                Calculation.type == CalculationType.sp,
            )
        ).all()
        assert len(sp_calcs) == 3
        assert all(c.conformer_observation_id is not None for c in sp_calcs)

        # Each species-owned calculation has exactly one output geometry row.
        output_geom_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(
                    [c.id for c in opt_calcs + sp_calcs]
                )
            )
        ).all()
        assert len(output_geom_links) == 6

        # Result rows
        assert session.scalar(select(func.count()).select_from(CalculationSPResult)) >= 3
        assert session.scalar(select(func.count()).select_from(CalculationOptResult)) >= 3

        # -- Reaction + canonical entry --
        chem_reaction = session.get(ChemReaction, summary["reaction_id"])
        assert chem_reaction is not None
        graph_parts = session.scalars(
            select(ReactionParticipant).where(
                ReactionParticipant.reaction_id == chem_reaction.id
            )
        ).all()
        # 2 reactants (CH3, H) + 1 product (CH4) collapsed by role
        assert {p.role for p in graph_parts} == {
            ReactionRole.reactant,
            ReactionRole.product,
        }

        structured = session.scalars(
            select(ReactionEntryStructureParticipant).where(
                ReactionEntryStructureParticipant.reaction_entry_id
                == summary["reaction_entry_id"]
            )
        ).all()
        # One slot per upload-order participant: 2 reactants + 1 product.
        assert len(structured) == 3
        assert sorted(p.participant_index for p in structured) == [1, 1, 2]

        # -- Transition state --
        ts = session.scalar(
            select(TransitionState).where(
                TransitionState.reaction_entry_id
                == summary["reaction_entry_id"]
            )
        )
        assert ts is not None
        ts_entry = session.get(
            TransitionStateEntry, summary["transition_state_entry_id"]
        )
        assert ts_entry is not None
        assert ts_entry.transition_state_id == ts.id

        ts_calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id
            )
        ).all()
        assert len(ts_calcs) == 2  # opt + freq
        assert (
            session.scalar(
                select(func.count())
                .select_from(CalculationFreqResult)
                .where(
                    CalculationFreqResult.calculation_id.in_(
                        [c.id for c in ts_calcs]
                    )
                )
            )
            == 1
        )

        # -- Thermo on every species --
        thermos = session.scalars(
            select(Thermo).where(Thermo.id.in_(summary["thermo_ids"]))
        ).all()
        assert len(thermos) == 3
        assert {t.species_entry_id for t in thermos} == set(
            summary["species_entry_ids"]
        )

        # -- Kinetics row on its own reaction entry (direction-specific) --
        kinetics_rows = session.scalars(
            select(Kinetics).where(Kinetics.id.in_(summary["kinetics_ids"]))
        ).all()
        assert len(kinetics_rows) == 1
        kin = kinetics_rows[0]
        assert kin.reaction_entry_id != summary["reaction_entry_id"]
        assert kin.ea_kj_mol == 10.0


# ---------------------------------------------------------------------------
# 2. Species reuse across participants
# ---------------------------------------------------------------------------


def test_species_reuse_across_participants(db_engine) -> None:
    """Two payload species with the same identity collapse to one species row."""
    payload = {
        "species": [
            # Two different upload keys, both [H] (same InChI key).
            _species_block("h_a", "[H]", 0, 2, _XYZ_H, include_thermo=False),
            _species_block("h_b", "[H]", 0, 2, _XYZ_H, include_thermo=False),
            _species_block("h2", "[H][H]", 0, 1, _XYZ_H2, include_thermo=False),
        ],
        "reversible": True,
        "reactant_keys": ["h_a", "h_b"],
        "product_keys": ["h2"],
        "kinetics": [],
    }
    # Distinct geometry keys are required even when identity is shared.
    payload["species"][1]["conformers"][0]["geometry"]["key"] = "h_b-geom"
    payload["species"][1]["conformers"][0]["geometry"]["xyz_text"] = (
        "1\nH\nH 0.0 0.0 0.1"
    )

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        # Scope the dedup check to species entries created by THIS upload —
        # other tests may have already committed [H] rows, so a global
        # COUNT(*) on Species.smiles == "[H]" would reflect cross-test state.
        upload_species_ids = set(
            session.scalars(
                select(SpeciesEntry.species_id).where(
                    SpeciesEntry.id.in_(summary["species_entry_ids"])
                )
            ).all()
        )
        upload_species = session.scalars(
            select(Species).where(Species.id.in_(upload_species_ids))
        ).all()
        smiles_to_count: dict[str, int] = {}
        for sp in upload_species:
            smiles_to_count[sp.smiles] = smiles_to_count.get(sp.smiles, 0) + 1
        assert smiles_to_count.get("[H]") == 1, (
            "Duplicate species row created for [H] within this upload — "
            "species dedup failed."
        )

        # The two reactant keys in the payload resolve to the same species row.
        species_entries = session.scalars(
            select(SpeciesEntry).where(
                SpeciesEntry.id.in_(summary["species_entry_ids"])
            )
        ).all()
        h_species_ids = {
            se.species_id for se in species_entries if se.species_id is not None
        }
        h_single = {
            se.species_id
            for se in species_entries
            if session.get(Species, se.species_id).smiles == "[H]"
        }
        assert len(h_single) == 1

        # Structure participants point back to the reused species entry.
        structured = session.scalars(
            select(ReactionEntryStructureParticipant).where(
                ReactionEntryStructureParticipant.reaction_entry_id
                == summary["reaction_entry_id"]
            )
        ).all()
        reactant_se_ids = {
            p.species_entry_id for p in structured
            if p.role == ReactionRole.reactant
        }
        # Both reactant slots should reference the same species_entry row.
        assert len(reactant_se_ids) == 1


# ---------------------------------------------------------------------------
# 3. Artifact persistence
# ---------------------------------------------------------------------------


def test_artifact_persists_and_links_to_calculation(db_engine, monkeypatch) -> None:
    """A calculation_artifact row is created and linked to its calculation."""
    _patch_artifact_storage(monkeypatch)

    payload = _minimal_payload()
    # Attach one artifact to the CH3 opt calculation.
    payload["species"][0]["conformers"][0]["calculation"]["artifacts"] = [
        {
            "kind": "output_log",
            "filename": "ch3_opt.log",
            "content_base64": base64.b64encode(
                b"Entering Gaussian System, Link 0\n"
                b"some minimal but realistic log body\n"
            ).decode("ascii"),
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        # There should be exactly one artifact in the whole bundle.
        all_artifacts = session.scalars(select(CalculationArtifact)).all()
        assert len(all_artifacts) == 1
        artifact = all_artifacts[0]

        # It links to the CH3 opt calculation — verify both the owning species
        # entry (a radical with multiplicity 2) and the calculation type.
        parent_calc = session.get(Calculation, artifact.calculation_id)
        assert parent_calc is not None
        assert parent_calc.type == CalculationType.opt
        ch3_entry = session.get(SpeciesEntry, parent_calc.species_entry_id)
        assert ch3_entry is not None
        ch3_species = session.get(Species, ch3_entry.species_id)
        assert ch3_species is not None
        assert ch3_species.inchi_key == session.scalar(
            select(Species.inchi_key).where(Species.smiles == "[CH3]")
        )
        assert artifact.kind == ArtifactKind.output_log
        assert artifact.sha256 is not None and len(artifact.sha256) == 64
        assert artifact.bytes is not None and artifact.bytes > 0
        # URI produced by the stubbed storage is content-addressed.
        assert artifact.sha256 in artifact.uri


# ---------------------------------------------------------------------------
# 4. Kinetics source-calculation linkage
# ---------------------------------------------------------------------------


def test_kinetics_source_calculation_linkage(db_engine) -> None:
    """SP calculations on participants are linked to the kinetics row."""
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**_minimal_payload())
        summary = persist_computed_reaction_upload(session, request)

        kin_id = summary["kinetics_ids"][0]
        source_calcs = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        # Forward direction: 2 reactants + 1 product = 3 SP links.
        assert len(source_calcs) == 3
        role_counts: dict[KineticsCalculationRole, int] = {}
        for sc in source_calcs:
            role_counts[sc.role] = role_counts.get(sc.role, 0) + 1
        assert role_counts == {
            KineticsCalculationRole.reactant_energy: 2,
            KineticsCalculationRole.product_energy: 1,
        }

        # Every linked calculation is indeed an SP calculation.
        linked_calcs = session.scalars(
            select(Calculation).where(
                Calculation.id.in_([sc.calculation_id for sc in source_calcs])
            )
        ).all()
        assert all(c.type == CalculationType.sp for c in linked_calcs)


# ---------------------------------------------------------------------------
# 5. Frequency scale factor resolution
# ---------------------------------------------------------------------------


def test_frequency_scale_factor_resolution_on_statmech(db_engine) -> None:
    """A statmech payload with a freq_scale_factor ref resolves an FSF row."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "external_symmetry": 6,
        "statmech_treatment": "rrho",
        "freq_scale_factor": {
            "level_of_theory": _LOT_DFT,
            "scale_kind": "fundamental",
            "value": 0.988,
            "software": {"name": "Gaussian"},
            "note": "wB97X-D/def2-TZVP fundamental factor",
        },
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch3_entry_id = summary["species_entry_ids"][0]
        statmech_rows = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch3_entry_id)
        ).all()
        assert len(statmech_rows) == 1
        statmech = statmech_rows[0]
        assert statmech.frequency_scale_factor_id is not None

        fsf = session.get(FrequencyScaleFactor, statmech.frequency_scale_factor_id)
        assert fsf is not None
        assert fsf.value == 0.988
        assert fsf.level_of_theory is not None
        assert fsf.level_of_theory.method.lower() == "wb97xd"
        # software dimension is resolved through the SoftwareRef
        assert fsf.software is not None
        assert fsf.software.name == "Gaussian"


def test_same_basin_conformer_payloads_create_distinct_observations_and_anchor_calcs(
    db_engine,
) -> None:
    """Two conformer payloads may share a group but still own separate observations."""
    payload = _minimal_payload()
    payload["species"][0]["conformers"] = [
        {
            "key": "ch3-conf-a",
            "geometry": {"key": "ch3-geom-a", "xyz_text": _XYZ_CH3},
            "calculation": {
                "key": "ch3-opt-a",
                "type": "opt",
                "software_release": _SOFTWARE_GAUSSIAN,
                "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "note": "observation a",
        },
        {
            "key": "ch3-conf-b",
            "geometry": {"key": "ch3-geom-b", "xyz_text": _XYZ_CH3},
            "calculation": {
                "key": "ch3-opt-b",
                "type": "opt",
                "software_release": _SOFTWARE_GAUSSIAN,
                "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "note": "observation b",
        },
    ]
    payload["species"][0]["calculations"] = [
        {
            "key": "ch3-sp-a",
            "type": "sp",
            "geometry_key": "ch3-geom-a",
            "software_release": _SOFTWARE_ORCA,
            "level_of_theory": _LOT_CC,
            "sp_electronic_energy_hartree": -40.51,
        },
        {
            "key": "ch3-sp-b",
            "type": "sp",
            "geometry_key": "ch3-geom-b",
            "software_release": _SOFTWARE_ORCA,
            "level_of_theory": _LOT_CC,
            "sp_electronic_energy_hartree": -40.52,
        },
    ]

    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=502, username="computed_rxn_tester_2"))
        session.flush()

        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request, created_by=502)

        ch3_entry_id = session.execute(
            select(Calculation.species_entry_id)
            .where(
                Calculation.created_by == 502,
                Calculation.type == CalculationType.opt,
                Calculation.species_entry_id.is_not(None),
            )
            .group_by(Calculation.species_entry_id)
            .having(func.count(Calculation.id) == 2)
        ).scalar_one()

        ch3_groups = session.scalars(
            select(ConformerGroup).where(
                ConformerGroup.species_entry_id == ch3_entry_id
            )
        ).all()
        assert len(ch3_groups) == 1

        observations = session.scalars(
            select(ConformerObservation).where(
                ConformerObservation.conformer_group_id == ch3_groups[0].id
            )
        ).all()
        assert len(observations) == 2
        observation_ids = {obs.id for obs in observations}

        sp_rows = session.execute(
            select(
                Calculation.conformer_observation_id,
                CalculationSPResult.electronic_energy_hartree,
            )
            .join(
                CalculationSPResult,
                CalculationSPResult.calculation_id == Calculation.id,
            )
            .where(
                Calculation.species_entry_id == ch3_entry_id,
                Calculation.created_by == 502,
                Calculation.type == CalculationType.sp,
            )
        ).all()
        assert len(sp_rows) == 2
        assert {row.conformer_observation_id for row in sp_rows} == observation_ids


# ---------------------------------------------------------------------------
# Bundle-to-shared-seam convergence regressions
# ---------------------------------------------------------------------------


def test_bundle_calculation_parameters_persist_via_shared_seam(db_engine) -> None:
    """Parsed parameters on a bundle ``CalculationIn`` land as relational rows
    and snapshot metadata when routed through the shared calculation seam."""
    from datetime import datetime, timezone

    from app.db.models.calculation import (
        CalculationParameter,
        CalculationParameterVocab,
    )

    extracted_at = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)

    canonical_key = "bundle_computed_rxn_opt_convergence"

    payload = _minimal_payload()
    payload["species"][0]["conformers"][0]["calculation"].update(
        {
            "parameters": [
                {
                    "raw_key": "tight",
                    "raw_value": "tight",
                    "canonical_key": canonical_key,
                    "canonical_value": "tight",
                    "section": "opt",
                },
                {
                    "raw_key": "nproc",
                    "raw_value": "8",
                    "section": "resource",
                    "value_type": "int",
                },
            ],
            "parameters_json": {"route": "# B3LYP/6-31G(d) opt=tight"},
            "parameters_parser_version": "computed-rxn-test-1",
            "parameters_extracted_at": extracted_at.isoformat(),
        }
    )

    with _isolated_session(db_engine) as session:
        existing = session.scalar(
            select(CalculationParameterVocab).where(
                CalculationParameterVocab.canonical_key == canonical_key
            )
        )
        if existing is None:
            session.add(CalculationParameterVocab(canonical_key=canonical_key))
            session.flush()

        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request, created_by=None)

        snapshot_calcs = session.scalars(
            select(Calculation).where(
                Calculation.parameters_parser_version == "computed-rxn-test-1"
            )
        ).all()
        assert len(snapshot_calcs) == 1
        calc = snapshot_calcs[0]
        assert calc.parameters_json == {"route": "# B3LYP/6-31G(d) opt=tight"}
        assert calc.parameters_extracted_at is not None

        rows = session.scalars(
            select(CalculationParameter)
            .where(CalculationParameter.calculation_id == calc.id)
            .order_by(CalculationParameter.id)
        ).all()
        assert len(rows) == 2
        first, second = rows
        assert first.raw_key == "tight"
        assert first.canonical_key == canonical_key
        assert second.raw_key == "nproc"
        # Vocab not seeded for "nproc" — canonical_key silently demoted.
        assert second.canonical_key is None


def test_bundle_inline_results_and_artifacts_preserved(db_engine, monkeypatch) -> None:
    """Inline results (opt/freq/sp) and artifact persistence still work after
    convergence onto the shared seam. Artifact persistence is intentionally
    still bundle-owned — it is orchestration, not calculation creation."""
    _patch_artifact_storage(monkeypatch)

    payload = _minimal_payload()
    content = (
        b"Entering Gaussian System, Link 0\n"
        b"some minimal but realistic log body\n"
    )
    payload["species"][0]["conformers"][0]["calculation"]["artifacts"] = [
        {
            "kind": ArtifactKind.output_log.value,
            "filename": "ch3.log",
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
    ]

    with _isolated_session(db_engine) as session:
        baseline_calc_id = session.scalar(select(func.max(Calculation.id))) or 0
        baseline_artifact_id = (
            session.scalar(select(func.max(CalculationArtifact.id))) or 0
        )

        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request, created_by=None)

        new_calc_ids = {
            c.id
            for c in session.scalars(
                select(Calculation).where(Calculation.id > baseline_calc_id)
            ).all()
        }

        opt_rows = session.scalars(
            select(CalculationOptResult).where(
                CalculationOptResult.calculation_id.in_(new_calc_ids)
            )
        ).all()
        assert any(r.converged is True for r in opt_rows)

        freq_rows = session.scalars(
            select(CalculationFreqResult).where(
                CalculationFreqResult.calculation_id.in_(new_calc_ids)
            )
        ).all()
        assert any(r.n_imag == 1 for r in freq_rows)

        artifacts = session.scalars(
            select(CalculationArtifact).where(
                CalculationArtifact.id > baseline_artifact_id
            )
        ).all()
        assert len(artifacts) == 1
        assert artifacts[0].bytes == len(content)


def test_bundle_owner_semantics_preserved_after_convergence(db_engine) -> None:
    """Calculations produced via the bundle path keep their exclusive-owner FKs
    (species XOR TS), and TS-owned calculations never leak a species-entry id."""
    with _isolated_session(db_engine) as session:
        baseline_calc_id = session.scalar(select(func.max(Calculation.id))) or 0

        request = ComputedReactionUploadRequest(**_minimal_payload())
        persist_computed_reaction_upload(session, request, created_by=None)

        new_calcs = session.scalars(
            select(Calculation).where(Calculation.id > baseline_calc_id)
        ).all()
        assert len(new_calcs) > 0
        for c in new_calcs:
            owner_count = (
                (1 if c.species_entry_id is not None else 0)
                + (1 if c.transition_state_entry_id is not None else 0)
            )
            assert owner_count == 1, (
                f"calc {c.id} has {owner_count} owners (species or TS)"
            )

        ts_calcs = [
            c for c in new_calcs if c.transition_state_entry_id is not None
        ]
        assert len(ts_calcs) >= 2
        assert all(c.species_entry_id is None for c in ts_calcs)
