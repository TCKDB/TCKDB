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

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationFreqResult,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
    CalculationSpinDiagnostic,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    IRCDirection,
    KineticsCalculationRole,
    ReactionRole,
)
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    FrequencyScaleFactor,
)
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
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.workflows.computed_reaction_upload import (
    BundleKineticsIn,
    ComputedReactionUploadRequest,
)
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
        "app.services.artifact_persistence.store_artifact", _fake_store
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

        # Output geometry rows: only opt calcs receive the fallback
        # (role=final, output_order=1) when the producer leaves
        # output_geometries empty. SP and freq calcs get zero rows
        # without explicit producer declaration — that matches the
        # generic shared semantics in attach_calculation_output_geometries.
        output_geom_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(
                    [c.id for c in opt_calcs + sp_calcs]
                )
            )
        ).all()
        assert len(output_geom_links) == 3
        assert {link.calculation_id for link in output_geom_links} == {
            c.id for c in opt_calcs
        }

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

        # -- Kinetics row reuses the canonical reaction entry --
        # The fit's reactant_keys/product_keys match the bundle's canonical
        # direction exactly, so the kinetics row attaches to the same
        # reaction_entry as the TS. Reverse-direction fits get their own
        # entry; that case is covered by
        # ``test_reverse_direction_kinetics_gets_separate_reaction_entry``.
        kinetics_rows = session.scalars(
            select(Kinetics).where(Kinetics.id.in_(summary["kinetics_ids"]))
        ).all()
        assert len(kinetics_rows) == 1
        kin = kinetics_rows[0]
        assert kin.reaction_entry_id == summary["reaction_entry_id"]
        assert ts.reaction_entry_id == kin.reaction_entry_id
        assert kin.ea_kj_mol == 10.0


def test_ts_freq_calc_with_hessian_persists(db_engine) -> None:
    """A TS freq calculation carrying a ``hessian`` persists one
    ``calculation_hessian`` row bound to the TS geometry with the correct
    triangle length (5-atom TS → 3N=15 → 15*16/2 = 120 entries)."""
    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=577, username="computed_rxn_ts_hessian"))
        session.flush()

        payload = _minimal_payload()
        triangle = [float(i) for i in range(120)]
        payload["transition_state"]["calculations"][0]["hessian"] = {
            "geometry": {"xyz_text": _XYZ_TS_CH3H},
            "lower_triangle_hartree_bohr2": triangle,
            "source": "parsed_fchk",
        }
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=577
        )

        ts_entry_id = summary["transition_state_entry_id"]
        # Locate the TS freq calculation (owned by the TS entry, type freq).
        freq_calc = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry_id,
                Calculation.type == CalculationType.freq,
            )
        )
        assert freq_calc is not None
        rows = session.scalars(
            select(CalculationHessian).where(
                CalculationHessian.calculation_id == freq_calc.id
            )
        ).all()
        assert len(rows) == 1
        hess = rows[0]
        assert hess.natoms == 5
        assert len(hess.lower_triangle_hartree_bohr2) == 120
        assert hess.geometry_id is not None


def test_ts_calc_with_spin_diagnostic_persists(db_engine) -> None:
    """A TS calculation carrying an inline ``spin_diagnostic`` persists one
    ``calc_spin_diagnostic`` (<S^2>) row anchored to the TS calc.

    Proves the shared ``CalculationIn`` change (adding the diagnostic field
    plus adapter forwarding) reaches ``persist_calculation_result`` through
    the computed_reaction route — the field is no longer dropped by
    ``extra='forbid'`` and is no longer silently ignored by the adapter."""
    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=591, username="computed_rxn_ts_spin"))
        session.flush()

        payload = _minimal_payload()
        payload["transition_state"]["calculations"][0]["spin_diagnostic"] = {
            "s_squared": 1.0123,
            "s_squared_expected": 0.75,
            "s_squared_annihilated": 0.7599,
            "note": "UHF TS",
        }
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=591
        )

        ts_entry_id = summary["transition_state_entry_id"]
        # ``calculations[0]`` is the TS freq calc (see _minimal_payload).
        ts_calc = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry_id,
                Calculation.type == CalculationType.freq,
            )
        )
        assert ts_calc is not None
        rows = session.scalars(
            select(CalculationSpinDiagnostic).where(
                CalculationSpinDiagnostic.calculation_id == ts_calc.id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].s_squared == pytest.approx(1.0123)
        assert rows[0].s_squared_expected == pytest.approx(0.75)
        assert rows[0].s_squared_annihilated == pytest.approx(0.7599)
        assert rows[0].note == "UHF TS"


def test_canonical_direction_kinetics_reuses_canonical_reaction_entry(
    db_engine,
) -> None:
    """A forward-only fit attaches to the bundle's canonical reaction entry.

    When a kinetics fit's ``reactant_keys`` / ``product_keys`` match the
    bundle's canonical direction byte-for-byte, the workflow must reuse
    ``canonical_reaction_entry`` instead of producing a duplicate row with
    identical participants. The canonical entry is the one that owns the
    transition state; kinetics for the same direction belong on the same
    entry, not a sibling.
    """
    payload = _minimal_payload()
    # ``_minimal_payload`` already sets a single forward fit whose keys
    # equal the bundle keys; assert that explicitly so the test contract
    # is self-documenting and survives future refactors of the fixture.
    assert payload["kinetics"][0]["reactant_keys"] == payload["reactant_keys"]
    assert payload["kinetics"][0]["product_keys"] == payload["product_keys"]

    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=540, username="canonical_dir_tester"))
        session.flush()

        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=540
        )

        kin = session.scalars(
            select(Kinetics).where(Kinetics.id.in_(summary["kinetics_ids"]))
        ).one()
        ts = session.scalar(
            select(TransitionState).where(
                TransitionState.reaction_entry_id
                == summary["reaction_entry_id"]
            )
        )
        assert ts is not None

        # Canonical-direction fit reuses the canonical reaction entry.
        assert kin.reaction_entry_id == summary["reaction_entry_id"]
        assert ts.reaction_entry_id == kin.reaction_entry_id

        # Exactly one reaction_entry exists for this reaction — no duplicate.
        all_entries = session.scalars(
            select(ReactionEntry).where(
                ReactionEntry.reaction_id == summary["reaction_id"]
            )
        ).all()
        assert len(all_entries) == 1


