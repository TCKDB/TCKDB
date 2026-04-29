"""Chemistry / structure consistency invariants.

Protect the backend against silently accepting a geometry whose atom
count or connectivity is inconsistent with the molecular representation
it is being attached to, and document the current reaction-balance
policy so a future change to that policy fails loudly rather than
quietly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.chemistry.geometry import parse_xyz
from app.db.models.common import ValidationStatus
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.geometry_validation import validate_calculation_geometry
from app.workflows.reaction import persist_reaction_upload
from app.workflows.thermo import _assert_calculation_owned_by


# ---------------------------------------------------------------------------
# Invariant 1: geometry atom-count consistency
# ---------------------------------------------------------------------------


def test_geometry_parser_rejects_atom_count_header_mismatch() -> None:
    """The XYZ header must agree with the number of atom lines.

    This is the lowest-level structural invariant: a header that claims
    three atoms but supplies two is not a valid geometry, regardless of
    what representation it is being attached to. ``parse_xyz`` is the
    choke point used by every geometry upload, so pinning it here covers
    every workflow that accepts an XYZ payload.
    """
    bad_xyz = "3\n\nO 0.0 0.0 0.0\nH 0.7572 0.5860 0.0\n"
    with pytest.raises(ValueError, match="atom count does not match"):
        parse_xyz(GeometryPayload(xyz_text=bad_xyz))


def test_geometry_parser_accepts_well_formed_xyz() -> None:
    """Sanity-check twin: the inverse path must succeed."""
    good_xyz = "2\n\nO 0.0 0.0 0.0\nH 0.7572 0.5860 0.0\n"
    parsed = parse_xyz(GeometryPayload(xyz_text=good_xyz))
    assert parsed.natoms == 2
    assert len(parsed.atoms) == 2


def test_geometry_validation_flags_atom_count_mismatch_vs_smiles() -> None:
    """Output geometry whose atom count disagrees with the SMILES graph
    must be marked ``fail`` by ``validate_calculation_geometry``.

    This is the representation-consistency half of the atom-count
    invariant: the geometry parser guarantees internal XYZ consistency,
    and this validator guarantees consistency between the geometry and
    the species identity the calculation claims to represent.
    """
    # Water is 3 atoms; we supply only 2 atoms.
    wrong_count_atoms = (
        ("O", 0.0, 0.0, 0.0),
        ("H", 0.7572, 0.5860, 0.0),
    )
    result = validate_calculation_geometry(
        output_atoms=wrong_count_atoms,
        species_smiles="O",  # H2O (3 atoms with explicit hydrogens)
    )
    assert result.is_isomorphic is False
    assert result.validation_status == ValidationStatus.fail
    assert "not graph-isomorphic" in (result.validation_reason or "")


def test_geometry_validation_flags_wrong_element_composition() -> None:
    """Right atom count, wrong elements → graph mismatch → fail.

    A refactor that accidentally ignored element identity would pass
    atom-count checks but silently corrupt species/geometry linkage.
    """
    # H2S geometry with 3 atoms — matches water's atom count but not its graph.
    h2s_shaped_as_water = (
        ("S", 0.0, 0.0, 0.0),
        ("H", 1.336, 0.0, 0.0),
        ("H", -0.448, 1.259, 0.0),
    )
    result = validate_calculation_geometry(
        output_atoms=h2s_shaped_as_water,
        species_smiles="O",
    )
    assert result.validation_status == ValidationStatus.fail


# ---------------------------------------------------------------------------
# Invariant 2: strict elemental balance for ordinary reactions
# ---------------------------------------------------------------------------
#
# Policy (strict): ordinary reactions must be element-balanced across every
# reaction-creation seam, including reactions used in network / PDep
# workflows. Pseudo-species are the only first-pass exception. If this test
# starts passing under a permissive policy, the backend has silently drifted
# back to the old behavior — document the new policy explicitly before
# relaxing the rule.


_H_ATOM = SpeciesEntryIdentityPayload(smiles="[H]", charge=0, multiplicity=2)
_O_ATOM = SpeciesEntryIdentityPayload(smiles="[O]", charge=0, multiplicity=3)


def test_reaction_upload_rejects_elementally_imbalanced_ordinary_reaction(
    db_engine,
) -> None:
    """Upload-level enforcement: ``H -> O`` is not element-balanced and
    must be rejected by the shared reaction-resolution seam."""
    request = ReactionUploadRequest(
        reversible=True,
        reactants=[{"species_entry": _H_ATOM.model_dump()}],
        products=[{"species_entry": _O_ATOM.model_dump()}],
    )

    # Use an explicit rollback rather than ``session.begin()`` here: the
    # workflow inserts the H/O species rows before the balance check fires,
    # and a successful exit from ``pytest.raises`` would otherwise commit
    # those rows and pollute the test DB for downstream tests.
    with Session(db_engine) as session:
        transaction = session.begin()
        try:
            with pytest.raises(ValueError, match="not element-balanced"):
                persist_reaction_upload(session, request)
        finally:
            transaction.rollback()


def test_reaction_upload_requires_at_least_one_participant_per_side() -> None:
    """The one structural constraint the schema DOES enforce: each side
    of a reaction must have at least one participant. A reaction with
    zero reactants or zero products is nonsensical even without mass
    balance enforcement, so this is pinned as the floor of the policy."""
    with pytest.raises(ValidationError):
        ReactionUploadRequest(
            reversible=True,
            reactants=[],
            products=[{"species_entry": _H_ATOM.model_dump()}],
        )
    with pytest.raises(ValidationError):
        ReactionUploadRequest(
            reversible=True,
            reactants=[{"species_entry": _H_ATOM.model_dump()}],
            products=[],
        )


# ---------------------------------------------------------------------------
# Invariant 3: owner / attachment consistency
# ---------------------------------------------------------------------------


class _FakeCalc:
    """Minimal stand-in for a ``Calculation`` row for the guard test.

    The workflow's owner-consistency check only reads ``id`` and
    ``species_entry_id``; a full ORM row requires DB setup that adds no
    signal to this invariant.
    """

    def __init__(self, *, id: int, species_entry_id: int) -> None:
        self.id = id
        self.species_entry_id = species_entry_id


def test_thermo_workflow_rejects_calculation_owned_by_other_species() -> None:
    """``_assert_calculation_owned_by`` is the defensive guard that
    prevents a thermo upload from attaching a calculation belonging to
    a different ``species_entry``.

    A silent regression here (e.g. flipped equality, or the guard being
    dropped during refactor) would let scientifically meaningless
    provenance links into the DB while every CRUD test still passes.
    """
    calc = _FakeCalc(id=42, species_entry_id=7)

    # Same owner → no error.
    _assert_calculation_owned_by(
        calc, species_entry_id=7, context="same owner",  # type: ignore[arg-type]
    )

    # Different owner → raise.
    with pytest.raises(ValueError, match="different species entry"):
        _assert_calculation_owned_by(
            calc,
            species_entry_id=8,  # type: ignore[arg-type]
            context="cross-owner",
        )


def test_thermo_upload_schema_rejects_source_calc_key_with_no_declared_calc() -> None:
    """End-to-end, an applied correction whose ``source_calculation_key``
    names a calculation that was never declared must be rejected at
    schema time, before the DB ever sees a row.

    This ties the owner-consistency invariant into the upload surface:
    even if the workflow guard above were bypassed, the upload schema
    blocks dangling calculation references at the gate."""
    with pytest.raises(ValidationError, match="undefined calculation_key"):
        ThermoUploadRequest(
            species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
            scientific_origin="computed",
            h298_kj_mol=-241.8,
            calculations=[],
            source_calculations=[
                {"calculation_key": "missing", "role": "sp"},
            ],
        )
