"""A calculation's geometries must be made of its subject's atoms.

Nothing compared them before. A conformer geometry has been checked against
its species entry since ``assert_geometry_composition_matches_identity``
landed; a *calculation's* geometries were checked against nothing, on any
path, for any subject — that function's own docstring said so and called the
input half "a genuine open gap". Two deposits that succeeded on ``main``:

* a species entry declaring methane (``smiles: "C"``) whose optimisation
  carried benzene coordinates as its input geometry and whose single point
  carried benzene as its *output* geometry;
* a transition state for ``[CH]=O + C -> C=O + [CH3]`` whose frequency
  calculation carried methane, reached by naming a reactant's ``geometry_key``
  in a bundle-global namespace.

Both are reproduced below as refusals.

**The accepting half of this file is the point.** A blocking check that
refuses correct science is worse than no check (ADR 0008), and the three
shapes that had to survive the design are each pinned here as an *acceptance*:

1. a transition state's geometry spans the whole reacting system and is
   neither reactant nor product;
2. an isotopologue is the same molecule by element, and D and T are hydrogen;
3. a scan point, an IRC endpoint and a dissociated optimisation are
   deliberately not the equilibrium structure.

Plus the case the rule's shape turns on, which the two blocking neighbours
had already settled and which is easy to get wrong here: composition is
conserved by every calculation type, so an *output* geometry is checked
exactly like an input one.

The full argument, including the cases considered and rejected, is in
``backend/docs/specs/calculation_geometry_composition.md``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.workflows.computed_species import persist_computed_species_upload
from app.workflows.transition_state import persist_transition_state_upload

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_USER_ID = 50_721

#: Methane, CH4.
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
#: Methane with one C-H stretched to 5 A: a dissociated fragment pair. Still
#: CH4 by composition, because dissociation moves nuclei rather than deleting
#: them. This is the geometry the exemption for output geometries was written
#: to protect, and it passes.
_XYZ_CH4_DISSOCIATED = (
    "5\nmethane, one hydrogen at 5 A\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  5.000"
)
#: Benzene, C6H6. Nothing to do with methane.
_XYZ_BENZENE = (
    "12\nbenzene\n"
    "C  1.396  0.000  0.000\n"
    "C  0.698  1.209  0.000\n"
    "C -0.698  1.209  0.000\n"
    "C -1.396  0.000  0.000\n"
    "C -0.698 -1.209  0.000\n"
    "C  0.698 -1.209  0.000\n"
    "H  2.479  0.000  0.000\n"
    "H  1.240  2.147  0.000\n"
    "H -1.240  2.147  0.000\n"
    "H -2.479  0.000  0.000\n"
    "H -1.240 -2.147  0.000\n"
    "H  1.240 -2.147  0.000"
)
#: Fully deuterated methane, with the hydrogens written ``D`` — a spelling
#: Gaussian, ORCA, Molpro and CFOUR all emit or accept.
_XYZ_CD4 = (
    "5\nperdeuteromethane\n"
    "C  0.000  0.000  0.000\n"
    "D  0.629  0.629  0.629\n"
    "D -0.629 -0.629  0.629\n"
    "D -0.629  0.629 -0.629\n"
    "D  0.629 -0.629 -0.629"
)
#: Ethane, C2H6, at its equilibrium-ish geometry.
_XYZ_ETHANE = (
    "8\nethane\n"
    "C  0.000  0.000  0.765\n"
    "C  0.000  0.000 -0.765\n"
    "H  1.017  0.000  1.163\n"
    "H -0.508  0.881  1.163\n"
    "H -0.508 -0.881  1.163\n"
    "H  1.017  0.000 -1.163\n"
    "H -0.508  0.881 -1.163\n"
    "H -0.508 -0.881 -1.163"
)
#: The same ethane with its C-C-H-H dihedral driven to eclipsed and the C-C
#: bond stretched: a rotor-scan point, deliberately not the minimum.
_XYZ_ETHANE_SCAN_POINT = (
    "8\nethane, eclipsed, C-C stretched\n"
    "C  0.000  0.000  0.900\n"
    "C  0.000  0.000 -0.900\n"
    "H  1.017  0.000  1.300\n"
    "H -0.508  0.881  1.300\n"
    "H -0.508 -0.881  1.300\n"
    "H  1.017  0.000 -1.300\n"
    "H -0.508  0.881 -1.300\n"
    "H -0.508 -0.881 -1.300"
)

#: The H + H2 -> H2 + H saddle point: three atoms, the whole reacting system.
_XYZ_H3_TS = "3\nH...H...H\nH 0.0 0.0 0.0\nH 0.0 0.0 0.9\nH 0.0 0.0 1.8"
#: The reactant end of that IRC. Still three atoms.
_XYZ_H3_IRC_R = "3\nR\nH 0.0 0.0 0.00\nH 0.0 0.0 0.74\nH 0.0 0.0 2.50"
#: The product end. Still three atoms.
_XYZ_H3_IRC_F = "3\nF\nH 0.0 0.0 0.00\nH 0.0 0.0 1.76\nH 0.0 0.0 2.50"
#: One hydrogen atom — a *participant* of that reaction, not a point on it.
_XYZ_H = "1\nH\nH 0.0 0.0 0.0"


@contextmanager
def _isolated_session(db_conn) -> Iterator[Session]:
    session = Session(bind=db_conn, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="calc_geometry_composition"))
        session.flush()
        yield session
    finally:
        session.close()


def _species_bundle(
    *,
    smiles: str,
    conformer_xyz: str,
    charge: int = 0,
    multiplicity: int = 1,
    isotopes: dict[int, int] | None = None,
    opt_input_xyz: str | None = None,
    sp_output_xyz: str | None = None,
) -> dict:
    geometry: dict = {"xyz_text": conformer_xyz}
    if isotopes is not None:
        geometry["isotopes"] = isotopes
    primary: dict = {
        "key": "opt0",
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "opt_result": {"converged": True},
    }
    if opt_input_xyz is not None:
        primary["input_geometries"] = [{"xyz_text": opt_input_xyz}]
    additional: list[dict] = []
    if sp_output_xyz is not None:
        additional.append(
            {
                "key": "sp0",
                "type": "sp",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "sp_result": {"electronic_energy_hartree": -40.5},
                "output_geometries": [
                    {"geometry": {"xyz_text": sp_output_xyz}, "role": "final"}
                ],
            }
        )
    return {
        "species_entry": {
            "smiles": smiles,
            "charge": charge,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": "c0",
                "geometry": geometry,
                "primary_calculation": primary,
                "additional_calculations": additional,
            }
        ],
    }


def _upload_species(session: Session, payload: dict):
    return persist_computed_species_upload(
        session,
        ComputedSpeciesUploadRequest(**payload),
        created_by=_USER_ID,
    )


def _ts_payload(*, irc_point_xyz: str | None = None) -> dict:
    """A standalone TS upload for ``[H] + [H][H] -> [H] + [H][H]``."""

    additional: list[dict] = [
        {
            "type": "freq",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_result": {"n_imag": 1, "imag_freq_cm1": -1500.0},
        }
    ]
    if irc_point_xyz is not None:
        additional.append(
            {
                "type": "irc",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "output_geometries": [
                    {"geometry": {"xyz_text": irc_point_xyz}, "role": "irc_forward"}
                ],
            }
        )
    return {
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"xyz_text": _XYZ_H3_TS},
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
        },
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True},
        },
        "additional_calculations": additional,
    }


def _upload_ts(session: Session, payload: dict):
    return persist_transition_state_upload(
        session,
        TransitionStateUploadRequest(**payload),
        created_by=_USER_ID,
    )


# ---------------------------------------------------------------------------
# It fires — and says which repair, not merely that something went wrong
# ---------------------------------------------------------------------------


def test_an_input_geometry_of_another_molecule_is_refused(db_conn) -> None:
    """The first reproduction: benzene as methane's optimisation input."""

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload_species(
                session,
                _species_bundle(
                    smiles="C", conformer_xyz=_XYZ_CH4, opt_input_xyz=_XYZ_BENZENE
                ),
            )

    error = excinfo.value
    assert error.code == "calculation_geometry_composition_mismatch"
    assert error.context["owner_kind"] == "species_entry"
    assert error.context["geometry_formula"] == "C6H6"
    assert error.context["subject_formula"] == "CH4"
    assert "input_geometries[0]" in error.context["field"]