def test_reverse_direction_kinetics_gets_separate_reaction_entry(
    db_engine,
) -> None:
    """A reverse-direction fit gets its own reaction entry with swapped roles.

    When a kinetics fit's participant ordering differs from the canonical
    direction (e.g. a reverse fit lists original products as reactants and
    original reactants as products), the workflow must create a separate
    direction-specific reaction entry, because
    ``reaction_entry_structure_participant`` is unique per
    ``(reaction_entry_id, role, participant_index)`` and the directional
    ordering is the scientific record kinetics points at.
    """
    payload = _minimal_payload()
    # Canonical: ch3 + h -> ch4. Add a reverse fit: ch4 -> ch3 + h.
    payload["kinetics"].append(
        {
            "reactant_keys": ["ch4"],
            "product_keys": ["ch3", "h"],
            "a": 5.0e10,
            "a_units": "per_s",
            "n": 0.0,
            "reported_ea": 420.0,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2500.0,
            "note": "reverse TST",
        }
    )

    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=541, username="reverse_dir_tester"))
        session.flush()

        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=541
        )

        kinetics_rows = session.scalars(
            select(Kinetics)
            .where(Kinetics.id.in_(summary["kinetics_ids"]))
            .order_by(Kinetics.id)
        ).all()
        assert len(kinetics_rows) == 2
        forward_kin, reverse_kin = kinetics_rows[0], kinetics_rows[1]

        # Forward kinetics still reuses canonical entry.
        assert forward_kin.reaction_entry_id == summary["reaction_entry_id"]

        # Reverse kinetics lives on a separate, direction-specific entry.
        assert reverse_kin.reaction_entry_id != summary["reaction_entry_id"]

        reverse_entry = session.get(ReactionEntry, reverse_kin.reaction_entry_id)
        assert reverse_entry is not None

        # Participant ordering on the reverse entry is exactly inverted
        # from the canonical entry: original products become reactants,
        # original reactants become products.
        reverse_parts = session.scalars(
            select(ReactionEntryStructureParticipant)
            .where(
                ReactionEntryStructureParticipant.reaction_entry_id
                == reverse_entry.id
            )
            .order_by(
                ReactionEntryStructureParticipant.role,
                ReactionEntryStructureParticipant.participant_index,
            )
        ).all()
        reactants = [p for p in reverse_parts if p.role == ReactionRole.reactant]
        products = [p for p in reverse_parts if p.role == ReactionRole.product]

        # Scope SpeciesEntry lookups to entries created by THIS upload —
        # other tests may have committed entries for the same SMILES, so
        # an unqualified ``Species.smiles == 'C'`` query is non-deterministic
        # under the shared db_engine fixture.
        upload_se_ids = set(summary["species_entry_ids"])
        ch4_se = session.scalar(
            select(SpeciesEntry)
            .join(Species, Species.id == SpeciesEntry.species_id)
            .where(SpeciesEntry.id.in_(upload_se_ids))
            .where(Species.smiles == "C")
        )
        ch3_se = session.scalar(
            select(SpeciesEntry)
            .join(Species, Species.id == SpeciesEntry.species_id)
            .where(SpeciesEntry.id.in_(upload_se_ids))
            .where(Species.smiles == "[CH3]")
        )
        h_se = session.scalar(
            select(SpeciesEntry)
            .join(Species, Species.id == SpeciesEntry.species_id)
            .where(SpeciesEntry.id.in_(upload_se_ids))
            .where(Species.smiles == "[H]")
        )

        assert [p.species_entry_id for p in reactants] == [ch4_se.id]
        assert [p.species_entry_id for p in products] == [ch3_se.id, h_se.id]


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
        _h_species_ids = {
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
        _summary = persist_computed_reaction_upload(session, request)

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


# ---------------------------------------------------------------------------
# 5b. Statmech parity with computed-species: point_group + source_calculations
# ---------------------------------------------------------------------------


def test_statmech_point_group_persists_on_species_block(db_engine) -> None:
    """Per-species statmech blocks accept and persist ``point_group``."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "external_symmetry": 6,
        "point_group": "D3h",
        "statmech_treatment": "rrho",
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch3_entry_id = summary["species_entry_ids"][0]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch3_entry_id)
        ).one()
        assert statmech.point_group == "D3h"


def test_statmech_source_calculations_persist_for_species_owned_calcs(db_engine) -> None:
    """Statmech source_calculations links resolve and persist correctly."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "external_symmetry": 6,
        "point_group": "D3h",
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "ch3-opt", "role": "opt"},
            {"calculation_key": "ch3-sp", "role": "sp"},
        ],
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch3_entry_id = summary["species_entry_ids"][0]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch3_entry_id)
        ).one()

        links = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == statmech.id
            )
        ).all()
        assert len(links) == 2

        # Each link points at a calc owned by this species entry, with
        # the producer-declared role preserved.
        from app.db.models.common import StatmechCalculationRole

        roles_to_calc_types: dict[StatmechCalculationRole, CalculationType] = {}
        for link in links:
            calc = session.get(Calculation, link.calculation_id)
            assert calc.species_entry_id == ch3_entry_id
            roles_to_calc_types[link.role] = calc.type
        assert roles_to_calc_types == {
            StatmechCalculationRole.opt: CalculationType.opt,
            StatmechCalculationRole.sp: CalculationType.sp,
        }


def test_statmech_source_calculation_referencing_ts_calc_rejects_with_owned_by_ts_message(
    db_engine,
) -> None:
    """A species statmech referencing a TS-owned calc rejects 422."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "ts-freq", "role": "freq"},
        ],
    }
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(
            ValueError, match=r"refers to a calculation owned by a transition state"
        ):
            persist_computed_reaction_upload(session, request)


def test_statmech_source_calculation_referencing_sibling_species_rejects_with_other_species_message(
    db_engine,
) -> None:
    """A species statmech referencing a sibling-species-owned calc rejects 422."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [
            # ch3 statmech referencing h's SP calc
            {"calculation_key": "h-sp", "role": "sp"},
        ],
    }
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(
            ValueError,
            match=r"refers to a calculation owned by a different species entry",
        ):
            persist_computed_reaction_upload(session, request)


def test_statmech_source_calculation_undefined_key_rejects_at_schema_layer() -> None:
    """A statmech source_calculation with an unknown key fails schema validation."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "does-not-exist", "role": "sp"},
        ],
    }
    import pytest

    with pytest.raises(ValueError, match=r"undefined calculation_key 'does-not-exist'"):
        ComputedReactionUploadRequest(**payload)


def test_statmech_without_new_fields_remains_valid(db_engine) -> None:
    """Pre-expansion payloads without point_group / source_calculations still persist."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "external_symmetry": 6,
        "statmech_treatment": "rrho",
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch3_entry_id = summary["species_entry_ids"][0]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch3_entry_id)
        ).one()
        assert statmech.point_group is None
        links = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == statmech.id
            )
        ).all()
        assert links == []


# ---------------------------------------------------------------------------
# 5c. Statmech torsion definitions in the reaction bundle
# ---------------------------------------------------------------------------


def _payload_with_ch4_scan() -> dict:
    """Add a scan calc to the ch4 species so torsion tests have a
    bundle-local scan key to point at. ch4 has 5 atoms; CH3 (3 H) has 4
    atoms — both are sufficient for a torsion atom quartet on H atoms."""
    payload = _minimal_payload()
    payload["species"][2]["calculations"].append(
        {
            "key": "ch4-scan",
            "type": "scan",
            "geometry_key": "ch4-geom",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
        }
    )
    return payload


def test_reaction_statmech_torsion_with_one_coordinate_persists(db_engine) -> None:
    """1D rotor: one statmech_torsion_definition row, atom quartet 1-based."""
    payload = _payload_with_ch4_scan()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "torsions": [
            {
                "torsion_index": 1,
                "symmetry_number": 3,
                "treatment_kind": "hindered_rotor",
                "dimension": 1,
                "top_description": "CH3 about C-H",
                "source_scan_calculation_key": "ch4-scan",
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 2,
                        "atom2_index": 1,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    }
                ],
            }
        ],
    }
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch4_entry_id = summary["species_entry_ids"][2]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch4_entry_id)
        ).one()
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == statmech.id
            )
        ).one()
        assert torsion.dimension == 1
        assert torsion.top_description == "CH3 about C-H"
        assert torsion.source_scan_calculation_id is not None
        scan_calc = session.get(Calculation, torsion.source_scan_calculation_id)
        assert scan_calc.type == CalculationType.scan

        coords = session.scalars(
            select(StatmechTorsionDefinition).where(
                StatmechTorsionDefinition.torsion_id == torsion.id
            )
        ).all()
        assert len(coords) == 1
        c = coords[0]
        assert (c.atom1_index, c.atom2_index, c.atom3_index, c.atom4_index) == (
            2,
            1,
            3,
            4,
        )


