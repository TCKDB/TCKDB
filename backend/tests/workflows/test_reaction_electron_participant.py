"""A reaction may name the electron it releases — and gains nothing by it.

``validate_reaction_charge_conservation`` refuses ``[OH-] + [H] -> H2O``. That
reaction is associative detachment, ``OH⁻ + H → H₂O + e⁻``: real, measured
gas-phase chemistry, sibling of ``H⁻ + H → H₂ + e⁻``, and balanced once its
electron is written down. Dissociative attachment, photoionization and
photodetachment are the same family.

Until this door existed the refusal was not merely inconvenient, it was
**out of tier**. Under ADR 0008 a check may block only when it asserts a
definition. Charge conservation is definitional only if the participant list
can be *complete*; with no way to name an electron, the rule was in fact
asserting "every participant was declared", which is an expectation about the
depositor. The error message pointed at ``molecule_kind: pseudo`` as the
escape, but nothing in the codebase can create a pseudo species —
``canonical_species_identity`` refused every non-``molecule`` kind — so the
advertised door did not exist and both pseudo exemptions were unreachable.

The load-bearing half of this file is the second half. ``pseudo`` participants
switch *both* conservation checks off entirely; if an electron did the same,
any depositor could disable mass balance on any reaction by adding one, which
is a larger hole than the one being closed. An electron is not "unknowable" —
it is exactly known: zero atoms, charge -1 — so it exempts nothing, and the
tests below pin that from both directions.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.identity import ELECTRON_SMILES

from app.chemistry.species import ELECTRON_INCHI_KEY
from app.db.models.common import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.db.models.reaction import ChemReaction, ReactionParticipant
from app.db.models.species import Species, SpeciesEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.species_resolution import (
    assert_geometry_composition_matches_identity,
)
from app.workflows.reaction import persist_reaction_upload

#: The whole depositor-facing interface for a free electron. No database id;
#: the identity row is resolved server-side like every other participant's.
_ELECTRON = {
    "molecule_kind": "electron",
    "smiles": ELECTRON_SMILES,
    "charge": -1,
    "multiplicity": 2,
}
_HYDROXIDE = {"smiles": "[OH-]", "charge": -1, "multiplicity": 1}
_H_ATOM = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
_HYDRIDE = {"smiles": "[H-]", "charge": -1, "multiplicity": 1}
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
_METHANE = {"smiles": "C", "charge": 0, "multiplicity": 1}
_OXIDE = {"smiles": "[O-2]", "charge": -2, "multiplicity": 1}
_O_ATOM = {"smiles": "[O]", "charge": 0, "multiplicity": 3}


def _request(*, reactants: list[dict], products: list[dict]) -> ReactionUploadRequest:
    return ReactionUploadRequest(
        reversible=False,
        reactants=[{"species_entry": dict(item)} for item in reactants],
        products=[{"species_entry": dict(item)} for item in products],
    )


# ---------------------------------------------------------------------------
# The door is real
# ---------------------------------------------------------------------------


def test_associative_detachment_deposits(db_engine) -> None:
    """``OH- + H -> H2O + e-``. The reaction the refusal was really about.

    Atoms: OH2 on both sides, the electron contributing none. Charge: -1 on
    the left, ``0 + (-1)`` on the right. Both conservation checks run and both
    pass, which is the point — the electron did not switch either off, it made
    the second one true.
    """

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(reactants=[_HYDROXIDE, _H_ATOM], products=[_WATER, _ELECTRON]),
            )
            assert entry.id is not None


def test_the_same_reaction_without_its_electron_is_still_refused(db_engine) -> None:
    """The check did not go soft. Omit the electron and the deposit still fails.

    This is the pairing that makes the tier claim honest: the rule now refuses
    exactly what it says it refuses — a reaction whose two sides describe
    different numbers of electrons — rather than refusing a reaction whose
    participant list happened to be inexpressible.
    """

    with Session(db_engine) as session:
        with session.begin():
            with pytest.raises(ValueError) as excinfo:
                persist_reaction_upload(
                    session,
                    _request(reactants=[_HYDROXIDE, _H_ATOM], products=[_WATER]),
                )
    message = str(excinfo.value)
    assert "reaction_charge_not_conserved" in message
    # The message must name the escape that exists, not one that does not.
    assert '"molecule_kind": "electron"' in message
    assert "pseudo" not in message


def test_an_electron_may_be_a_reactant(db_engine) -> None:
    """Dissociative attachment: ``e- + H2 -> H- + H``.

    Charge -1 both sides; atoms H2 both sides. Nothing about the electron's
    handling depends on which side of the arrow it sits on.
    """

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(reactants=[_ELECTRON, _H2], products=[_HYDRIDE, _H_ATOM]),
            )
            assert entry.id is not None


def test_two_electrons_carry_a_stoichiometric_coefficient(db_engine) -> None:
    """``[O-2] -> O + 2 e-``. A two-electron process needs two electrons.

    Both electrons resolve to the same species row and are compressed into a
    coefficient of 2 by ``compress_species_stoichiometry`` before either check
    sees them, so the charge sum is ``0 + 2 x (-1) = -2`` and matches the
    oxide's -2. A per-participant comparison would read this as -1 and refuse
    correct chemistry.
    """

    with Session(db_engine) as session:
        with session.begin():
            entry = persist_reaction_upload(
                session,
                _request(reactants=[_OXIDE], products=[_O_ATOM, _ELECTRON, _ELECTRON]),
            )
            session.flush()
            reaction = session.get(ChemReaction, entry.reaction_id)
            electron_rows = [
                participant
                for participant in reaction.participants
                if participant.species.kind == MoleculeKind.electron
            ]
            assert len(electron_rows) == 1
            assert electron_rows[0].stoichiometry == 2


def test_the_electron_is_part_of_the_reaction_identity(db_engine) -> None:
    """It is a participant row, so it is inside the stoichiometry hash.

    ``A -> B`` and ``A -> B + e-`` are different reactions and must not dedupe
    onto one ``chem_reaction``. Carrying the electron in
    ``reaction_participant`` rather than as a scalar on the reaction is what
    makes that automatic.
    """

    with Session(db_engine) as session:
        with session.begin():
            with_electron = persist_reaction_upload(
                session,
                _request(reactants=[_OXIDE], products=[_O_ATOM, _ELECTRON, _ELECTRON]),
            )
            # Same heavy atoms, no electron declared, and charge-balanced on
            # its own terms so it is accepted for its own reasons.
            without_electron = persist_reaction_upload(
                session,
                _request(reactants=[_OXIDE], products=[_OXIDE]),
            )
            assert with_electron.reaction_id != without_electron.reaction_id


# ---------------------------------------------------------------------------
# The door is not a back door
# ---------------------------------------------------------------------------


def test_an_electron_does_not_switch_off_elemental_balance(db_engine) -> None:
    """The whole risk of this feature, pinned.

    ``OH- + H + CH4 -> H2O + e-`` has a carbon and four hydrogens that simply
    vanish. Its charge balances (-1 against -1), so charge conservation has
    nothing to say — and if the electron had been routed through the
    ``pseudo`` exemption, elemental balance would have had nothing to say
    either, and a depositor could disable mass balance on any reaction by
    adding an electron to it.
    """

    with Session(db_engine) as session:
        with session.begin():
            before = session.scalar(select(func.count()).select_from(ChemReaction))
            with pytest.raises(ValueError) as excinfo:
                persist_reaction_upload(
                    session,
                    _request(
                        reactants=[_HYDROXIDE, _H_ATOM, _METHANE],
                        products=[_WATER, _ELECTRON],
                    ),
                )
            assert "reaction_mass_balance_failed" in str(excinfo.value)
            after = session.scalar(select(func.count()).select_from(ChemReaction))
            assert after == before


def test_an_electron_does_not_switch_off_charge_conservation(db_engine) -> None:
    """``OH- + H -> H2O + 2 e-`` releases one electron too many.

    Atoms balance, so only the charge rule can catch it: -1 on the left
    against -2 on the right. Declaring an electron makes the charge sum
    expressible, never optional.
    """

    with Session(db_engine) as session:
        with session.begin():
            with pytest.raises(ValueError) as excinfo:
                persist_reaction_upload(
                    session,
                    _request(
                        reactants=[_HYDROXIDE, _H_ATOM],
                        products=[_WATER, _ELECTRON, _ELECTRON],
                    ),
                )
    message = str(excinfo.value)
    assert "reaction_charge_not_conserved" in message
    assert "reactants total charge -1" in message
    assert "products total -2" in message


def test_a_pseudo_participant_still_exempts_and_an_electron_still_does_not(
    db_engine,
) -> None:
    """The contrast, in one place, on the same unbalanced reaction.

    ``pseudo`` means "this participant is not atom-resolved, do not judge this
    reaction". ``electron`` means "this participant is exactly nothing, judge
    the reaction as usual". Two kinds, two consequences, deliberately not one.
    """

    unbalanced = {
        "reactants": [_HYDROXIDE, _H_ATOM, _METHANE],
        "products": [_WATER, _ELECTRON],
    }

    with Session(db_engine) as session:
        with session.begin():
            with pytest.raises(ValueError):
                persist_reaction_upload(session, _request(**unbalanced))

    with Session(db_engine) as session:
        with session.begin():
            # Same reaction, with a lumped construct standing in for the
            # electron. The composition is now declared unknowable, so nothing
            # is judged and the deposit goes through.
            lumped = Species(
                kind=MoleculeKind.pseudo,
                smiles="lumped_electron_sink_0001",
                inchi_key="ELECTRONCONTRAST0000000001",
                charge=-1,
                multiplicity=2,
                stereo_kind=StereoKind.achiral,
            )
            session.add(lumped)
            session.flush()
            lumped_entry = SpeciesEntry(
                species_id=lumped.id,
                kind=StationaryPointKind.minimum,
                electronic_state_kind=SpeciesEntryStateKind.ground,
            )
            session.add(lumped_entry)
            session.flush()

            entry = persist_reaction_upload(
                session,
                ReactionUploadRequest(
                    reversible=False,
                    reactants=[
                        {"species_entry": dict(item)}
                        for item in (_HYDROXIDE, _H_ATOM, _METHANE)
                    ],
                    products=[
                        {"species_entry": dict(_WATER)},
                        {"species_entry_id": lumped_entry.id},
                    ],
                ),
            )
            assert entry.id is not None


def test_a_geometry_deposited_under_an_electron_is_refused() -> None:
    """An electron has no atoms, so any structure contradicts it.

    Without this, ``molecule_kind: electron`` would be a second, quieter way to
    attach coordinates that no composition check would look at.
    """

    with pytest.raises(ValueError) as excinfo:
        assert_geometry_composition_matches_identity(
            SpeciesEntryIdentityPayload(**_ELECTRON),
            GeometryPayload(xyz_text="1\nnot an electron\nH 0.0 0.0 0.0"),
        )
    message = str(excinfo.value)
    assert "species_geometry_composition_mismatch" in message
    assert "a free electron has no atoms" in message


# ---------------------------------------------------------------------------
# The identity is pinned, not trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {**_ELECTRON, "charge": 0},
            "a neutral electron would silently satisfy any charge sum",
        ),
        (
            {**_ELECTRON, "multiplicity": 1},
            "an electron is a doublet",
        ),
        (
            {**_ELECTRON, "smiles": "C"},
            "an electron declared with a structure would hide that structure "
            "from elemental balance",
        ),
        (
            {**_ELECTRON, "molecule_kind": "molecule"},
            "the token would be handed to RDKit, which cannot parse it",
        ),
    ],
)
def test_an_electron_payload_may_not_contradict_itself(payload, reason) -> None:
    """``electron``, ``[e-]``, -1 and 2 are declared together or not at all."""

    with pytest.raises(ValidationError):
        SpeciesEntryIdentityPayload(**payload)


def test_every_electron_deposit_resolves_to_the_one_electron(db_engine) -> None:
    """There is one electron, so there is one row, with no molecular graph.

    ``smiles`` holds the reserved token and ``inchi_key`` a sentinel that
    cannot be mistaken for an InChIKey (a real one is 14-10-1 letters). Every
    graph-derived column of the entry is NULL, because there is no graph.
    """

    with Session(db_engine) as session:
        with session.begin():
            detachment = persist_reaction_upload(
                session,
                _request(reactants=[_HYDROXIDE, _H_ATOM], products=[_WATER, _ELECTRON]),
            )
            attachment = persist_reaction_upload(
                session,
                _request(reactants=[_ELECTRON, _H2], products=[_HYDRIDE, _H_ATOM]),
            )
            session.flush()

            electrons = list(
                session.scalars(
                    select(Species).where(Species.kind == MoleculeKind.electron)
                )
            )
            assert len(electrons) == 1
            electron = electrons[0]
            assert electron.smiles == ELECTRON_SMILES
            assert electron.inchi_key.strip() == ELECTRON_INCHI_KEY
            assert (electron.charge, electron.multiplicity) == (-1, 2)
            assert electron.stereo_kind == StereoKind.achiral

            entries = list(
                session.scalars(
                    select(SpeciesEntry).where(
                        SpeciesEntry.species_id == electron.id
                    )
                )
            )
            assert len(entries) == 1
            assert entries[0].mol is None
            assert entries[0].unmapped_smiles is None
            assert entries[0].isotope_key is None

            # It is a participant of both reactions, not a decoration on one.
            # Asserted per reaction rather than as a total: ``db_engine`` is
            # session-scoped and these tests commit, so other reactions in
            # this file share the one electron row.
            participating = set(
                session.scalars(
                    select(ReactionParticipant.reaction_id).where(
                        ReactionParticipant.species_id == electron.id
                    )
                )
            )
            assert {detachment.reaction_id, attachment.reaction_id} <= participating
