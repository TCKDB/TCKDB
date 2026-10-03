"""A calculation's geometries must carry its subject's isotopes (#666).

``calculation_geometry_composition_mismatch`` counts elements and reads ``D``,
``T`` and ``[2H]`` as hydrogen, so on its own it let a deuterium energy be
filed under a protium species: a ``[H]`` record linking one single point on an
H geometry and another on an H geometry declaring ``isotopes {1: 2}`` returned
201. ``calculation_geometry_isotope_mismatch`` closes that.

Design, each pinned below:

* **Count-based.** A calculation geometry has no atom map to the species graph,
  so the multiset of ``(element, mass_number)`` substitutions is compared (the
  same core as the conformer rule). CH2D-OH vs CH3-OD is not distinguished.
* **The reference is ``species_entry.isotope_key``**, not ``species.smiles``,
  which is isotope-blind by design.
* **A ``D``/``T`` element spelling is isotope-silent**, by the documented
  decision in ``resolve_element_symbol``. That is pinned as an *acceptance*
  here so it cannot change by accident.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest
from app.schemas.workflows.transition_state_upload import TransitionStateUploadRequest
from app.services.calculation_resolution import (
    attach_calculation_input_geometries,
    attach_calculation_output_geometries,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.workflows.computed_species import persist_computed_species_upload
from app.workflows.transition_state import persist_transition_state_upload

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_USER_ID = 50_722
_CODE = "calculation_geometry_isotope_mismatch"

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_D = "1\nD\nD 0.0 0.0 0.0"
#: Methane. Atom 1 is carbon, atoms 2-5 are hydrogens.
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_H3_TS = "3\nH...H...H\nH 0.0 0.0 0.0\nH 0.0 0.0 0.9\nH 0.0 0.0 1.8"


@contextmanager
def _isolated_session(db_conn) -> Iterator[Session]:
    session = Session(bind=db_conn, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="calc_geometry_isotopes"))
        session.flush()
        yield session
    finally:
        session.close()


def _geom(xyz: str, isotopes: dict[int, int] | None) -> dict:
    out: dict = {"xyz_text": xyz}
    if isotopes is not None:
        out["isotopes"] = isotopes
    return out


def _species_bundle(
    *,
    smiles: str,
    multiplicity: int,
    conformer_xyz: str,
    conformer_isotopes: dict[int, int] | None = None,
    opt_input: dict | None = None,
    sp_output: dict | None = None,
) -> dict:
    primary: dict = {
        "key": "opt0",
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "opt_result": {"converged": True},
    }
    if opt_input is not None:
        primary["input_geometries"] = [opt_input]
    additional: list[dict] = []
    if sp_output is not None:
        additional.append(
            {
                "key": "sp0",
                "type": "sp",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "sp_result": {"electronic_energy_hartree": -0.5},
                "output_geometries": [{"geometry": sp_output, "role": "final"}],
            }
        )
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": "c0",
                "geometry": _geom(conformer_xyz, conformer_isotopes),
                "primary_calculation": primary,
                "additional_calculations": additional,
            }
        ],
    }


def _upload(session: Session, payload: dict):
    return persist_computed_species_upload(
        session, ComputedSpeciesUploadRequest(**payload), created_by=_USER_ID
    )


def _refused(db_conn, payload: dict) -> CodedValueError:
    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _upload(session, payload)
    assert excinfo.value.code == _CODE
    return excinfo.value


def _accepted(db_conn, payload: dict) -> None:
    with _isolated_session(db_conn) as session:
        _upload(session, payload)
        session.flush()


def _protium(**kw) -> dict:
    return _species_bundle(smiles="[H]", multiplicity=2, conformer_xyz=_XYZ_H, **kw)


def _deuterium(**kw) -> dict:
    return _species_bundle(
        smiles="[2H]",
        multiplicity=2,
        conformer_xyz=_XYZ_H,
        conformer_isotopes={1: 2},
        **kw,
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_protium_species_with_a_deuterium_input_geometry_is_refused(db_conn) -> None:
    error = _refused(db_conn, _protium(opt_input=_geom(_XYZ_H, {1: 2})))
    assert error.context["owner_kind"] == "species_entry"
    assert error.context["geometry_substitutions"] == "2Hx1"
    assert error.context["subject_substitutions"] == "none (all standard isotopes)"
    assert "input_geometries[0]" in error.context["field"]


def test_protium_species_with_a_deuterium_output_geometry_is_refused(db_conn) -> None:
    """The issue's shape: an sp energy on a deuterium geometry, protium record."""

    error = _refused(db_conn, _protium(sp_output=_geom(_XYZ_H, {1: 2})))
    assert "output_geometries[0]" in error.context["field"]