def test_reaction_statmech_torsion_without_coordinates_writes_no_definitions(
    db_engine,
) -> None:
    payload = _minimal_payload()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [{"torsion_index": 1, "symmetry_number": 3}],
    }
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch4_entry_id = summary["species_entry_ids"][2]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch4_entry_id)
        ).one()
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == statmech.id
            )
        ).one()
        coord_count = session.scalar(
            select(func.count())
            .select_from(StatmechTorsionDefinition)
            .where(StatmechTorsionDefinition.torsion_id == torsion.id)
        )
        assert coord_count == 0


def test_reaction_statmech_torsion_scan_key_missing_rejected() -> None:
    payload = _minimal_payload()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "ghost"}
        ],
    }
    with pytest.raises(ValueError, match="undefined calculation_key"):
        ComputedReactionUploadRequest(**payload)


def test_reaction_statmech_torsion_scan_key_must_be_scan_type() -> None:
    payload = _minimal_payload()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [
            # ts-freq is type=freq, not scan
            {"torsion_index": 1, "source_scan_calculation_key": "ch4-sp"}
        ],
    }
    with pytest.raises(ValueError, match="must reference a scan-type calculation"):
        ComputedReactionUploadRequest(**payload)


def test_reaction_statmech_torsion_coords_length_must_match_dimension() -> None:
    payload = _minimal_payload()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [
            {
                "torsion_index": 1,
                "dimension": 2,
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 2,
                        "atom2_index": 1,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="must equal dimension"):
        ComputedReactionUploadRequest(**payload)


def test_reaction_statmech_torsion_duplicate_coordinate_index_rejected() -> None:
    payload = _minimal_payload()
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [
            {
                "torsion_index": 1,
                "dimension": 2,
                "coordinates": [
                    {
                        "coordinate_index": 1,
                        "atom1_index": 2,
                        "atom2_index": 1,
                        "atom3_index": 3,
                        "atom4_index": 4,
                    },
                    {
                        "coordinate_index": 1,
                        "atom1_index": 2,
                        "atom2_index": 1,
                        "atom3_index": 3,
                        "atom4_index": 5,
                    },
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="coordinate_index"):
        ComputedReactionUploadRequest(**payload)


# ---------------------------------------------------------------------------
# Scan result persistence on bundle calculations (computed-reaction)
# ---------------------------------------------------------------------------


def _ch4_scan_result_payload(*, points: int = 3) -> dict:
    """A 1D dihedral scan over 4 atoms of CH4.

    CH4 has 5 atoms (C, H, H, H, H); a torsion atom-quartet on its
    hydrogens is sufficient.
    """
    coordinate_values = [0.0 + i * 60.0 for i in range(points)]
    return {
        "dimension": 1,
        "is_relaxed": True,
        "coordinates": [
            {
                "coordinate_index": 1,
                "coordinate_kind": "dihedral",
                "atom1_index": 2,
                "atom2_index": 1,
                "atom3_index": 3,
                "atom4_index": 4,
                "step_count": points,
                "step_size": 60.0,
                "start_value": 0.0,
                "end_value": 60.0 * (points - 1),
                "value_unit": "degree",
                "resolution_degrees": 60.0,
                "symmetry_number": 3,
            }
        ],
        "points": [
            {
                "point_index": i + 1,
                "electronic_energy_hartree": -40.5 - 1e-4 * i,
                "relative_energy_kj_mol": float(i),
                "coordinate_values": [
                    {
                        "coordinate_index": 1,
                        "coordinate_value": coordinate_values[i],
                        "value_unit": "degree",
                    }
                ],
            }
            for i in range(points)
        ],
    }


def test_reaction_bundle_scan_calculation_persists_scan_result_rows(
    db_engine,
) -> None:
    """A computed-reaction bundle with a type=scan species calc carrying
    scan_result persists rows in calc_scan_result, calc_scan_coordinate,
    calc_scan_point, and calc_scan_point_coordinate_value."""
    payload = _payload_with_ch4_scan()
    # Attach a scan_result to the ch4-scan calc.
    ch4_calcs = payload["species"][2]["calculations"]
    scan_idx = next(i for i, c in enumerate(ch4_calcs) if c["type"] == "scan")
    ch4_calcs[scan_idx] = {
        **ch4_calcs[scan_idx],
        "scan_result": _ch4_scan_result_payload(points=3),
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch4_entry_id = summary["species_entry_ids"][2]
        scan_calc = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == ch4_entry_id,
                Calculation.type == CalculationType.scan,
            )
        ).one()

        result = session.scalars(
            select(CalculationScanResult).where(
                CalculationScanResult.calculation_id == scan_calc.id
            )
        ).one()
        assert result.dimension == 1
        assert result.is_relaxed is True

        coords = session.scalars(
            select(CalculationScanCoordinate).where(
                CalculationScanCoordinate.calculation_id == scan_calc.id
            )
        ).all()
        assert len(coords) == 1
        assert coords[0].coordinate_kind == "dihedral"

        points = session.scalars(
            select(CalculationScanPoint)
            .where(CalculationScanPoint.calculation_id == scan_calc.id)
            .order_by(CalculationScanPoint.point_index)
        ).all()
        assert [p.point_index for p in points] == [1, 2, 3]

        values = session.scalars(
            select(CalculationScanPointCoordinateValue).where(
                CalculationScanPointCoordinateValue.calculation_id == scan_calc.id
            )
        ).all()
        assert len(values) == 3


def test_reaction_bundle_scan_result_on_non_scan_calc_rejected() -> None:
    """``scan_result`` on a ``type=freq`` calc rejects at the schema layer."""
    payload = _minimal_payload()
    # Attach scan_result to ts-freq (type=freq) — not allowed.
    ts_calcs = payload["transition_state"]["calculations"]
    ts_calcs[0] = {
        **ts_calcs[0],
        "scan_result": _ch4_scan_result_payload(points=2),
    }
    with pytest.raises(
        ValueError,
        match="scan_result is only allowed for calculation type 'scan'",
    ):
        ComputedReactionUploadRequest(**payload)


def test_reaction_bundle_scan_calc_rejects_non_scan_inline_results() -> None:
    """A ``type=scan`` calc that also carries opt/freq/sp inline result
    fields is rejected at the schema layer."""
    payload = _payload_with_ch4_scan()
    ch4_calcs = payload["species"][2]["calculations"]
    scan_idx = next(i for i, c in enumerate(ch4_calcs) if c["type"] == "scan")
    ch4_calcs[scan_idx] = {
        **ch4_calcs[scan_idx],
        "freq_n_imag": 0,
    }
    with pytest.raises(
        ValueError, match="not allowed for calculation type 'scan'"
    ):
        ComputedReactionUploadRequest(**payload)


def test_reaction_torsion_resolves_to_scan_calc_with_scan_result(
    db_engine,
) -> None:
    """A statmech torsion's ``source_scan_calculation_key`` resolves to a
    bundle-local type=scan calc that carries a ``scan_result``; the
    persisted ``StatmechTorsion.source_scan_calculation_id`` points at
    that calc, and the scan result rows exist."""
    payload = _payload_with_ch4_scan()
    ch4_calcs = payload["species"][2]["calculations"]
    scan_idx = next(i for i, c in enumerate(ch4_calcs) if c["type"] == "scan")
    ch4_calcs[scan_idx] = {
        **ch4_calcs[scan_idx],
        "scan_result": _ch4_scan_result_payload(points=2),
    }
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "torsions": [
            {
                "torsion_index": 1,
                "symmetry_number": 3,
                "treatment_kind": "hindered_rotor",
                "dimension": 1,
                "source_scan_calculation_key": "ch4-scan",
            }
        ],
    }

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ch4_entry_id = summary["species_entry_ids"][2]
        statmech = session.scalars(
            select(Statmech).where(Statmech.species_entry_id == ch4_entry_id)
        ).one()
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == statmech.id
            )
        ).one()
        assert torsion.source_scan_calculation_id is not None
        scan_calc = session.get(Calculation, torsion.source_scan_calculation_id)
        assert scan_calc.type == CalculationType.scan
        result = session.scalars(
            select(CalculationScanResult).where(
                CalculationScanResult.calculation_id == scan_calc.id
            )
        ).one()
        assert result.dimension == 1


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

        # Scope observations to this test's user. The canonical [CH3]
        # species_entry is shared with prior tests in the same DB, so
        # querying by group alone returns observations committed by
        # earlier tests as well.
        observations = session.scalars(
            select(ConformerObservation).where(
                ConformerObservation.conformer_group_id == ch3_groups[0].id,
                ConformerObservation.created_by == 502,
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


# ---------------------------------------------------------------------------
# Producer-controlled kinetics source calculations + calculation provenance
# (DR for compute-reaction generic enrichment).
# ---------------------------------------------------------------------------


def _payload_with_ts_irc() -> dict:
    """Same as `_minimal_payload()` but adds an `irc` calc to the TS so
    tests can exercise role=irc and role=freq compatibility checks.

    All inline kinetics source_calculations declarations are applied by
    each test; the base payload here only provides the calc set.
    """
    payload = _minimal_payload()
    # Add a TS-owned IRC calc so tests can reference it.
    payload["transition_state"]["calculations"].append(
        {
            "key": "ts-irc",
            "type": "irc",
            "geometry_key": "ts-geom",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
        }
    )
    # Add a TS-owned SP at the higher LOT for ts_energy linking.
    payload["transition_state"]["calculations"].append(
        {
            "key": "ts-sp",
            "type": "sp",
            "geometry_key": "ts-geom",
            "software_release": _SOFTWARE_ORCA,
            "level_of_theory": _LOT_CC,
            "sp_electronic_energy_hartree": -40.9,
        }
    )
    return payload


def test_kinetics_source_calculations_explicit_local_keys(db_engine) -> None:
    """source_calculations declarations resolve to KineticsSourceCalculation
    rows with the exact (calc_id, role) pairs the producer asked for."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ch3-sp", "role": "reactant_energy"},
        {"calculation_key": "h-sp", "role": "reactant_energy"},
        {"calculation_key": "ch4-sp", "role": "product_energy"},
        {"calculation_key": "ts-sp", "role": "ts_energy"},
        {"calculation_key": "ts-freq", "role": "freq"},
        {"calculation_key": "ts-irc", "role": "irc"},
        # fit_source is intentionally loose — link a species opt to prove it.
        {"calculation_key": "ch3-opt", "role": "fit_source"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        kin_id = summary["kinetics_ids"][0]
        rows = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        assert len(rows) == 7

        # Resolve calc keys back via Calculation rows so we can assert by key.
        calc_id_to_type_owner = {
            c.id: (c.type, c.species_entry_id, c.transition_state_entry_id)
            for c in session.scalars(
                select(Calculation).where(
                    Calculation.id.in_([r.calculation_id for r in rows])
                )
            )
        }
        role_owner_pairs = {
            (r.role, calc_id_to_type_owner[r.calculation_id][0])
            for r in rows
        }
        assert (KineticsCalculationRole.reactant_energy, CalculationType.sp) in role_owner_pairs
        assert (KineticsCalculationRole.product_energy, CalculationType.sp) in role_owner_pairs
        assert (KineticsCalculationRole.ts_energy, CalculationType.sp) in role_owner_pairs
        assert (KineticsCalculationRole.freq, CalculationType.freq) in role_owner_pairs
        assert (KineticsCalculationRole.irc, CalculationType.irc) in role_owner_pairs
        assert (KineticsCalculationRole.fit_source, CalculationType.opt) in role_owner_pairs


def test_declared_source_calculations_override_legacy_fallback(db_engine) -> None:
    """Even when species SPs exist (which the legacy fallback would auto-link),
    a non-empty source_calculations writes only the declared rows."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ts-sp", "role": "ts_energy"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        kin_id = summary["kinetics_ids"][0]

        rows = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].role == KineticsCalculationRole.ts_energy

        # No fallback rows.
        assert not any(
            r.role == KineticsCalculationRole.reactant_energy for r in rows
        )
        assert not any(
            r.role == KineticsCalculationRole.product_energy for r in rows
        )


def test_empty_source_calculations_preserves_legacy_fallback(db_engine) -> None:
    """When source_calculations is empty, the legacy auto-link still
    produces species-owned SP reactant_energy/product_energy rows."""
    # _minimal_payload() leaves source_calculations empty by default.
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**_minimal_payload())
        summary = persist_computed_reaction_upload(session, request)
        kin_id = summary["kinetics_ids"][0]

        rows = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        # 2 reactants + 1 product = 3 fallback links.
        assert len(rows) == 3
        roles = {r.role for r in rows}
        assert roles == {
            KineticsCalculationRole.reactant_energy,
            KineticsCalculationRole.product_energy,
        }


