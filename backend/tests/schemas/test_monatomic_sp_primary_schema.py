"""Offline pins for the one-atom ``sp`` primary rule (#610).

The rule is ``require_opt_primary_unless_monatomic``, reached through the
species conformer, the reaction conformer and the pressure-dependent network's
conformer (#615). Every way it could be too wide
is a case here: a geometry that cannot be counted, two atoms, and every
calculation type other than ``opt`` and ``sp``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.workflows.computed_reaction_upload import ConformerIn
from tckdb_schemas.workflows.computed_species_upload import ConformerInBundle

from app.db.models.common import CalculationType
from app.schemas.workflows.network_pdep_upload import ConformerIn as PDepConformerIn

_ATOM = "1\nH atom\nH 0.0 0.0 0.0"
_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_SOFTWARE = {"name": "Gaussian", "version": "16"}


def _species_conformer(calc_type: str, xyz: str) -> ConformerInBundle:
    return ConformerInBundle.model_validate(
        {
            "key": "c0",
            "geometry": {"xyz_text": xyz},
            "primary_calculation": {
                "key": "p0",
                "type": calc_type,
                "level_of_theory": _LOT,
                "software_release": _SOFTWARE,
            },
        }
    )


def _reaction_conformer(calc_type: str, xyz: str) -> ConformerIn:
    return ConformerIn.model_validate(
        {
            "key": "c0",
            "geometry": {"key": "g0", "xyz_text": xyz},
            "calculation": {
                "key": "p0",
                "type": calc_type,
                "level_of_theory": _LOT,
                "software_release": _SOFTWARE,
            },
        }
    )


def _pdep_conformer(calc_type: str, xyz: str) -> PDepConformerIn:
    return PDepConformerIn.model_validate(
        {
            "key": "c0",
            "geometry": {"key": "g0", "xyz_text": xyz},
            "calculation": {
                "key": "p0",
                "type": calc_type,
                "level_of_theory": _LOT,
                "software_release": _SOFTWARE,
            },
        }
    )


_BUILDERS = pytest.mark.parametrize(
    "build",
    [_species_conformer, _reaction_conformer, _pdep_conformer],
    ids=["species", "reaction", "pdep"],
)
_OTHER_TYPES = [t.value for t in CalculationType if t not in (CalculationType.opt, CalculationType.sp)]


@_BUILDERS
@pytest.mark.parametrize("xyz", [_ATOM, _H2])
def test_opt_is_accepted_for_any_geometry(build, xyz):
    build("opt", xyz)


@_BUILDERS
def test_sp_is_accepted_for_one_atom(build):
    build("sp", _ATOM)


@_BUILDERS
def test_sp_is_refused_for_two_atoms_and_names_the_count(build):
    with pytest.raises(ValidationError) as exc:
        build("sp", _H2)
    assert "this geometry has 2 atoms" in str(exc.value)


@_BUILDERS
@pytest.mark.parametrize(
    "xyz",
    ["not an xyz", "1\nH", "H 0 0 0", "2\nH\nH 0 0 0"],
    ids=["prose", "no-atom-line", "no-header", "count-mismatch"],
)
@pytest.mark.parametrize("calc_type", ["sp", "opt"])
def test_an_uncountable_geometry_gets_no_exemption(build, xyz, calc_type):
    """An atom that cannot be proven a single atom is not one.

    ``opt`` is still accepted (the rule never looked at its geometry); ``sp``
    is refused, and the refusal does not claim a count it does not have.
    """
    if calc_type == "opt":
        build("opt", xyz)
        return
    with pytest.raises(ValidationError) as exc:
        build("sp", xyz)
    assert "exactly one atom" in str(exc.value)
    assert "atoms" not in str(exc.value).replace("exactly one atom", "")


@_BUILDERS
@pytest.mark.parametrize("calc_type", _OTHER_TYPES)
@pytest.mark.parametrize("xyz", [_ATOM, _H2], ids=["one-atom", "two-atoms"])
def test_no_other_type_is_ever_exempt(build, calc_type, xyz):
    assert _OTHER_TYPES, "the parametrization must not be empty"
    with pytest.raises(ValidationError) as exc:
        build(calc_type, xyz)
    assert f"got '{calc_type}'" in str(exc.value)


@pytest.mark.parametrize("calc_type", ["sp", "freq", "scan", "irc"])
@pytest.mark.parametrize("xyz", [_ATOM, _H2], ids=["one-atom", "two-atoms"])
def test_pdep_transition_state_primary_still_requires_opt(calc_type, xyz):
    """A saddle point is never an atom: the exemption does not reach it."""
    from app.schemas.workflows.network_pdep_upload import TransitionStateIn

    with pytest.raises(ValidationError, match="must be type 'opt'"):
        TransitionStateIn.model_validate(
            {
                "key": "ts0",
                "micro_reaction_key": "r0",
                "charge": 0,
                "multiplicity": 2,
                "geometry": {"key": "g0", "xyz_text": xyz},
                "calculation": {
                    "key": "p0",
                    "type": calc_type,
                    "level_of_theory": _LOT,
                    "software_release": _SOFTWARE,
                },
            }
        )
