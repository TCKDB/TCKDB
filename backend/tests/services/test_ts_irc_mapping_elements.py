"""Element checks on IRC participant mappings.

``transition_state_validation_evidence.reactant_participant_mapping`` and
``product_participant_mapping`` say which saddle-point atom indices become which
declared participant. ``validate_ts_evidence_set`` checks the *shape* of that
claim — the keys name every declared participant, the indices partition the TS
atoms exactly once — and for a long time nothing checked what the atoms **are**.

A well-formed partition of the wrong atoms therefore passed. The case these
tests are built around is real: a nine-atom ``C C O O H H H H H`` saddle point
for ``ethylperoxy -> ethene + HO2`` was written with ``product:1 = [1..6]``,
handing ethene two oxygens and HO2 three hydrogens, under a comment that
correctly read "C2H4 (six atoms)". The partition was valid; the chemistry was
not. The identical claim written as a ``reaction_atom_map`` was refused the
whole time, by the composite foreign key into ``geometry_atom``.

The false-positive tests are as load-bearing as the refusal ones: ADR 0008 lets
a check block only when no correct calculation could produce the record it
refuses, so a blocking rule that fires on a pseudo participant, an isotopologue
or a deposit that simply declined to map its atoms would be the wrong rule.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)

from app.db.models.common import MoleculeKind
from app.db.models.species import SpeciesEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.reaction_resolution import (
    validate_ts_evidence_participant_composition,
)
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)

# The saddle point at the centre of this file: C2H5O2, listed 1 C, 2 C, 3 O,
# 4 O, 5-9 H. It is the ethylperoxy -> ethene + HO2 concerted elimination TS.
_XYZ_ELIM_TS = (
    "9\nTS for C2H5OO -> C2H4 + HO2\n"
    "C  0.00  0.00  0.00\n"
    "C  1.42  0.00  0.00\n"
    "O  2.14  1.24  0.00\n"
    "O  3.42  1.06  0.00\n"
    "H -0.32  1.03  0.00\n"
    "H -0.42 -0.55  0.86\n"
    "H -0.42 -0.55 -0.86\n"
    "H  1.92 -0.61  0.78\n"
    "H  2.71 -0.74 -0.42"
)

#: The correct partition. C2H4 takes both carbons and four hydrogens; HO2 takes
#: both oxygens and the fifth hydrogen.
_CORRECT_PRODUCTS = {
    "product:1": [1, 2, 5, 6, 7, 8],  # C2H4: C, C, H, H, H, H
    "product:2": [3, 4, 9],  # HO2: O, O, H
}

#: The partition that used to pass. Nine atoms, each claimed exactly once, and
#: ethene is made of two carbons, two oxygens and two hydrogens.
_ETHENE_MADE_OF_OXYGENS = {
    "product:1": [1, 2, 3, 4, 5, 6],  # "C2H4" -- actually C2O2H2
    "product:2": [7, 8, 9],  # "HO2"   -- actually H3
}

_WHOLE_SKELETON = {"reactant:1": list(range(1, 10))}


def _evidence(reactants: dict | None, products: dict | None, *, passed: bool = True):
    return [
        TransitionStateValidationEvidenceIn(
            kind="irc",
            passed=passed,
            rationale="IRC descends to C2H5OO one way and C2H4 + HO2 the other.",
            reactant_participant_mapping=reactants,
            product_participant_mapping=products,
        )
    ]


def _species_entry(session, species):
    """Get-or-create the default species entry for *species*.

    ``make_species`` deliberately returns a pre-existing row for an ordinary
    collider — the test database is session-scoped and other suites commit
    ethene and HO2 — but ``make_species_entry`` always inserts, so pairing the
    two directly trips ``uq_species_entry_species_id`` as soon as anything else
    in the run has committed the same species. This file only reads
    ``Species.smiles`` and ``Species.kind`` through the entry, so reusing an
    existing one is equivalent and order-independent.
    """
    existing = session.scalar(
        select(SpeciesEntry).where(SpeciesEntry.species_id == species.id).limit(1)
    )
    return existing if existing is not None else make_species_entry(session, species)


def _elimination_reaction(
    session,
    *,
    reactant_smiles: str = "CCO[O]",
    product_smiles: tuple[str, ...] = ("C=C", "O[O]"),
    product_kinds: tuple[MoleculeKind, ...] | None = None,
    product_multiplicities: tuple[int, ...] = (1, 2),
):
    """Build ``ethylperoxy -> ethene + HO2`` and return its reaction entry.

    Multiplicities are the real ones — ethylperoxy and HO2 are radicals — so
    ``make_species`` resolves onto the same identity another suite would have
    committed rather than manufacturing a near-duplicate.
    """
    kinds = product_kinds or tuple(MoleculeKind.molecule for _ in product_smiles)
    reactant = make_species(
        session,
        smiles=reactant_smiles,
        inchi_key=next_inchi_key("IRCR"),
        multiplicity=2,
    )
    products = [
        make_species(
            session,
            smiles=smiles,
            inchi_key=next_inchi_key("IRCP"),
            kind=kind,
            multiplicity=multiplicity,
        )
        for smiles, kind, multiplicity in zip(
            product_smiles, kinds, product_multiplicities, strict=True
        )
    ]
    chem = make_chem_reaction(session, reactants=[reactant], products=products)
    return make_reaction_entry(
        session,
        reaction=chem,
        reactant_entries=[_species_entry(session, reactant)],
        product_entries=[_species_entry(session, species) for species in products],
    )


def _geometry_id(session, xyz_text: str) -> int:
    return resolve_geometry_payload(session, GeometryPayload(xyz_text=xyz_text)).id


def _check(session, reaction_entry, geometry_id, evidence) -> None:
    validate_ts_evidence_participant_composition(
        session,
        evidence,
        reaction_entry_id=reaction_entry.id,
        transition_state_geometry_id=geometry_id,
        subject_label="ts_elim",
    )


# ---------------------------------------------------------------------------
# The rule fires
# ---------------------------------------------------------------------------


def test_ethene_made_of_two_oxygens_is_refused(db_session) -> None:
    """The headline case: a valid partition of the wrong atoms.

    Every one of the nine atoms is claimed exactly once, so the shape rule in
    ``validate_ts_evidence_set`` has nothing to say. What is wrong is that
    ``product:1`` is declared as ``C=C`` and made of C2O2H2.
    """
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    with pytest.raises(ValueError) as excinfo:
        _check(
            db_session,
            reaction_entry,
            geometry_id,
            _evidence(_WHOLE_SKELETON, _ETHENE_MADE_OF_OXYGENS),
        )

    message = str(excinfo.value)
    assert "transition_state_irc_mapping_element_mismatch" in message
    # The error has to name the formula it was handed and the formula it
    # expected, or a depositor cannot correct the mapping per atom -- which is
    # the mistake that created this gap in the first place.
    assert "C2H2O2" in message
    assert "C2H4" in message
    assert "product:1" in message


def test_the_reactant_leg_is_checked_too(db_session) -> None:
    """Both legs, not just the products."""
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    with pytest.raises(ValueError, match="reactant:1"):
        _check(
            db_session,
            reaction_entry,
            geometry_id,
            # The lone reactant is C2H5O2 and must take the whole skeleton;
            # eight atoms is a formula it does not have.
            _evidence({"reactant:1": list(range(1, 9))}, _CORRECT_PRODUCTS),
        )


def test_a_swap_between_two_participants_is_refused(db_session) -> None:
    """Right formulas on the side as a whole, wrong ones per participant.

    This is the case an aggregate composition check cannot reach:
    ``validate_transition_state_composition`` compares the saddle point against
    the reactant side in total, and the total is correct here. Only the
    per-participant rule sees that ethene has been handed HO2's atoms.
    """
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    with pytest.raises(ValueError, match="transition_state_irc_mapping_element_mismatch"):
        _check(
            db_session,
            reaction_entry,
            geometry_id,
            _evidence(
                _WHOLE_SKELETON,
                {
                    "product:1": [3, 4, 9, 5, 6, 7],  # O, O, H, H, H, H
                    "product:2": [1, 2, 8],  # C, C, H
                },
            ),
        )


# ---------------------------------------------------------------------------
# The rule does not fire on correct chemistry
# ---------------------------------------------------------------------------


def test_the_correct_partition_of_the_same_saddle_point_passes(db_session) -> None:
    """The other half of the headline case, and the one that matters most."""
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    _check(
        db_session,
        reaction_entry,
        geometry_id,
        _evidence(_WHOLE_SKELETON, _CORRECT_PRODUCTS),
    )


def test_evidence_that_did_not_pass_is_not_contradicted(db_session) -> None:
    """A record that does not claim to be passing evidence claims nothing.

    An IRC that ran and *failed* is worth storing — it is the record of a
    saddle point that does not connect what its depositor hoped — and its
    mapping is a description of what was found, not an assertion about it.
    """
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    _check(
        db_session,
        reaction_entry,
        geometry_id,
        _evidence(_WHOLE_SKELETON, _ETHENE_MADE_OF_OXYGENS, passed=False),
    )


def test_evidence_without_mappings_is_not_checked(db_session) -> None:
    """Mappings are optional on every path; absence is never contradiction.

    This is also what a barrierless association deposits when it has run an
    IRC-like scan but has no atom partition to declare, and it is the escape
    hatch the blocking tier depends on.
    """
    reaction_entry = _elimination_reaction(db_session)
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    _check(db_session, reaction_entry, geometry_id, _evidence(None, None))


def test_absent_geometry_is_not_a_contradiction(db_session) -> None:
    """A barrierless path has no saddle point, so there is nothing to compare.

    Nothing about the mapping can be judged without the geometry its indices
    count into, and refusing on that basis would refuse a deposit for what it
    does not contain rather than for what it says.
    """
    reaction_entry = _elimination_reaction(db_session)

    validate_ts_evidence_participant_composition(
        db_session,
        _evidence(_WHOLE_SKELETON, _ETHENE_MADE_OF_OXYGENS),
        reaction_entry_id=reaction_entry.id,
        transition_state_geometry_id=None,
        subject_label="barrierless",
    )


def test_a_pseudo_participant_is_skipped_without_exempting_its_siblings(
    db_session,
) -> None:
    """A lumped construct has no atom-resolved composition to compare against.

    It is skipped *individually* rather than exempting the whole record: the
    other participants' formulas are still perfectly well-defined, and dropping
    them would discard a guarantee for no reason -- the same reasoning that
    keeps ``validate_transition_state_composition``'s exemption scoped to the
    reactant side.
    """
    # The lumped participant gets a SMILES no other suite commits. ``kind`` is
    # not part of ``Species.public_ref``, which is content-addressed on
    # (smiles, charge, multiplicity), so a *pseudo* row reusing an ordinary
    # molecule's SMILES cannot be inserted alongside it at all.
    reaction_entry = _elimination_reaction(
        db_session,
        product_smiles=(unique_smiles(), "O[O]"),
        product_kinds=(MoleculeKind.pseudo, MoleculeKind.molecule),
    )
    geometry_id = _geometry_id(db_session, _XYZ_ELIM_TS)

    # ``product:1`` is the lumped construct, and is handed two oxygens that are
    # nothing like the alkane its SMILES names. A pseudo species has no
    # atom-resolved composition to contradict, so this is accepted -- while
    # ``product:2`` beside it is given its own correct O, O, H.
    _check(
        db_session,
        reaction_entry,
        geometry_id,
        _evidence(_WHOLE_SKELETON, {"product:1": [3, 4], "product:2": [3, 4, 9]}),
    )

    # The exemption stops at the pseudo participant. ``product:2`` is an
    # ordinary molecule, is declared ``O[O]``, and has just been handed O, H, H.
    with pytest.raises(ValueError, match="product:2"):
        _check(
            db_session,
            reaction_entry,
            geometry_id,
            _evidence(_WHOLE_SKELETON, {"product:1": [3, 4], "product:2": [4, 8, 9]}),
        )


def test_an_isotopologue_written_with_D_is_not_a_mismatch(db_session) -> None:
    """``D`` in an XYZ is hydrogen, and a blocking check may not say otherwise.

    Gaussian, ORCA, Molpro and CFOUR all emit or accept ``D``/``T`` for
    hydrogen's isotopes, and ``geometry_atom.element`` stores them verbatim by
    design. The SMILES side spells the same nucleus ``[2H]``. Both sides are
    counted through ``resolve_element_symbol``, so the two agree; comparing raw
    symbols would read a perfectly ordinary deuterated saddle point as
    containing an element its participants never mention.
    """
    deuterated_ts = (
        "9\nd5-TS for C2D5OO -> C2D4 + DO2\n"
        "C  0.00  0.00  0.00\n"
        "C  1.42  0.00  0.00\n"
        "O  2.14  1.24  0.00\n"
        "O  3.42  1.06  0.00\n"
        "D -0.32  1.03  0.00\n"
        "D -0.42 -0.55  0.86\n"
        "D -0.42 -0.55 -0.86\n"
        "D  1.92 -0.61  0.78\n"
        "D  2.71 -0.74 -0.42"
    )
    reaction_entry = _elimination_reaction(
        db_session,
        reactant_smiles="[2H]C([2H])([2H])C([2H])([2H])O[O]",
        product_smiles=("[2H]C([2H])=C([2H])[2H]", "[2H]O[O]"),
    )
    geometry_id = _geometry_id(db_session, deuterated_ts)

    _check(
        db_session,
        reaction_entry,
        geometry_id,
        _evidence(_WHOLE_SKELETON, _CORRECT_PRODUCTS),
    )