def test_unknown_source_calculation_key_raises(db_engine) -> None:
    """A calculation_key not present in the bundle is rejected at validation."""
    payload = _minimal_payload()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "does-not-exist", "role": "reactant_energy"},
    ]
    import pytest

    with pytest.raises(ValueError, match="unknown calculation_key"):
        ComputedReactionUploadRequest(**payload)


def test_duplicate_source_calculation_pair_raises() -> None:
    """A repeated (calculation_key, role) pair on one kinetics row is rejected."""
    payload = _minimal_payload()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ch3-sp", "role": "reactant_energy"},
        {"calculation_key": "ch3-sp", "role": "reactant_energy"},
    ]
    import pytest

    with pytest.raises(ValueError, match="Duplicate kinetics source_calculations"):
        ComputedReactionUploadRequest(**payload)


def test_role_owner_mismatch_rejects_species_sp_for_ts_energy(db_engine) -> None:
    """ts_energy must point to a TS-owned sp, not species-owned."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ch3-sp", "role": "ts_energy"},
    ]
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="ts_energy"):
            persist_computed_reaction_upload(session, request)


def test_role_owner_mismatch_rejects_ts_sp_for_reactant_energy(db_engine) -> None:
    """reactant_energy must point to a species-owned sp, not TS-owned."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ts-sp", "role": "reactant_energy"},
    ]
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="reactant_energy"):
            persist_computed_reaction_upload(session, request)


