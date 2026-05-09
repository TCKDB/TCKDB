"""Workflow wiring tests for ``calc_geometry_validation`` (phase 1).

Phase 1 wires :func:`run_and_persist_geometry_validation` into the
computed-species and computed-reaction upload workflows for opt
calculations only. These tests verify the wiring contract:

* matching identity → ``passed`` row written
* mismatched identity → ``fail`` row written (evidence, not a gate)
* missing data → upload still succeeds, no row written
* non-opt calcs → no row written in this phase
* TS opt → deferred (no row written)

The pure chemistry seam is exercised separately in
``tests/services/test_geometry_validation.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
)
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    MoleculeKind,
    StereoKind,
    ValidationStatus,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)
from app.services.geometry_validation import (
    run_and_persist_geometry_validation,
)
from app.workflows.computed_reaction import persist_computed_reaction_upload
from app.workflows.computed_species import persist_computed_species_upload


# ---------------------------------------------------------------------------
# Geometry / SMILES fixtures (chosen so RDKit can perceive a real graph;
# pure single-atom species would be degenerate for the isomorphism test).
# ---------------------------------------------------------------------------

_XYZ_WATER = (
    "3\nwater\n"
    "O 0.0 0.0 0.117\n"
    "H 0.0 0.757 -0.469\n"
    "H 0.0 -0.757 -0.469"
)
_XYZ_METHANE = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)


_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Open a session on a connection-bound transaction that is always
    rolled back. Mirrors the helper used in
    ``test_computed_reaction_upload.py``: keeps each wiring test
    hermetic against species/InChI conflicts when reusing common
    SMILES like water."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Computed-species helpers
# ---------------------------------------------------------------------------


def _species_bundle(
    *, smiles: str, xyz: str, multiplicity: int = 1
) -> ComputedSpeciesUploadRequest:
    """Minimal one-conformer opt bundle for the wiring tests."""
    return ComputedSpeciesUploadRequest(
        **{
            "species_entry": {
                "smiles": smiles,
                "charge": 0,
                "multiplicity": multiplicity,
            },
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": xyz},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "level_of_theory": _LOT,
                        "software_release": _SOFTWARE,
                        "opt_result": {"converged": True},
                    },
                }
            ],
        }
    )


def _species_bundle_with_freq() -> ComputedSpeciesUploadRequest:
    """Bundle with an additional freq calc so we can assert non-opt is skipped."""
    return ComputedSpeciesUploadRequest(
        **{
            "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": _XYZ_WATER},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "level_of_theory": _LOT,
                        "software_release": _SOFTWARE,
                        "opt_result": {"converged": True},
                    },
                    "additional_calculations": [
                        {
                            "key": "freq0",
                            "type": "freq",
                            "level_of_theory": _LOT,
                            "software_release": _SOFTWARE,
                            "freq_result": {"n_imag": 0},
                        }
                    ],
                }
            ],
        }
    )


def _make_minimal_species_calc(
    session: Session,
    *,
    inchi_key: str,
    smiles: str = "O",
) -> Calculation:
    """Build a Species + SpeciesEntry + bare opt Calculation row with no
    attached geometries. Used by skip-path tests that want a calc
    without any input/output geometry link."""
    species = Species(
        smiles=smiles,
        inchi_key=inchi_key,
        charge=0,
        multiplicity=1,
        kind=MoleculeKind.molecule,
        stereo_kind=StereoKind.achiral,
    )
    session.add(species)
    session.flush()
    entry = SpeciesEntry(species_id=species.id, unmapped_smiles=smiles)
    session.add(entry)
    session.flush()
    calc = Calculation(
        type=CalculationType.opt,
        quality=CalculationQuality.raw,
        species_entry_id=entry.id,
    )
    session.add(calc)
    session.flush()
    return calc


# ---------------------------------------------------------------------------
# 1. computed-species opt happy path: matching identity → passed row
# ---------------------------------------------------------------------------


def test_computed_species_opt_persists_passed_row(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        outcome = persist_computed_species_upload(
            session, _species_bundle(smiles="O", xyz=_XYZ_WATER)
        )
        primary_id = outcome.conformers[0].primary_calculation.id
        row = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id == primary_id
            )
        )
        assert row is not None
        assert row.is_isomorphic is True
        assert row.validation_status == ValidationStatus.passed
        assert row.species_smiles == "O"
        assert row.output_geometry_id is not None


# ---------------------------------------------------------------------------
# 2. computed-species opt: mismatched identity → fail row, upload still OK
# ---------------------------------------------------------------------------


def test_computed_species_opt_records_fail_when_identity_mismatch(
    db_engine,
) -> None:
    """Geometry is methane but the declared species is water — the
    chemistry layer must reject isomorphism, and the wiring layer must
    persist that as evidence (validation_status=fail) rather than abort
    the upload. This is the policy: phase-1 is non-blocking."""
    with _isolated_session(db_engine) as session:
        bundle = _species_bundle(smiles="O", xyz=_XYZ_METHANE)
        outcome = persist_computed_species_upload(session, bundle)
        primary_id = outcome.conformers[0].primary_calculation.id
        row = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id == primary_id
            )
        )
        assert row is not None
        assert row.is_isomorphic is False
        assert row.validation_status == ValidationStatus.fail
        assert row.validation_reason is not None


# ---------------------------------------------------------------------------
# 3. Missing data: helper-level skip leaves no row, upload unaffected
# ---------------------------------------------------------------------------


def test_helper_skips_when_output_geometry_missing(db_engine) -> None:
    """If a calculation has no attached output geometry, the helper is a
    no-op and writes nothing. Constructed by adding a bare
    Calculation row directly so we can assert the skip semantics
    without fighting the workflow's fallback geometry attachment."""
    with _isolated_session(db_engine) as session:
        calc = _make_minimal_species_calc(
            session, inchi_key="GVSKIP000000000000000000001"
        )
        result = run_and_persist_geometry_validation(
            session, calc, species_smiles="O"
        )
        assert result is None
        assert calc.geometry_validation is None


