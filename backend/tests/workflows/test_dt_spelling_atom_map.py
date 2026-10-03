"""A ``D`` spelling and an ``H`` plus mass number map onto each other (#672).

``reaction_atom_map_pair`` carries ``element`` on both ends and
``ck_reaction_atom_map_pair_element_matches`` requires them equal, so how a
deposited hydrogen isotope is *stored* decides whether a correct map can be
written. If ``parse_xyz`` kept ``D`` in ``geometry_atom.element``, a reactant
spelled ``D`` mapped onto a saddle point spelled ``H`` (plus ``isotopes``) would
be refused as ``atom_map_element_not_conserved`` -- correct chemistry refused
over how one side spelled a hydrogen. Storing ``H`` with the mass number removes
that, in both directions, and these tests hold it there.

The reaction is ``CD3 + D -> CD4``, the deuterated twin of the bundle in
``test_reaction_atom_map``.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.db.models.geometry import GeometryAtom
from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair
from tests.workflows.test_reaction_atom_map import (
    _XYZ_CH3,
    _XYZ_CH4,
    _XYZ_TS,
    _complete_map,
    _isolated_session,
    _payload,
    _species,
    _upload,
)


def _as_d(xyz: str) -> str:
    """Spell every hydrogen ``D``, keeping the column layout."""
    return "\n".join(
        ("D" + line[1:]) if line.startswith("H ") else line for line in xyz.splitlines()
    )


def _h_indices(xyz: str) -> dict[int, int]:
    """``geometry.isotopes`` marking every hydrogen of an H-spelled block as mass 2."""
    atoms = xyz.splitlines()[2:]
    return {
        index: 2 for index, line in enumerate(atoms, start=1) if line.split()[0] == "H"
    }


_CD3 = "[2H][C]([2H])[2H]"
_CD4 = "[2H]C([2H])([2H])[2H]"
_D_ATOM = "[2H]"


def _deuterated_payload(*, reactants_spelled_d: bool) -> dict:
    """``CD3 + D -> CD4``; one side spelled ``D``, the other ``H`` + isotopes."""
    payload = _payload()
    species = [
        _species("ch3", _CD3, 2, _XYZ_CH3),
        _species("h", _D_ATOM, 2, "1\nH\nH 0.0 0.0 0.0"),
        _species("ch4", _CD4, 1, _XYZ_CH4),
    ]
    spelled_d_species = {"ch3": _XYZ_CH3, "h": "1\nH\nH 0.0 0.0 0.0", "ch4": _XYZ_CH4}
    for entry in species:
        key = entry["key"]
        xyz = spelled_d_species[key]
        geometry = entry["conformers"][0]["geometry"]
        # Reactants and product are spelled together so that "the participant
        # side" is one spelling; the saddle point takes the other.
        if reactants_spelled_d:
            geometry["xyz_text"] = _as_d(xyz)
        else:
            geometry["isotopes"] = _h_indices(xyz)
    payload["species"] = species

    ts_geometry = payload["transition_state"]["geometry"]
    if reactants_spelled_d:
        ts_geometry["isotopes"] = _h_indices(_XYZ_TS)
    else:
        ts_geometry["xyz_text"] = _as_d(_XYZ_TS)
    payload["atom_map"] = _complete_map()
    return payload


@pytest.mark.parametrize("reactants_spelled_d", [True, False])
def test_a_d_spelled_side_maps_onto_an_h_plus_isotopes_side(
    db_conn, reactants_spelled_d
) -> None:
    """Either direction persists, and both ends of every pair store ``H``."""
    payload = _deuterated_payload(reactants_spelled_d=reactants_spelled_d)

    with _isolated_session(db_conn) as session:
        result = _upload(session, copy.deepcopy(payload))
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map is not None
        pairs = session.scalars(
            select(ReactionAtomMapPair).where(
                ReactionAtomMapPair.atom_map_id == atom_map.id
            )
        ).all()
        assert len(pairs) == 10
        hydrogen_pairs = [p for p in pairs if p.element.strip() == "H"]
        assert hydrogen_pairs and all(p.ts_element.strip() == "H" for p in hydrogen_pairs)
        # No pair anywhere carries the nuclide symbol: that is what keeps the
        # element-match constraint satisfiable across the two spellings.
        assert not any(p.element.strip() in {"D", "T"} for p in pairs)
        assert not any(p.ts_element.strip() in {"D", "T"} for p in pairs)

        stored = session.scalars(
            select(GeometryAtom.element).where(
                GeometryAtom.geometry_id.in_({p.geometry_id for p in pairs})
            )
        ).all()
        assert not any(element.strip() in {"D", "T"} for element in stored)