def test_role_type_mismatch_rejects_sp_for_freq_role(db_engine) -> None:
    """role=freq requires a TS-owned freq, not a TS-owned sp."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ts-sp", "role": "freq"},
    ]
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="freq"):
            persist_computed_reaction_upload(session, request)


def test_role_type_mismatch_rejects_freq_for_irc_role(db_engine) -> None:
    """role=irc requires a TS-owned irc, not a TS-owned freq."""
    payload = _payload_with_ts_irc()
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ts-freq", "role": "irc"},
    ]
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="irc"):
            persist_computed_reaction_upload(session, request)


def test_freq_role_rejects_species_owned_freq(db_engine) -> None:
    """v0 contract: role=freq is TS frequency only, never species frequency.

    Reactant/product frequency provenance belongs in
    thermo/statmech provenance, not kinetics provenance.
    """
    payload = _minimal_payload()
    # Add a species-owned freq calc on CH4.
    payload["species"][2]["calculations"].append(
        {
            "key": "ch4-freq",
            "type": "freq",
            "geometry_key": "ch4-geom",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
            "freq_n_imag": 0,
        }
    )
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ch4-freq", "role": "freq"},
    ]
    import pytest

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="freq"):
            persist_computed_reaction_upload(session, request)


def test_input_geometries_persist(db_engine) -> None:
    """A producer-declared input_geometries list creates input geometry rows."""
    payload = _minimal_payload()
    # Attach an explicit input_geometries entry to the CH3 SP calc using
    # the same xyz_text as the conformer geometry (canonicalizes to the
    # same Geometry row).
    payload["species"][0]["calculations"][0]["input_geometries"] = [
        {"xyz_text": _XYZ_CH3},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)

        # The bundle creates 3 SP calcs (one per species). Pick the CH3
        # one explicitly via its species_entry — `session.scalar(...)`
        # without a unique filter is non-deterministic and may return
        # an SP that wasn't the one this test mutated.
        ch3_sp = session.scalar(
            select(Calculation)
            .join(SpeciesEntry, SpeciesEntry.id == Calculation.species_entry_id)
            .join(Species, Species.id == SpeciesEntry.species_id)
            .where(
                Calculation.type == CalculationType.sp,
                Species.smiles == "[CH3]",
            )
        )
        assert ch3_sp is not None
        rows = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == ch3_sp.id
            )
        ).all()
        # Producer-explicit list: exactly one row at order 1.
        assert len(rows) == 1
        assert rows[0].input_order == 1


def test_output_geometries_persist_with_declared_role_and_order(db_engine) -> None:
    """A producer-declared output_geometries list creates rows with role/order."""
    payload = _payload_with_ts_irc()
    # Attach an explicit output_geometries to the TS-IRC calc with two
    # entries (forward + reverse). These must canonicalize to *distinct*
    # Geometry rows, so we use two different xyz texts.
    payload["transition_state"]["calculations"][1]["output_geometries"] = [
        {"geometry": {"xyz_text": _XYZ_CH3}, "role": "irc_forward"},
        {"geometry": {"xyz_text": _XYZ_CH4}, "role": "irc_reverse"},
    ]
    # Note: index 1 above is "ts-irc" (index 0 is "ts-freq", index 1 is "ts-irc",
    # index 2 is "ts-sp" since _payload_with_ts_irc appends them in that order).
    # We re-look up by key below.

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)

        # Find the TS-IRC calc — TS-owned, type=irc.
        ts_irc = session.scalar(
            select(Calculation).where(
                Calculation.type == CalculationType.irc,
                Calculation.transition_state_entry_id.is_not(None),
            )
        )
        assert ts_irc is not None
        rows = session.scalars(
            select(CalculationOutputGeometry)
            .where(CalculationOutputGeometry.calculation_id == ts_irc.id)
            .order_by(CalculationOutputGeometry.output_order)
        ).all()
        assert len(rows) == 2
        assert [r.output_order for r in rows] == [1, 2]
        assert rows[0].role == CalculationGeometryRole.irc_forward
        assert rows[1].role == CalculationGeometryRole.irc_reverse


def test_depends_on_persists_with_role(db_engine) -> None:
    """A computed-reaction calc may declare depends_on edges by local key."""
    payload = _payload_with_ts_irc()
    # Make the TS SP depend on the TS opt, role=single_point_on (compatible:
    # parent type must be opt). We override the additional sp-on-ts to wire
    # an explicit depends_on edge.
    payload["transition_state"]["calculations"][2]["depends_on"] = [
        {"parent_calculation_key": "ts-opt", "role": "single_point_on"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)

        ts_opt = session.scalar(
            select(Calculation).where(
                Calculation.type == CalculationType.opt,
                Calculation.transition_state_entry_id.is_not(None),
            )
        )
        ts_sp = session.scalar(
            select(Calculation).where(
                Calculation.type == CalculationType.sp,
                Calculation.transition_state_entry_id.is_not(None),
            )
        )
        assert ts_opt is not None and ts_sp is not None

        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == ts_opt.id,
                CalculationDependency.child_calculation_id == ts_sp.id,
            )
        ).all()
        assert len(edges) == 1
        assert edges[0].dependency_role == CalculationDependencyRole.single_point_on


def test_depends_on_optimized_from_with_freq_parent_raises(db_engine) -> None:
    """``optimized_from`` parent must be opt or path_search.

    Regression: the bundle path delivers ``role`` as a wire-mirror enum
    (``tckdb_schemas.enums.CalculationDependencyRole.optimized_from``);
    the service-layer check previously used ``is`` against the backend
    enum and silently skipped this validation for bundle workflows. With
    the ``==`` fix, the workflow's call to
    ``assert_dependency_role_type_compatible`` raises because
    ``ts-freq`` is type=freq, not opt or path_search.

    ``ts-sp`` is the third TS calc (index 2) in ``_payload_with_ts_irc``;
    pointing its depends_on at ``ts-freq`` with role ``optimized_from``
    is the regression scenario.
    """
    payload = _payload_with_ts_irc()
    payload["transition_state"]["calculations"][2]["depends_on"] = [
        {"parent_calculation_key": "ts-freq", "role": "optimized_from"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError) as exc:
            persist_computed_reaction_upload(session, request)
        assert "optimized_from" in str(exc.value)


def test_loose_roles_accept_any_calc_type_and_owner(db_engine) -> None:
    """master_equation and fit_source are intentionally unrestricted in v0.

    Linking a species-owned opt under fit_source must succeed; linking
    a species-owned freq under master_equation must succeed.
    """
    payload = _payload_with_ts_irc()
    # Attach a species-owned freq for master_equation linking.
    payload["species"][0]["calculations"].append(
        {
            "key": "ch3-freq",
            "type": "freq",
            "geometry_key": "ch3-geom",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
            "freq_n_imag": 0,
        }
    )
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ch3-opt", "role": "fit_source"},
        {"calculation_key": "ch3-freq", "role": "master_equation"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        kin_id = summary["kinetics_ids"][0]

        rows = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        roles = {r.role for r in rows}
        assert KineticsCalculationRole.fit_source in roles
        assert KineticsCalculationRole.master_equation in roles


# ---------------------------------------------------------------------------
# Structured IRC result on the TS-IRC calculation
# ---------------------------------------------------------------------------


_XYZ_IRC_FORWARD = (
    "5\nIRC forward\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.500"
)
_XYZ_IRC_REVERSE = (
    "5\nIRC reverse\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.300"
)


def _irc_result_block(*, with_points: bool) -> dict:
    """Build a structured ``irc_result`` payload for the TS-IRC calc.

    When ``with_points`` is True, includes a TS marker, one forward-branch
    point, and one reverse-branch point with inline geometries that the
    geometry resolver can canonicalize.
    """
    block: dict = {
        "direction": "both",
        "has_forward": with_points,
        "has_reverse": with_points,
        "ts_point_index": 0 if with_points else None,
        "zero_energy_reference_hartree": -40.5,
        "note": "test IRC bundle",
    }
    if with_points:
        block["points"] = [
            {
                "point_index": 0,
                "direction": None,
                "is_ts": True,
                "reaction_coordinate": 0.0,
                "electronic_energy_hartree": -40.5,
                "geometry": {"xyz_text": _XYZ_TS_CH3H},
            },
            {
                "point_index": 1,
                "direction": "forward",
                "reaction_coordinate": 0.5,
                "electronic_energy_hartree": -40.6,
                "geometry": {"xyz_text": _XYZ_IRC_FORWARD},
            },
            {
                "point_index": 2,
                "direction": "reverse",
                "reaction_coordinate": -0.5,
                "electronic_energy_hartree": -40.6,
                "geometry": {"xyz_text": _XYZ_IRC_REVERSE},
            },
        ]
    return block


def _find_ts_irc_calc(session: Session) -> Calculation:
    """Look up the TS-owned IRC calculation by type+owner."""
    calc = session.scalar(
        select(Calculation).where(
            Calculation.type == CalculationType.irc,
            Calculation.transition_state_entry_id.is_not(None),
        )
    )
    assert calc is not None
    return calc


def test_computed_reaction_accepts_structured_irc_result(db_engine) -> None:
    """A structured ``irc_result`` on the TS-IRC calc creates one
    ``calc_irc_result`` row through the existing persistence seam."""
    payload = _payload_with_ts_irc()
    # ts-irc is the second additional TS calc (index 1: ts-freq=0, ts-irc=1).
    ts_irc_in = next(
        c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-irc"
    )
    ts_irc_in["irc_result"] = _irc_result_block(with_points=False)

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)

        ts_irc = _find_ts_irc_calc(session)
        irc_result = session.get(CalculationIRCResult, ts_irc.id)
        assert irc_result is not None
        assert irc_result.direction == IRCDirection.both
        assert irc_result.note == "test IRC bundle"
        assert irc_result.zero_energy_reference_hartree == -40.5


def test_computed_reaction_irc_points_persist_with_directions_and_geometries(
    db_engine,
) -> None:
    """Forward/reverse IRC points persist with directions preserved and
    inline geometries resolved through the shared geometry resolver."""
    payload = _payload_with_ts_irc()
    ts_irc_in = next(
        c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-irc"
    )
    ts_irc_in["irc_result"] = _irc_result_block(with_points=True)

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)

        ts_irc = _find_ts_irc_calc(session)
        points = session.scalars(
            select(CalculationIRCPoint)
            .where(CalculationIRCPoint.calculation_id == ts_irc.id)
            .order_by(CalculationIRCPoint.point_index)
        ).all()
        assert len(points) == 3

        ts_point = points[0]
        assert ts_point.is_ts is True
        assert ts_point.direction is None
        assert ts_point.geometry_id is not None

        forward = points[1]
        assert forward.direction == IRCDirection.forward
        assert forward.geometry_id is not None

        reverse = points[2]
        assert reverse.direction == IRCDirection.reverse
        assert reverse.geometry_id is not None

        # Forward and reverse must point at distinct geometry rows.
        assert forward.geometry_id != reverse.geometry_id


def test_computed_reaction_irc_dependency_and_kinetics_source_coexist(
    db_engine,
) -> None:
    """A bundle may simultaneously declare an ``irc_start`` dependency
    edge from TS-opt to TS-IRC, a ``role=irc`` kinetics source link
    pointing at TS-IRC, and a structured ``irc_result`` on TS-IRC."""
    payload = _payload_with_ts_irc()
    ts_irc_in = next(
        c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-irc"
    )
    ts_irc_in["irc_result"] = _irc_result_block(with_points=True)
    ts_irc_in["depends_on"] = [
        {"parent_calculation_key": "ts-opt", "role": "irc_start"},
    ]
    payload["kinetics"][0]["source_calculations"] = [
        {"calculation_key": "ts-irc", "role": "irc"},
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        ts_irc = _find_ts_irc_calc(session)
        ts_opt = session.scalar(
            select(Calculation).where(
                Calculation.type == CalculationType.opt,
                Calculation.transition_state_entry_id.is_not(None),
            )
        )
        assert ts_opt is not None

        # Dependency edge ts-opt -> ts-irc with role=irc_start.
        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == ts_opt.id,
                CalculationDependency.child_calculation_id == ts_irc.id,
            )
        ).all()
        assert len(edges) == 1
        assert edges[0].dependency_role == CalculationDependencyRole.irc_start

        # Kinetics source link role=irc -> ts-irc.
        kin_id = summary["kinetics_ids"][0]
        ksc_rows = session.scalars(
            select(KineticsSourceCalculation).where(
                KineticsSourceCalculation.kinetics_id == kin_id
            )
        ).all()
        irc_links = [
            r for r in ksc_rows if r.role == KineticsCalculationRole.irc
        ]
        assert len(irc_links) == 1
        assert irc_links[0].calculation_id == ts_irc.id

        # Structured IRC result row exists on the same calc.
        assert session.get(CalculationIRCResult, ts_irc.id) is not None


def test_irc_result_rejected_on_non_irc_calc_type() -> None:
    """Sending ``irc_result`` on a non-IRC calc fails request-time
    validation (would surface as 422 at the API boundary)."""
    import pytest

    payload = _payload_with_ts_irc()
    # Attach irc_result to the TS-FREQ (not type=irc).
    ts_freq_in = next(
        c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-freq"
    )
    ts_freq_in["irc_result"] = _irc_result_block(with_points=False)

    with pytest.raises(
        ValueError, match="irc_result is only allowed for calculation type 'irc'"
    ):
        ComputedReactionUploadRequest(**payload)


# ---------------------------------------------------------------------------
# Applied energy corrections — species-side and TS-side
#
# These tests target the workflow-tool-neutral applied-correction surface
# added to computed-reaction uploads. The species-side path mirrors the
# computed-species top-level applied-corrections behavior; the TS-side
# path uses the new ``target_transition_state_entry_id`` column. The
# scheme/role validation is reused unchanged from the primitive payload.
# ---------------------------------------------------------------------------


_LOT_AEC_RXN = {"method": "B3LYP", "basis": "6-31G(d)"}


def _aec_scheme_ref_rxn(**overrides) -> dict:
    base: dict = {
        "kind": "atom_energy",
        "name": "AEC v1 (rxn)",
        "level_of_theory": dict(_LOT_AEC_RXN),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


def _bac_petersson_scheme_ref_rxn(**overrides) -> dict:
    base: dict = {
        "kind": "bac_petersson",
        "name": "Petersson BAC v1 (rxn)",
        "level_of_theory": dict(_LOT_AEC_RXN),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


def _bac_melius_scheme_ref_rxn(**overrides) -> dict:
    base: dict = {
        "kind": "bac_melius",
        "name": "Melius BAC v1 (rxn)",
        "level_of_theory": dict(_LOT_AEC_RXN),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


def _payload_with_aec_carriers() -> dict:
    """Minimal payload carrying species-owned SP calcs and a TS-owned SP calc.

    The standard ``_minimal_payload`` already wires ``ch3-sp`` and ``ch4-sp``
    on the species side. We append a TS-owned ``ts-sp`` so TS-side
    correction tests have a calc to reference via local key.
    """
    payload = _minimal_payload()
    payload["transition_state"]["calculations"].append(
        {
            "key": "ts-sp",
            "type": "sp",
            "geometry_key": "ts-geom",
            "software_release": _SOFTWARE_ORCA,
            "level_of_theory": _LOT_CC,
            "sp_electronic_energy_hartree": -42.0,
        }
    )
    return payload


# --- Species-side: 1-12 ----------------------------------------------------


def test_species_aec_total_no_components_persists(db_engine) -> None:
    """Spec test 1: species-side AEC persists targeting species_entry."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.123,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert len(applied) == 1
        ac = applied[0]
        assert ac.application_role.value == "aec_total"
        assert ac.target_reaction_entry_id is None
        assert ac.target_transition_state_entry_id is None
        assert ac.scheme_id is not None
        assert ac.frequency_scale_factor_id is None
        assert ac.value == -0.123
        assert ac.source_calculation_id is not None
        # ch3 owns ch3-sp; verify ownership consistency at the row level.
        source_calc = session.get(Calculation, ac.source_calculation_id)
        assert source_calc.species_entry_id == ac.target_species_entry_id