# ---------------------------------------------------------------------------
# 4. Non-opt calcs are not validated in phase 1
# ---------------------------------------------------------------------------


def test_freq_calc_does_not_get_geometry_validation(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        outcome = persist_computed_species_upload(
            session, _species_bundle_with_freq()
        )
        freq_calc = outcome.conformers[0].additional_calculations[0]
        assert freq_calc.type == CalculationType.freq
        freq_row = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id == freq_calc.id
            )
        )
        assert freq_row is None

        primary_id = outcome.conformers[0].primary_calculation.id
        opt_row = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id == primary_id
            )
        )
        assert opt_row is not None
        assert opt_row.validation_status == ValidationStatus.passed


# ---------------------------------------------------------------------------
# 5. The GET endpoint reads the persisted row through the live workflow
# ---------------------------------------------------------------------------


def test_get_geometry_validation_endpoint_returns_persisted_row(
    client, db_session
) -> None:
    """End-to-end: real upload writes a row, real GET surfaces it."""
    payload = {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _XYZ_WATER},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": _LOT,
                    "software_release": _SOFTWARE,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=payload,
        headers={"Idempotency-Key": "geom-val-wiring-end2end"},
    )
    assert resp.status_code in (200, 201), resp.text

    # Use the response-reported calculation id rather than a raw query;
    # other tests in the session may have committed unrelated opt calcs.
    primary_id = resp.json()["conformers"][0]["primary_calculation"][
        "calculation_id"
    ]
    assert primary_id is not None

    read = client.get(f"/api/v1/calculations/{primary_id}/geometry-validation")
    assert read.status_code == 200
    body = read.json()
    assert body["calculation_id"] == primary_id
    assert body["is_isomorphic"] is True
    assert body["validation_status"] == ValidationStatus.passed.value
    assert body["species_smiles"] == "O"


# ---------------------------------------------------------------------------
# 6. computed-reaction species-side opt also gets validated
# ---------------------------------------------------------------------------


