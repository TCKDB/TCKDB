"""A transition state must be made of its own reaction's atoms.

``validate_reaction_elemental_balance`` has always compared reactants against
products and has never mentioned the transition state, so a saddle point whose
molecular formula differed from the reaction it was attached to was accepted.
That is a contradiction of exactly the class the elemental-balance rule already
refuses one column to the left: a transition state is a stationary point on the
potential energy surface **of those atoms**, so a saddle point made of
different atoms cannot be that reaction's saddle point.

Definitional under ADR 0008, therefore blocking. The check runs on every
transition-state deposit path, mapped or not — an atom map catches a formula
mismatch implicitly, but only where a map was supplied, which would deliver the
stronger guarantee exactly to the deposits that were already the most careful.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.workflows.computed_reaction import persist_computed_reaction_upload
from app.workflows.transition_state import persist_transition_state_upload

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
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
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)
#: One hydrogen short of the reaction it will be attached to.
_XYZ_TS_MISSING_AN_ATOM = (
    "4\nCH3 fragment posing as a CH4 saddle point\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629"
)
#: ``CH3 + Cl -> CH3Cl``, for the case where capitalisation is the only thing
#: separating a correct saddle point from a refused one.
_XYZ_CL = "1\nchlorine atom\nCl 0.000 0.000 0.000"
_XYZ_CH3CL = (
    "5\nchloromethane\n"
    "C  0.000  0.000  0.000\n"
    "Cl 1.781  0.000  0.000\n"
    "H -0.372  1.028  0.000\n"
    "H -0.372 -0.514  0.890\n"
    "H -0.372 -0.514 -0.890"
)
#: The same saddle point, with its element symbols written the way plenty of
#: electronic-structure output writes them: chlorine shouted, carbon and
#: hydrogen whispered. ``geometry_atom.element`` stores whatever the XYZ said,
#: verbatim, so this deposit is where a case-sensitive comparison bites.
_XYZ_TS_MIXED_CASE = (
    "5\nTS for CH3 + Cl -> CH3Cl, elements as an ESS wrote them\n"
    "c  0.000  0.000  0.000\n"
    "CL 2.200  0.000  0.000\n"
    "h -0.300  1.028  0.000\n"
    "h -0.300 -0.514  0.890\n"
    "h -0.300 -0.514 -0.890"
)
#: Mixed case *and* genuinely the wrong atoms: one chlorine short.
_XYZ_TS_MIXED_CASE_MISSING_AN_ATOM = (
    "4\nmethyl fragment posing as a CH3Cl saddle point\n"
    "c  0.000  0.000  0.000\n"
    "h -0.300  1.028  0.000\n"
    "h -0.300 -0.514  0.890\n"
    "h -0.300 -0.514 -0.890"
)
#: The reaction's atoms plus a water molecule nobody declared.
_XYZ_TS_WITH_AN_UNDECLARED_SPECTATOR = (
    "8\nCH4 saddle point clustered with a water nobody declared\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400\n"
    "O  3.000  0.000  0.000\n"
    "H  3.500  0.800  0.000\n"
    "H  3.500 -0.800  0.000"
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_USER_ID = 50_612


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="ts_composition_tests"))
        session.flush()
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(*, ts_xyz: str = _XYZ_TS, ts_charge: int = 0) -> dict:
    return {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("h", "[H]", 2, _XYZ_H),
            _species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": ts_charge,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": ts_xyz},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                }
            ],
            "label": "ch3+h->ch4 TS",
        },
    }


def _upload(session: Session, payload: dict) -> dict:
    return persist_computed_reaction_upload(
        session,
        ComputedReactionUploadRequest(**payload),
        created_by=_USER_ID,
    )


# ---------------------------------------------------------------------------


def test_a_saddle_point_with_its_reaction_atoms_is_accepted(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        result = _upload(session, _bundle())
    assert result["transition_state_entry_id"] is not None


def test_a_saddle_point_missing_an_atom_is_refused(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, _bundle(ts_xyz=_XYZ_TS_MISSING_AN_ATOM))
    message = str(excinfo.value)
    assert "transition_state_composition_mismatch" in message
    assert "is CH3, but the reaction it sits in is CH4" in message


def test_a_saddle_point_carrying_an_undeclared_spectator_is_refused(
    db_engine,
) -> None:
    """Extra atoms are a *different* reaction, not a richer description of this one.

    The message points at the fix that keeps the science: declare the spectator
    as a participant, so the record says what was actually computed.
    """
    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(
                session, _bundle(ts_xyz=_XYZ_TS_WITH_AN_UNDECLARED_SPECTATOR)
            )
    message = str(excinfo.value)
    assert "is CH6O, but the reaction it sits in is CH4" in message
    assert "declare them as participants" in message


def test_a_saddle_point_at_the_wrong_charge_is_refused(db_engine) -> None:
    """Charge is conserved along a reaction coordinate."""
    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, _bundle(ts_charge=1))
    message = str(excinfo.value)
    assert "transition_state_charge_mismatch" in message
    assert "carries charge +1" in message
    assert "reactants total +0" in message


# ---------------------------------------------------------------------------
# Capitalisation is not chemistry
# ---------------------------------------------------------------------------


def _chlorine_bundle(*, ts_xyz: str) -> dict:
    """``CH3 + Cl -> CH3Cl``, so a two-letter element symbol is in play."""
    return {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("cl", "[Cl]", 2, _XYZ_CL),
            _species("ch3cl", "CCl", 1, _XYZ_CH3CL),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "cl"],
        "product_keys": ["ch3cl"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": ts_xyz},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -400.0,
                }
            ],
            "label": "ch3+cl->ch3cl TS",
        },
    }


def test_a_saddle_point_whose_xyz_shouts_its_elements_is_accepted(
    db_engine,
) -> None:
    """``CL`` and ``c`` are ``Cl`` and ``C``, and this check must know it.

    ``geometry_atom.element`` holds the depositor's XYZ symbol verbatim, while
    the reactant side of the comparison comes from RDKit's title-case
    ``GetSymbol()``. Comparing the two raw makes a correct saddle point read as
    ``CCLHHH`` against a reaction of ``CH3Cl`` and refuses it — rejecting
    correct chemistry over capitalisation, which ADR 0008 disqualifies a
    blocking check from doing.
    """
    with _isolated_session(db_engine) as session:
        result = _upload(session, _chlorine_bundle(ts_xyz=_XYZ_TS_MIXED_CASE))
    assert result["transition_state_entry_id"] is not None


def test_normalising_case_does_not_blunt_the_check(db_engine) -> None:
    """A mixed-case saddle point with the wrong *atoms* is still refused.

    Otherwise the fix for the capitalisation regression would have bought
    acceptance by making the rule stop firing.
    """
    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(
                session,
                _chlorine_bundle(ts_xyz=_XYZ_TS_MIXED_CASE_MISSING_AN_ATOM),
            )
    message = str(excinfo.value)
    assert "transition_state_composition_mismatch" in message
    # Reported in the normalised spelling and in Hill notation, so the formula
    # reads as chemistry rather than as whatever the file happened to shout.
    assert "is CH3, but the reaction it sits in is CH3Cl" in message


def test_multiplicity_is_deliberately_not_checked(db_engine) -> None:
    """Spin is not conserved the way charge and atoms are.

    Two doublets may react over a singlet or a triplet surface, and
    spin-forbidden reactions are real chemistry. A multiplicity rule would fire
    on correct novel results, which ADR 0008 disqualifies from blocking.
    """
    payload = _bundle()
    payload["transition_state"]["multiplicity"] = 4
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
    assert result["transition_state_entry_id"] is not None


# ---------------------------------------------------------------------------
# The rule holds on every transition-state deposit path, not just the bundle
# ---------------------------------------------------------------------------


def _standalone_ts_payload(xyz: str, *, charge: int = 0) -> dict:
    return {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}}
            ],
        },
        "charge": charge,
        "multiplicity": 2,
        "geometry": {"xyz_text": xyz},
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True},
        },
        "label": "standalone ch3+h->ch4 TS",
    }


def test_standalone_transition_state_upload_is_checked_too(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        entry = persist_transition_state_upload(
            session,
            TransitionStateUploadRequest(**_standalone_ts_payload(_XYZ_TS)),
            created_by=_USER_ID,
        )
        assert entry.id is not None

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            persist_transition_state_upload(
                session,
                TransitionStateUploadRequest(
                    **_standalone_ts_payload(_XYZ_TS_MISSING_AN_ATOM)
                ),
                created_by=_USER_ID,
            )
    assert "transition_state_composition_mismatch" in str(excinfo.value)