def test_an_output_geometry_of_another_molecule_is_refused(db_conn) -> None:
    """The output half, which the note that stood here said to leave open.

    The argument for leaving it open was that "an optimisation that
    dissociated is science to record". Benzene is not a dissociated methane —
    it has seven more nuclei, and no calculation creates a nucleus. The
    genuinely dissociated case is accepted below.
    """

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload_species(
                session,
                _species_bundle(
                    smiles="C", conformer_xyz=_XYZ_CH4, sp_output_xyz=_XYZ_BENZENE
                ),
            )

    error = excinfo.value
    assert error.code == "calculation_geometry_composition_mismatch"
    assert error.context["geometry_formula"] == "C6H6"
    assert error.context["subject_formula"] == "CH4"
    assert "output_geometries[0]" in error.context["field"]


def test_a_transition_states_calculation_may_not_carry_a_participant(
    db_conn,
) -> None:
    """One hydrogen atom as an IRC point of a three-atom saddle.

    A participant of the reaction is exactly the wrong reference: the TS sits
    on the potential energy surface of *all* the atoms, so every point on its
    IRC has all three. This is the shape the rule's TS branch exists to get
    right in both directions — it must refuse the participant here and accept
    the whole system in the acceptance tests below.
    """

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload_ts(session, _ts_payload(irc_point_xyz=_XYZ_H))

    error = excinfo.value
    assert error.code == "calculation_geometry_composition_mismatch"
    assert error.context["owner_kind"] == "transition_state_entry"
    assert error.context["geometry_formula"] == "H"
    assert error.context["subject_formula"] == "H3"