def test_deuterated_species_with_a_protium_calculation_geometry_is_refused(
    db_conn,
) -> None:
    error = _refused(db_conn, _deuterium(sp_output=_geom(_XYZ_H, None)))
    assert error.context["geometry_substitutions"] == "none (all standard isotopes)"
    assert error.context["subject_substitutions"] == "2Hx1"


def test_ch3d_species_with_a_ch4_calculation_geometry_is_refused(db_conn) -> None:
    payload = _species_bundle(
        smiles="[2H]C",
        multiplicity=1,
        conformer_xyz=_XYZ_CH4,
        conformer_isotopes={2: 2},
        opt_input=_geom(_XYZ_CH4, None),
    )
    _refused(db_conn, payload)


def test_ch4_species_with_a_ch3d_calculation_geometry_is_refused(db_conn) -> None:
    payload = _species_bundle(
        smiles="C",
        multiplicity=1,
        conformer_xyz=_XYZ_CH4,
        opt_input=_geom(_XYZ_CH4, {2: 2}),
    )
    _refused(db_conn, payload)


def test_a_wrong_isotope_count_is_refused(db_conn) -> None:
    """CH3D species, CH2D2 geometry: same element, different count."""

    payload = _species_bundle(
        smiles="[2H]C",
        multiplicity=1,
        conformer_xyz=_XYZ_CH4,
        conformer_isotopes={2: 2},
        sp_output=_geom(_XYZ_CH4, {2: 2, 3: 2}),
    )
    error = _refused(db_conn, payload)
    assert error.context["geometry_substitutions"] == "2Hx2"
    assert error.context["subject_substitutions"] == "2Hx1"


def test_the_refusal_carries_no_row_id(db_conn) -> None:
    error = _refused(db_conn, _protium(opt_input=_geom(_XYZ_H, {1: 2})))
    text = str(error)
    assert "id=" not in text
    assert set(error.context) == {
        "field",
        "owner_kind",
        "geometry_substitutions",
        "subject_substitutions",
    }


# ---------------------------------------------------------------------------
# Acceptances
# ---------------------------------------------------------------------------


def test_deuterated_species_with_a_matching_deuterated_geometry_is_accepted(
    db_conn,
) -> None:
    _accepted(
        db_conn,
        _deuterium(opt_input=_geom(_XYZ_H, {1: 2}), sp_output=_geom(_XYZ_H, {1: 2})),
    )


def test_ch3d_species_with_a_matching_ch3d_geometry_is_accepted(db_conn) -> None:
    _accepted(
        db_conn,
        _species_bundle(
            smiles="[2H]C",
            multiplicity=1,
            conformer_xyz=_XYZ_CH4,
            conformer_isotopes={2: 2},
            opt_input=_geom(_XYZ_CH4, {2: 2}),
            sp_output=_geom(_XYZ_CH4, {3: 2}),  # isotopomer: not distinguished
        ),
    )


def test_unlabelled_geometries_on_an_unlabelled_species_are_unaffected(
    db_conn,
) -> None:
    _accepted(
        db_conn, _protium(opt_input=_geom(_XYZ_H, None), sp_output=_geom(_XYZ_H, None))
    )


def test_an_explicit_standard_isotope_is_not_a_substitution(db_conn) -> None:
    """``{1: 1}`` on a hydrogen is dropped, so it cannot contradict protium."""

    _accepted(db_conn, _protium(opt_input=_geom(_XYZ_H, {1: 1})))


def test_a_d_spelling_is_isotope_silent_by_design(db_conn) -> None:
    """PINS CURRENT BEHAVIOUR, documented in ``resolve_element_symbol``.

    A ``D`` in the element column is composition-neutral *and* isotope-silent:
    isotope identity is carried only by ``geometry.isotopes`` and SMILES
    labels. So a ``D`` geometry on a protium species is accepted here. That
    differs from ``normal_modes.atomic_mass``, which reads ``D`` as mass 2; the
    split is tracked as its own issue and deliberately not changed by #666.
    If you are making D/T an isotope declaration, this test is the one that
    should change, together with the conformer rule and ``validate_isotope``.
    """

    _accepted(db_conn, _protium(opt_input=_geom(_XYZ_D, None)))


# ---------------------------------------------------------------------------
# Transition states: the reference is the summed reactants
# ---------------------------------------------------------------------------