def test_species_bac_total_persists(db_engine) -> None:
    """Spec test 2: species-side BAC persists targeting species_entry."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _bac_petersson_scheme_ref_rxn(),
            "application_role": "bac_total",
            "value": -0.05,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)

        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert len(applied) == 1
        scheme = session.get(EnergyCorrectionScheme, applied[0].scheme_id)
        assert scheme.kind.value == "bac_petersson"


def test_species_correction_components_optional(db_engine) -> None:
    """Spec test 3: components are optional (no rows when omitted)."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _bac_melius_scheme_ref_rxn(),
            "application_role": "bac_total",
            "value": -0.04,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).one()
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert comps == []


def test_species_correction_components_persist_when_supplied(db_engine) -> None:
    """Spec test 4: components persist when supplied."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _bac_petersson_scheme_ref_rxn(
                bond_params=[{"bond_key": "C-H", "value": -0.11}],
            ),
            "application_role": "bac_total",
            "value": -0.66,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
            "components": [
                {
                    "component_kind": "bond",
                    "key": "C-H",
                    "multiplicity": 6,
                    "parameter_value": -0.11,
                    "contribution_value": -0.66,
                }
            ],
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).one()
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert len(comps) == 1
        assert comps[0].component_kind.value == "bond"
        assert comps[0].multiplicity == 6
        assert comps[0].contribution_value == -0.66


def test_species_source_calculation_key_resolves_to_species_owned_calc(
    db_engine,
) -> None:
    """Spec test 5: source_calculation_key resolves into the bundle namespace."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).one()
        # Resolved source calc must be a species-owned SP calc.
        source_calc = session.get(Calculation, ac.source_calculation_id)
        assert source_calc.type == CalculationType.sp
        assert source_calc.species_entry_id == ac.target_species_entry_id