def test_a_ts_calculation_may_not_borrow_a_reactants_geometry_by_key(
    db_conn,
) -> None:
    """The second reproduction, and the reason the fallback branch is checked.

    On ``/uploads/computed-reaction`` a calculation may name any geometry in
    the bundle through ``geometry_key``, resolved against a single
    bundle-global map. The wire schema narrows that key to a species' own
    conformers for a *species'* calculations and does not do the same for a
    *transition state's*, so a TS frequency calculation could name a
    reactant's geometry and have it attached. Nothing objected: the deposit
    below stored methane as the input geometry of a C2H5O saddle point's
    frequency job.

    This arrives on the fallback branch, not the producer-explicit one, which
    is why checking only ``input_geometries`` / ``output_geometries`` would
    have left it open.
    """

    import glob
    import json

    from app.schemas.workflows.computed_reaction_upload import (
        ComputedReactionUploadRequest,
    )
    from app.workflows.computed_reaction import persist_computed_reaction_upload

    fixture = glob.glob(
        "tests/fixtures/arc_runs/reaction_1/tckdb_payloads/computed_reaction/*.json"
    )[0]
    with open(fixture) as handle:
        doc = json.load(handle)

    ts_freq = next(
        calc
        for calc in doc["transition_state"]["calculations"]
        if calc["type"] == "freq"
    )
    ts_freq["input_geometries"] = []
    ts_freq["geometry_key"] = "r1_CH4_geom"  # a reactant. The TS is C2H5O.

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            persist_computed_reaction_upload(
                session,
                ComputedReactionUploadRequest(**doc),
                created_by=_USER_ID,
            )

    error = excinfo.value
    assert error.code == "calculation_geometry_composition_mismatch"
    assert error.context["owner_kind"] == "transition_state_entry"
    assert error.context["geometry_formula"] == "CH4"
    assert error.context["subject_formula"] == "C2H5O"
    assert "geometry_key" in error.context["field"]