def _ts_payload(*, geometry_isotopes: dict[int, int] | None, deuterated: bool) -> dict:
    h = {"smiles": "[2H]" if deuterated else "[H]", "charge": 0, "multiplicity": 2}
    h2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    return {
        "charge": 0,
        "multiplicity": 2,
        "geometry": _geom(_XYZ_H3_TS, geometry_isotopes),
        "reaction": {
            "reversible": True,
            "reactants": [{"species_entry": h}, {"species_entry": h2}],
            "products": [{"species_entry": h}, {"species_entry": h2}],
        },
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True},
        },
    }


def _ts(session: Session, payload: dict):
    return persist_transition_state_upload(
        session, TransitionStateUploadRequest(**payload), created_by=_USER_ID
    )


def test_ts_geometry_must_carry_the_summed_reactant_isotopes(db_conn) -> None:
    """D + H2: the saddle point holds one deuteron, so an unlabelled one is refused."""

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _ts(session, _ts_payload(geometry_isotopes=None, deuterated=True))
    assert excinfo.value.code == _CODE
    assert excinfo.value.context["owner_kind"] == "transition_state_entry"
    assert excinfo.value.context["subject_substitutions"] == "2Hx1"


def test_ts_geometry_with_an_isotope_the_reactants_lack_is_refused(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _ts(session, _ts_payload(geometry_isotopes={1: 2}, deuterated=False))
    assert excinfo.value.code == _CODE


def test_ts_geometry_with_the_matching_isotope_is_accepted(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        _ts(session, _ts_payload(geometry_isotopes={1: 2}, deuterated=True))
        session.flush()


# ---------------------------------------------------------------------------
# The service enforces it, not the schema
# ---------------------------------------------------------------------------


def test_the_service_enforces_it_on_an_unvalidated_payload(db_conn) -> None:
    """``model_construct`` skips every Pydantic validator; the service still refuses."""

    with _isolated_session(db_conn) as session:
        _upload(session, _protium())
        session.flush()
        calc = session.scalars(
            select(Calculation).where(
                Calculation.created_by == _USER_ID, Calculation.type == "opt"
            )
        ).one()
        unvalidated = GeometryPayload.model_construct(
            xyz_text=_XYZ_H, isotopes={1: 2}
        )
        with pytest.raises(CodedValueError) as excinfo:
            attach_calculation_input_geometries(
                session,
                calc=calc,
                explicit_input_geometries=[unvalidated],
                fallback_geometry_id=None,
                context="test",
            )
    assert excinfo.value.code == _CODE


def _protium_calc_and_deuterium_geometry(session: Session, calc_type: str):
    _upload(session, _protium(sp_output=_geom(_XYZ_H, None)))
    session.flush()
    calc = session.scalars(
        select(Calculation).where(
            Calculation.created_by == _USER_ID, Calculation.type == calc_type
        )
    ).one()
    deuterium = resolve_geometry_payload(
        session, GeometryPayload.model_construct(xyz_text=_XYZ_H, isotopes={1: 2})
    )
    return calc, deuterium.id


def test_the_input_fallback_branch_also_enforces_it(db_conn) -> None:
    """``geometry_key`` fallback: no explicit list, a resolved geometry id."""

    with _isolated_session(db_conn) as session:
        calc, geometry_id = _protium_calc_and_deuterium_geometry(session, "sp")
        with pytest.raises(CodedValueError) as excinfo:
            attach_calculation_input_geometries(
                session,
                calc=calc,
                explicit_input_geometries=[],
                fallback_geometry_id=geometry_id,
                context="test",
            )
    assert excinfo.value.code == _CODE


def test_the_output_fallback_branch_also_enforces_it(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        calc, geometry_id = _protium_calc_and_deuterium_geometry(session, "opt")
        with pytest.raises(CodedValueError) as excinfo:
            attach_calculation_output_geometries(
                session,
                calc=calc,
                explicit_output_geometries=[],
                fallback_geometry_id=geometry_id,
                context="test",
            )
    assert excinfo.value.code == _CODE


def test_ts_reference_sums_every_reactant_not_just_the_first(db_conn) -> None:
    """D + HD -> two deuterons in the system; one label is not enough, two are."""

    def payload(isotopes: dict[int, int]) -> dict:
        d = {"smiles": "[2H]", "charge": 0, "multiplicity": 2}
        hd = {"smiles": "[2H][H]", "charge": 0, "multiplicity": 1}
        p = _ts_payload(geometry_isotopes=isotopes, deuterated=True)
        p["reaction"]["reactants"] = [{"species_entry": d}, {"species_entry": hd}]
        p["reaction"]["products"] = [{"species_entry": d}, {"species_entry": hd}]
        return p

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _ts(session, payload({1: 2}))
        assert excinfo.value.context["subject_substitutions"] == "2Hx2"
    with _isolated_session(db_conn) as session:
        _ts(session, payload({1: 2, 2: 2}))
        session.flush()