def _reaction_payload_h2o_self() -> dict:
    """A trivial degenerate water-water "reaction" payload — only used
    here to drive the workflow far enough that species-side opt calcs
    are persisted and validated. The reaction graph itself is
    immaterial to this test."""
    return {
        "analysis_software_release": {"name": "Arkane", "version": "3.0"},
        "species": [
            {
                "key": "wA",
                "species_entry": {
                    "smiles": "O",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "conformers": [
                    {
                        "key": "wA-conf",
                        "geometry": {"key": "wA-geom", "xyz_text": _XYZ_WATER},
                        "calculation": {
                            "key": "wA-opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
            {
                "key": "wB",
                "species_entry": {
                    "smiles": "O",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "conformers": [
                    {
                        "key": "wB-conf",
                        "geometry": {"key": "wB-geom", "xyz_text": _XYZ_WATER},
                        "calculation": {
                            "key": "wB-opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
        ],
        "reversible": True,
        "reactant_keys": ["wA"],
        "product_keys": ["wB"],
    }


def test_computed_reaction_species_opt_writes_validation_rows(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        # Snapshot the max calc id BEFORE running the workflow so we
        # can filter strictly to calcs created in THIS transaction.
        # Prior committed tests in the same session may have left
        # opt calcs attached to the deduplicated water species_entry,
        # which a naive `in_(species_entry_ids)` filter would also
        # pick up.
        baseline = session.scalar(select(func.coalesce(func.max(Calculation.id), 0)))

        request = ComputedReactionUploadRequest(**_reaction_payload_h2o_self())
        persist_computed_reaction_upload(session, request)

        new_opt_calcs = session.scalars(
            select(Calculation).where(
                Calculation.type == CalculationType.opt,
                Calculation.id > baseline,
            )
        ).all()
        # Two species-side opt calcs (one per species). No TS in this payload.
        assert len(new_opt_calcs) == 2

        for calc in new_opt_calcs:
            row = session.scalar(
                select(CalculationGeometryValidation).where(
                    CalculationGeometryValidation.calculation_id == calc.id
                )
            )
            assert row is not None
            assert row.validation_status == ValidationStatus.passed


# ---------------------------------------------------------------------------
# 7. TS opt geometry validation is deferred (phase-1 boundary)
# ---------------------------------------------------------------------------


def test_ts_opt_does_not_get_geometry_validation(db_engine) -> None:
    """End-to-end TS deferral: a computed-reaction bundle with a TS
    must persist validation rows for the species-side opt calcs but
    NOT for the TS opt calc. The species-graph isomorphism check
    cannot be applied to a TS — its connectivity sits between the
    reactant and product graphs — so the wiring is intentionally
    skipped at the workflow layer."""
    payload = {
        "analysis_software_release": {"name": "Arkane", "version": "3.0"},
        "species": [
            {
                "key": "ch3",
                "species_entry": {
                    "smiles": "[CH3]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "conformers": [
                    {
                        "key": "ch3-conf",
                        "geometry": {
                            "key": "ch3-geom",
                            "xyz_text": (
                                "4\nmethyl\n"
                                "C  0.000  0.000  0.000\n"
                                "H  1.080  0.000  0.000\n"
                                "H -0.540  0.935  0.000\n"
                                "H -0.540 -0.935  0.000"
                            ),
                        },
                        "calculation": {
                            "key": "ch3-opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
            {
                "key": "h",
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "conformers": [
                    {
                        "key": "h-conf",
                        "geometry": {
                            "key": "h-geom",
                            "xyz_text": "1\nH\nH 0.0 0.0 0.0",
                        },
                        "calculation": {
                            "key": "h-opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
            {
                "key": "ch4",
                "species_entry": {
                    "smiles": "C",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "conformers": [
                    {
                        "key": "ch4-conf",
                        "geometry": {
                            "key": "ch4-geom",
                            "xyz_text": _XYZ_METHANE,
                        },
                        "calculation": {
                            "key": "ch4-opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [],
            },
        ],
        "reversible": False,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {
                "key": "ts-geom",
                "xyz_text": (
                    "5\nTS for CH3 + H -> CH4\n"
                    "C  0.000  0.000  0.000\n"
                    "H  0.629  0.629  0.629\n"
                    "H -0.629 -0.629  0.629\n"
                    "H -0.629  0.629 -0.629\n"
                    "H  0.000  0.000  1.400"
                ),
            },
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "label": "CH3 + H -> CH4 TS",
        },
    }

    with _isolated_session(db_engine) as session:
        baseline = session.scalar(
            select(func.coalesce(func.max(Calculation.id), 0))
        )
        summary = persist_computed_reaction_upload(
            session, ComputedReactionUploadRequest(**payload)
        )
        assert summary["transition_state_entry_id"] is not None

        # Find this transaction's calcs by id snapshot to avoid
        # cross-test contamination from prior committed runs.
        new_calcs = session.scalars(
            select(Calculation).where(Calculation.id > baseline)
        ).all()

        species_opt_calcs = [
            c
            for c in new_calcs
            if c.type == CalculationType.opt
            and c.species_entry_id is not None
        ]
        ts_opt_calcs = [
            c
            for c in new_calcs
            if c.type == CalculationType.opt
            and c.transition_state_entry_id is not None
        ]
        # CH3 + H + CH4 = three species opts, one TS opt.
        assert len(species_opt_calcs) == 3
        assert len(ts_opt_calcs) == 1

        # Species-side opts: validation row written.
        for calc in species_opt_calcs:
            row = session.scalar(
                select(CalculationGeometryValidation).where(
                    CalculationGeometryValidation.calculation_id == calc.id
                )
            )
            assert row is not None, (
                f"species opt calc {calc.id} should have a "
                f"geometry-validation row"
            )
            assert row.validation_status == ValidationStatus.passed

        # TS-side opt: NO validation row.
        ts_calc = ts_opt_calcs[0]
        ts_row = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id == ts_calc.id
            )
        )
        assert ts_row is None, (
            f"TS opt calc {ts_calc.id} must not get a geometry-validation row "
            f"in phase 1; species graph-isomorphism is not a valid TS check."
        )


def test_helper_skips_when_species_smiles_is_none(db_engine) -> None:
    """TS opt geometry validation is intentionally NOT wired in phase 1.

    The current ``validate_calculation_geometry`` service takes a
    single ``species_smiles`` graph and decides identity by graph
    isomorphism. A TS does not have a single canonical SMILES — its
    connectivity sits between the reactant and product graphs, so
    feeding either one would systematically fail. The chosen contract
    is therefore:

    * ``computed_reaction.persist_computed_reaction_upload`` only calls
      :func:`run_and_persist_geometry_validation` for species-side
      calcs, never for the TS calc.
    * If a future caller does invoke the helper with
      ``species_smiles=None`` (the natural shape for TS), the helper
      must skip and write nothing.

    This test asserts the second half of that contract directly.
    """
    with _isolated_session(db_engine) as session:
        calc = _make_minimal_species_calc(
            session, inchi_key="GVTSDEFER000000000000000001"
        )
        out = run_and_persist_geometry_validation(
            session, calc, species_smiles=None
        )
        assert out is None
        assert calc.geometry_validation is None