def test_the_refusal_carries_no_row_id(db_conn) -> None:
    """DR-0028 Requirement 2: ``context`` names fields, never primary keys."""

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload_species(
                session,
                _species_bundle(
                    smiles="C", conformer_xyz=_XYZ_CH4, opt_input_xyz=_XYZ_BENZENE
                ),
            )

    context = excinfo.value.context
    assert set(context) == {
        "field",
        "owner_kind",
        "geometry_formula",
        "subject_formula",
    }
    # Formulas legitimately carry digits ("C6H6"); a leaked primary key would
    # be a bare integer or an all-digit string. Neither may appear.
    for value in context.values():
        assert not isinstance(value, int)
        assert not str(value).isdigit()
    # And the message body carries no id either.
    assert "id=" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# It accepts — the half that stops this refusing correct science later
# ---------------------------------------------------------------------------


def test_case_1_a_transition_states_geometries_span_the_whole_system(
    db_conn,
) -> None:
    """CASE 1. The saddle point is neither reactant nor product.

    For ``H + H2 -> H2 + H`` the TS holds three atoms; neither participant
    does. Every calculation the TS owns — the opt, the freq, the IRC — runs on
    all three, and each must be accepted against the *sum* of the reactants
    rather than against any one of them.
    """

    with _isolated_session(db_conn) as session:
        _upload_ts(session, _ts_payload(irc_point_xyz=_XYZ_H3_IRC_F))
        session.flush()

        ts_calcs = session.scalars(
            select(Calculation).where(
                Calculation.created_by == _USER_ID,
                Calculation.transition_state_entry_id.is_not(None),
            )
        ).all()
        assert {calc.type.value for calc in ts_calcs} == {"opt", "freq", "irc"}

        irc = next(calc for calc in ts_calcs if calc.type.value == "irc")
        linked = session.scalars(
            select(CalculationOutputGeometry.geometry_id).where(
                CalculationOutputGeometry.calculation_id == irc.id
            )
        ).all()
        assert len(linked) == 1


def test_case_2_deuterium_in_the_element_column_is_hydrogen(db_conn) -> None:
    """CASE 2, the ESS spelling: ``D`` written in the element column.

    Gaussian, ORCA, Molpro and CFOUR all emit or accept it, and ingestion
    deliberately preserves the token. A calculation geometry spelling its
    hydrogens ``D`` is CH4 by element, so it matches a ``smiles: "C"``
    identity. Comparing raw symbols would read this as containing an element
    the SMILES never mentions and refuse every such deposit.
    """

    with _isolated_session(db_conn) as session:
        _upload_species(
            session,
            _species_bundle(
                smiles="C",
                conformer_xyz=_XYZ_CH4,
                opt_input_xyz=_XYZ_CD4,
                sp_output_xyz=_XYZ_CD4,
            ),
        )
        session.flush()
        assert (
            session.scalar(
                select(Calculation.id).where(
                    Calculation.created_by == _USER_ID,
                    Calculation.type == "sp",
                )
            )
            is not None
        )


def test_case_2b_a_labelled_isotopologue_identity_is_not_a_mismatch(
    db_conn,
) -> None:
    """CASE 2, the canonical spelling: ``[2H]`` in the SMILES.

    CD3H's identity carries three isotope labels and its geometries carry the
    matching per-atom mass numbers. By element both sides are CH4, which is
    the whole point: a mass-aware composition comparison would refuse every
    isotopologue in the database, and isotope agreement is a separate rule
    that checks it exactly.
    """

    with _isolated_session(db_conn) as session:
        _upload_species(
            session,
            _species_bundle(
                smiles="[2H]C([2H])[2H]",
                conformer_xyz=_XYZ_CH4,
                # Atom 1 is the carbon; atoms 2-4 are the three deuterons.
                isotopes={2: 2, 3: 2, 4: 2},
                opt_input_xyz=_XYZ_CH4,
                sp_output_xyz=_XYZ_CH4_DISSOCIATED,
            ),
        )
        session.flush()
        assert (
            session.scalar(
                select(Calculation.id).where(
                    Calculation.created_by == _USER_ID,
                    Calculation.type == "sp",
                )
            )
            is not None
        )


