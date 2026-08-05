"""Charge must be conserved across a reaction.

``validate_reaction_elemental_balance`` has always compared element totals and
has never mentioned charge, so ``[OH-] + [H] -> H2O`` — which loses an electron
on the way across — deposited with no error and no warning at all. It is
element-balanced and it is not a reaction.

The gap reached further than itself. ``validate_transition_state_composition``
checks a saddle point's charge against its *reactants*, so that rule was
anchored to a side which carried no conservation guarantee of its own.

Charge conservation is the same argument as elemental balance applied to the
other conserved quantity: electrons are neither created nor destroyed by
rearranging bonds. Definitional under ADR 0008, therefore blocking, with the
same stoichiometric-coefficient handling and the same pseudo-species exemption.

The accepting half of this file is the load-bearing half. Ionic chemistry is
real chemistry, and a check that refused it would be worse than no check at
all: a reaction may carry any net charge it likes, as long as both sides carry
the same one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.db.models.reaction import ChemReaction
from app.db.models.species import Species, SpeciesEntry
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.workflows.reaction import persist_reaction_upload

#: ``smiles`` / ``charge`` / ``multiplicity`` for the ions and molecules below.
_HYDROXIDE = {"smiles": "[OH-]", "charge": -1, "multiplicity": 1}
_PROTON = {"smiles": "[H+]", "charge": 1, "multiplicity": 1}
_H_ATOM = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
_OXIDE = {"smiles": "[O-2]", "charge": -2, "multiplicity": 1}
_O_ATOM = {"smiles": "[O]", "charge": 0, "multiplicity": 3}
_METHANE = {"smiles": "C", "charge": 0, "multiplicity": 1}
_METHANIDE = {"smiles": "[CH3-]", "charge": -1, "multiplicity": 1}


def _request(*, reactants: list[dict], products: list[dict]) -> ReactionUploadRequest:
    return ReactionUploadRequest(
        reversible=False,
        reactants=[{"species_entry": dict(item)} for item in reactants],
        products=[{"species_entry": dict(item)} for item in products],
    )


# ---------------------------------------------------------------------------
# It fires
# ---------------------------------------------------------------------------


def test_a_reaction_that_loses_an_electron_is_refused(db_engine) -> None:
    """``[OH-] + [H] -> H2O``: element-balanced (OH2 both sides), charge -1 -> 0.

    The exact deposit that used to succeed silently.
    """

    with Session(db_engine) as session:
        with session.begin():
            before = session.scalar(select(func.count()).select_from(ChemReaction))
            with pytest.raises(ValueError) as excinfo:
                persist_reaction_upload(
                    session,
                    _request(reactants=[_HYDROXIDE, _H_ATOM], products=[_WATER]),
                )
            message = str(excinfo.value)
            assert "reaction_charge_not_conserved" in message
            assert "reactants total charge -1" in message
            assert "products total +0" in message
            after = session.scalar(select(func.count()).select_from(ChemReaction))
            assert after == before


def test_stoichiometric_coefficients_are_multiplied_in(db_engine) -> None:
    """``2 [OH-] -> H2O + O``: two hydroxides carry -2, not -1.

    A per-participant comparison that ignored the coefficient would read this
    as -1 against 0 and still refuse it, for the wrong reason and with the
    wrong number in the message. The repeated participant is compressed to a
    coefficient by ``compress_species_stoichiometry`` before the check ever
    sees it, exactly as elemental balance receives it.
    """

    with Session(db_engine) as session:
        with session.begin():
            with pytest.raises(ValueError) as excinfo:
                persist_reaction_upload(
                    session,
                    _request(
                        reactants=[_HYDROXIDE, _HYDROXIDE],
                        products=[_WATER, _O_ATOM],
                    ),
                )
            assert "reactants total charge -2" in str(excinfo.value)


# ---------------------------------------------------------------------------
# It does not fire on correct chemistry
# ---------------------------------------------------------------------------


def test_a_neutral_reaction_is_accepted(db_engine) -> None:
    """The overwhelming majority of the database: 0 on both sides."""

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(reactants=[_H_ATOM, _H_ATOM], products=[
                    {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
                ]),
            )
            assert entry.id is not None


def test_ion_recombination_to_a_neutral_product_is_accepted(db_engine) -> None:
    """``[OH-] + [H+] -> H2O``. Charge -1 and +1 sum to the product's 0."""

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(reactants=[_HYDROXIDE, _PROTON], products=[_WATER]),
            )
            assert entry.id is not None


def test_a_reaction_with_a_conserved_nonzero_charge_is_accepted(db_engine) -> None:
    """``[OH-] + CH4 -> [CH3-] + H2O``, a proton transfer that stays at -1.

    Conservation, not neutrality, is the rule. Requiring both sides to be
    neutral would refuse every ion-molecule reaction in the literature, which
    is the false-positive ADR 0008 disqualifies a blocking check for.
    """

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(
                    reactants=[_HYDROXIDE, _METHANE],
                    products=[_METHANIDE, _WATER],
                ),
            )
            assert entry.id is not None


def test_a_reaction_conserving_a_charge_of_minus_two_is_accepted(db_engine) -> None:
    """``2 [OH-] -> H2O + [O-2]``. -2 on both sides, with a coefficient of 2."""

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(
                    reactants=[_HYDROXIDE, _HYDROXIDE],
                    products=[_WATER, _OXIDE],
                ),
            )
            assert entry.id is not None


def test_a_pseudo_species_participant_is_exempt(db_engine) -> None:
    """Mirrors the elemental-balance exemption, and shares its implementation.

    A lumped or phenomenological construct is not atom-resolved, so neither its
    elements nor its charge is a quantity a conservation law applies to.
    """

    with Session(db_engine) as session:
        with session.begin():
            # An anion on one side and a neutral pseudo-species on the other:
            # -1 against 0, which would be refused were the reaction judged.
            # The ordinary participant's smiles is a unique placeholder — the
            # check reads ``Species.charge`` and never parses it, and the
            # exemption returns before even that.
            ordinary = Species(
                kind=MoleculeKind.molecule,
                smiles="pseudo-charged-anion-0001",
                inchi_key="CHARGECONSERVE00000000001",
                charge=-1,
                multiplicity=1,
                stereo_kind=StereoKind.achiral,
            )
            pseudo = Species(
                kind=MoleculeKind.pseudo,
                smiles="lumped_charge_sink",
                inchi_key="CHARGECONSERVE00000000002",
                charge=0,
                multiplicity=1,
                stereo_kind=StereoKind.achiral,
            )
            session.add_all([ordinary, pseudo])
            session.flush()
            entries = []
            for species in (ordinary, pseudo):
                entry = SpeciesEntry(
                    species_id=species.id,
                    kind=StationaryPointKind.minimum,
                    electronic_state_kind=SpeciesEntryStateKind.ground,
                )
                session.add(entry)
                entries.append(entry)
            session.flush()

            reaction_entry = persist_reaction_upload(
                session,
                ReactionUploadRequest(
                    reversible=False,
                    reactants=[{"species_entry_id": entries[0].id}],
                    products=[{"species_entry_id": entries[1].id}],
                ),
            )
            assert reaction_entry.id is not None