def test_undefined_species_source_calculation_key_returns_422() -> None:
    """Spec test 6: undefined source_calculation_key returns 422 at schema layer."""
    import pytest

    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ghost",
        }
    ]

    with pytest.raises(ValueError, match="undefined calculation_key"):
        ComputedReactionUploadRequest(**payload)


def test_species_role_scheme_kind_mismatch_returns_422() -> None:
    """Spec test 7: aec_total + bac_petersson scheme returns 422."""
    import pytest

    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _bac_petersson_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]
    with pytest.raises(ValueError, match="aec_total.*atom_energy"):
        ComputedReactionUploadRequest(**payload)


def test_species_repeated_scheme_identity_reuses_scheme_row(db_engine) -> None:
    """Spec test 8: duplicate scheme refs across two species reuse one row."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]
    payload["species"][2]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.2,
            "value_unit": "hartree",
            "source_calculation_key": "ch4-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert len(applied) == 2
        scheme_ids = {ac.scheme_id for ac in applied}
        assert len(scheme_ids) == 1


def test_species_note_does_not_affect_scheme_identity(db_engine) -> None:
    """Spec test 9: scheme ``note`` differs but identity tuple matches → same row."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(note="first ref"),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]
    payload["species"][2]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(note="second ref"),
            "application_role": "aec_total",
            "value": -0.2,
            "value_unit": "hartree",
            "source_calculation_key": "ch4-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert {ac.scheme_id for ac in applied} == {applied[0].scheme_id}


def test_species_aec_does_not_create_freq_scale_factor_row(db_engine) -> None:
    """Spec test 10: AEC/BAC corrections do not create a frequency_scale_factor row."""
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        before = session.scalar(
            select(func.count()).select_from(FrequencyScaleFactor)
        )
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request)
        after = session.scalar(
            select(func.count()).select_from(FrequencyScaleFactor)
        )
        assert after == before


def test_existing_payload_without_corrections_remains_valid(db_engine) -> None:
    """Spec test 11: bundles without applied_energy_corrections still work."""
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**_minimal_payload())
        summary = persist_computed_reaction_upload(session, request)
        assert summary["species_count"] == 3
        # No applied corrections were declared anywhere.
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id.in_(
                    summary["species_entry_ids"]
                )
            )
        ).all()
        assert applied == []


def test_computed_species_applied_correction_behavior_unchanged(db_engine) -> None:
    """Spec test 12: computed-species AEC behavior remains unchanged.

    Smoke-test the species-side primitive payload by re-running one
    computed-species AEC scenario through ``persist_computed_species_upload``
    and asserting the row still targets ``target_species_entry_id`` and
    leaves the new TS column null.
    """
    from app.schemas.workflows.computed_species_upload import (
        ComputedSpeciesUploadRequest,
    )
    from app.workflows.computed_species import persist_computed_species_upload

    bundle = ComputedSpeciesUploadRequest(
        **{
            "species_entry": {
                "smiles": "Cl",
                "charge": 0,
                "multiplicity": 1,
            },
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {
                        "xyz_text": "1\nHCl\nCl 0.0 0.0 0.0",
                    },
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "software_release": _SOFTWARE_GAUSSIAN,
                        "level_of_theory": _LOT_DFT,
                        "opt_result": {"converged": True},
                    },
                    "additional_calculations": [
                        {
                            "key": "sp0",
                            "type": "sp",
                            "software_release": _SOFTWARE_ORCA,
                            "level_of_theory": _LOT_CC,
                            "sp_result": {
                                "electronic_energy_hartree": -460.0
                            },
                        }
                    ],
                }
            ],
            "applied_energy_corrections": [
                {
                    "scheme": _aec_scheme_ref_rxn(name="AEC v1 (rxn-cspecies)"),
                    "application_role": "aec_total",
                    "value": -0.07,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                }
            ],
        }
    )

    with _isolated_session(db_engine) as session:
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        assert ac.target_reaction_entry_id is None
        assert ac.target_transition_state_entry_id is None


# --- TS-side: 13-17 --------------------------------------------------------


def test_ts_aec_total_persists_targeting_transition_state_entry(db_engine) -> None:
    """Spec test 13: TS-side AEC targets transition_state_entry_id."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.111,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id
                == summary["transition_state_entry_id"]
            )
        ).all()
        assert len(applied) == 1
        ac = applied[0]
        assert ac.target_species_entry_id is None
        assert ac.target_reaction_entry_id is None
        assert ac.application_role.value == "aec_total"


def test_ts_bac_total_persists_targeting_transition_state_entry(db_engine) -> None:
    """Spec test 14: TS-side BAC targets transition_state_entry_id."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _bac_petersson_scheme_ref_rxn(),
            "application_role": "bac_total",
            "value": -0.05,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id
                == summary["transition_state_entry_id"]
            )
        ).one()
        scheme = session.get(EnergyCorrectionScheme, ac.scheme_id)
        assert scheme.kind.value == "bac_petersson"


def test_ts_source_calculation_key_resolves_to_ts_sp(db_engine) -> None:
    """Spec test 15: TS-side ``source_calculation_key="ts-sp"`` resolves correctly."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.111,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id
                == summary["transition_state_entry_id"]
            )
        ).one()
        source_calc = session.get(Calculation, ac.source_calculation_id)
        assert source_calc.type == CalculationType.sp
        assert (
            source_calc.transition_state_entry_id
            == summary["transition_state_entry_id"]
        )


def test_ts_correction_never_uses_target_reaction_entry_id(db_engine) -> None:
    """Spec test 16: TS corrections are never stored as reaction-entry corrections."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.111,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        # The reaction entry must have ZERO applied corrections attached.
        re_corrections = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_reaction_entry_id
                == summary["reaction_entry_id"]
            )
        ).all()
        assert re_corrections == []