def test_case_3_a_scan_point_is_deliberately_not_the_minimum(db_conn) -> None:
    """CASE 3. An eclipsed, stretched rotor-scan point of ethane.

    Right atoms, wrong arrangement, on purpose — that is what a scan point is.
    Nothing in the rule reads a coordinate, so distortion is invisible to it.
    A check that drifted into geometric plausibility would refuse exactly the
    structures a torsional potential is built from.
    """

    with _isolated_session(db_conn) as session:
        _upload_species(
            session,
            _species_bundle(
                smiles="CC",
                conformer_xyz=_XYZ_ETHANE,
                opt_input_xyz=_XYZ_ETHANE_SCAN_POINT,
                sp_output_xyz=_XYZ_ETHANE_SCAN_POINT,
            ),
        )
        session.flush()
        inputs = session.scalars(
            select(CalculationInputGeometry.geometry_id)
            .join(
                Calculation,
                Calculation.id == CalculationInputGeometry.calculation_id,
            )
            .where(Calculation.created_by == _USER_ID)
        ).all()
        assert inputs


def test_case_3b_an_irc_endpoint_in_a_product_well_is_accepted(db_conn) -> None:
    """CASE 3, the transition-state spelling: an IRC endpoint is not the TS.

    ``_XYZ_H3_IRC_F`` is the product side of the path — H2 formed, the third
    atom 2.5 A away. It is three atoms in an arrangement that is not the
    saddle point, and it is accepted, because only the atoms are compared.
    """

    with _isolated_session(db_conn) as session:
        _upload_ts(session, _ts_payload(irc_point_xyz=_XYZ_H3_IRC_R))
        session.flush()
        assert session.scalar(
            select(Calculation.id).where(
                Calculation.created_by == _USER_ID,
                Calculation.type == "irc",
            )
        )


def test_a_dissociated_optimisation_is_science_to_record(db_conn) -> None:
    """The case the output-geometry exemption was written to protect.

    Methane with one hydrogen pulled out to 5 A: a dissociated fragment pair,
    reported as the *output* of the optimisation and as a single point on it.
    It has the same five nuclei it started with, so it is accepted — which is
    why the exemption was unnecessary. Composition is conserved by every
    calculation type; connectivity is not, and connectivity is not checked.
    """

    with _isolated_session(db_conn) as session:
        _upload_species(
            session,
            _species_bundle(
                smiles="C",
                conformer_xyz=_XYZ_CH4,
                opt_input_xyz=_XYZ_CH4_DISSOCIATED,
                sp_output_xyz=_XYZ_CH4_DISSOCIATED,
            ),
        )
        session.flush()
        outputs = session.scalars(
            select(CalculationOutputGeometry.geometry_id)
            .join(
                Calculation,
                Calculation.id == CalculationOutputGeometry.calculation_id,
            )
            .where(Calculation.created_by == _USER_ID)
        ).all()
        assert outputs


def test_a_charged_species_is_compared_on_atoms_only(db_conn) -> None:
    """Charge is owned elsewhere and is not re-derived here.

    ``canonical_species_identity`` blocks a SMILES/charge disagreement, so per
    ADR 0008 section 9 this rule does not check charge — a second copy could
    only disagree with the first. An ammonium deposit therefore passes on its
    atoms.
    """

    xyz_nh4 = (
        "5\nammonium\n"
        "N  0.000  0.000  0.000\n"
        "H  0.589  0.589  0.589\n"
        "H -0.589 -0.589  0.589\n"
        "H -0.589  0.589 -0.589\n"
        "H  0.589 -0.589 -0.589"
    )
    with _isolated_session(db_conn) as session:
        _upload_species(
            session,
            _species_bundle(
                smiles="[NH4+]",
                conformer_xyz=xyz_nh4,
                charge=1,
                opt_input_xyz=xyz_nh4,
                sp_output_xyz=xyz_nh4,
            ),
        )
        session.flush()
        assert session.scalar(
            select(Calculation.id).where(Calculation.created_by == _USER_ID)
        )