@pytest.mark.filterwarnings(
    "ignore:transaction already deassociated from connection"
)
def test_target_exclusivity_enforced_by_check_constraint(db_engine) -> None:
    """Spec test 17: exactly one of species/reaction/TS target may be set.

    The check constraint ``num_nonnulls(target_species_entry_id,
    target_reaction_entry_id, target_transition_state_entry_id) = 1``
    rejects any row that violates the rule. We exercise it directly at
    the ORM layer (no upload path can construct a multi-target row).

    Uses a dedicated isolated session: a CHECK violation aborts the
    enclosing PostgreSQL transaction, which can't be reused, so we
    can't share the test-wide ``_isolated_session`` context.
    """
    from sqlalchemy.exc import IntegrityError

    # First isolated session: persist a real bundle so we have valid
    # FK targets for the probe row.
    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**_payload_with_aec_carriers())
        summary = persist_computed_reaction_upload(session, request)
        # Capture the ids while the row visibility is still in scope.
        species_entry_id = summary["species_entry_ids"][0]
        ts_entry_id = summary["transition_state_entry_id"]

        from app.db.models.common import (
            EnergyCorrectionApplicationRole,
            EnergyCorrectionSchemeKind,
            EnergyUnit,
        )

        scheme = EnergyCorrectionScheme(
            kind=EnergyCorrectionSchemeKind.atom_energy,
            name="exclusivity probe",
            version="1",
        )
        session.add(scheme)
        session.flush()
        scheme_id = scheme.id

        bad = AppliedEnergyCorrection(
            target_species_entry_id=species_entry_id,
            target_transition_state_entry_id=ts_entry_id,
            scheme_id=scheme_id,
            application_role=EnergyCorrectionApplicationRole.aec_total,
            value=-0.1,
            value_unit=EnergyUnit.hartree,
        )
        session.add(bad)
        try:
            with session.begin_nested():
                session.flush()
            raise AssertionError("expected CHECK constraint failure")
        except IntegrityError as exc:
            msg = str(exc.orig).lower()
            assert ("exactly_one_target" in msg) or ("num_nonnulls" in msg)


# --- Cross-owner negatives -------------------------------------------------


def test_species_correction_referencing_ts_calc_returns_422(db_engine) -> None:
    """Species-side correction whose ``source_calculation_key`` names a
    TS-owned calc rejects with 422."""
    import pytest

    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",  # TS-owned, not species-owned
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(ValueError, match="not owned by this species entry"):
            persist_computed_reaction_upload(session, request)


def test_ts_correction_referencing_species_calc_returns_422(db_engine) -> None:
    """TS-side correction whose ``source_calculation_key`` names a
    species-owned calc rejects with 422."""
    import pytest

    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(),
            "application_role": "aec_total",
            "value": -0.1,
            "value_unit": "hartree",
            "source_calculation_key": "ch3-sp",  # species-owned, not TS
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        with pytest.raises(
            ValueError, match="not owned by this transition state entry"
        ):
            persist_computed_reaction_upload(session, request)


def test_ts_side_scheme_atom_params_persist(db_engine) -> None:
    """TS-side AEC scheme params populate the atom_param table."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _aec_scheme_ref_rxn(
                name="TS-side AEC params",
                atom_params=[
                    {"element": "H", "value": -0.5},
                    {"element": "C", "value": -37.7},
                ],
            ),
            "application_role": "aec_total",
            "value": -0.111,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id
                == summary["transition_state_entry_id"]
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {(r.element, r.value) for r in rows} == {
            ("H", -0.5),
            ("C", -37.7),
        }


def test_ts_side_scheme_bond_params_persist(db_engine) -> None:
    """TS-side BAC scheme params populate the bond_param table."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        {
            "scheme": _bac_petersson_scheme_ref_rxn(
                name="TS-side PBAC params",
                bond_params=[
                    {"bond_key": "C-H", "value": -0.11},
                    {"bond_key": "C-C", "value": -0.13},
                ],
            ),
            "application_role": "bac_total",
            "value": -0.05,
            "value_unit": "hartree",
            "source_calculation_key": "ts-sp",
        }
    ]

    with _isolated_session(db_engine) as session:
        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(session, request)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id
                == summary["transition_state_entry_id"]
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeBondParam).where(
                EnergyCorrectionSchemeBondParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {(r.bond_key, r.value) for r in rows} == {
            ("C-H", -0.11),
            ("C-C", -0.13),
        }


# ---------------------------------------------------------------------------
# Kinetics degeneracy on the bundle schema
# ---------------------------------------------------------------------------


def _bundle_kinetics_kwargs(**overrides) -> dict:
    """Minimal valid BundleKineticsIn kwargs, with optional overrides."""
    base = {
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "a": 1.2e13,
        "a_units": "cm3_mol_s",
        "n": 0.5,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
    }
    base.update(overrides)
    return base


def test_bundle_kinetics_accepts_degeneracy() -> None:
    """BundleKineticsIn accepts a positive ``degeneracy`` value."""
    kin = BundleKineticsIn(**_bundle_kinetics_kwargs(degeneracy=2.0))
    assert kin.degeneracy == 2.0


def test_bundle_kinetics_degeneracy_optional() -> None:
    """``degeneracy`` is optional and defaults to ``None``."""
    kin = BundleKineticsIn(**_bundle_kinetics_kwargs())
    assert kin.degeneracy is None


@pytest.mark.parametrize("bad_value", [0, 0.0, -1.0])
def test_bundle_kinetics_rejects_non_positive_degeneracy(bad_value) -> None:
    """Zero or negative ``degeneracy`` is a schema-level error."""
    with pytest.raises(ValidationError):
        BundleKineticsIn(**_bundle_kinetics_kwargs(degeneracy=bad_value))


def test_computed_reaction_payload_accepts_degeneracy_regression() -> None:
    """Regression: the previously-failing ARC payload now validates.

    Before this change, ``BundleKineticsIn``'s ``extra="forbid"`` rejected
    ``degeneracy`` with ``[type=extra_forbidden]``. The full
    ``ComputedReactionUploadRequest`` must accept it end-to-end so ARC
    computed-reaction uploads carrying reaction-path degeneracy succeed.
    """
    payload = _minimal_payload()
    payload["kinetics"][0]["degeneracy"] = 2.0
    request = ComputedReactionUploadRequest.model_validate(payload)
    assert request.kinetics[0].degeneracy == 2.0


def test_computed_reaction_persists_degeneracy(db_engine) -> None:
    """``kinetics[].degeneracy`` is written to the kinetics row."""
    payload = _minimal_payload()
    payload["kinetics"][0]["degeneracy"] = 3.0

    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=601, username="degeneracy_persist_tester"))
        session.flush()

        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=601
        )

        kin = session.scalars(
            select(Kinetics).where(Kinetics.id.in_(summary["kinetics_ids"]))
        ).one()
        assert kin.degeneracy == 3.0


def test_computed_reaction_persists_null_degeneracy_when_omitted(db_engine) -> None:
    """Omitting ``degeneracy`` persists a NULL — never silently defaulted to 1.0."""
    payload = _minimal_payload()
    assert "degeneracy" not in payload["kinetics"][0]

    with _isolated_session(db_engine) as session:
        session.add(AppUser(id=602, username="degeneracy_null_tester"))
        session.flush()

        request = ComputedReactionUploadRequest(**payload)
        summary = persist_computed_reaction_upload(
            session, request, created_by=602
        )

        kin = session.scalars(
            select(Kinetics).where(Kinetics.id.in_(summary["kinetics_ids"]))
        ).one()
        assert kin.degeneracy is None
