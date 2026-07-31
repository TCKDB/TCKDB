"""Atom-resolved isotope identity (Stage 2 blocker B10).

Before this feature the only isotope-aware column in the schema was
``species_entry.isotopologue_label`` — a free-text ``VARCHAR(64)`` inside
``uq_species_entry_species_id``. Any two arbitrary strings forked a
species identity, and no atom-resolved isotope data existed anywhere, so
isotope-specific frequencies, rotational constants, ZPE, Hessian reuse
and kinetic isotope effects were unreconstructible.

Identity is now derived, never uploaded: the entry's ``isotope_key`` is
the canonical SMILES of the isotope-labelled identity molecule, and
``NULL`` is the all-standard key.

These tests roll back their transaction (rather than the commit-on-exit
pattern) so they never pollute the shared session-scoped test DB.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.geometry import parse_xyz
from app.chemistry.species import (
    canonical_isotope_key,
    canonical_species_identity,
    isotope_substitutions,
)
from app.db.models.geometry import GeometryAtom
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.species_resolution import resolve_species_entry

# Methanol, staggered-ish; only the labelling ever differs between cases.
METHANOL_XYZ = """6

C  -0.047131  0.664389  0.000000
O  -0.047131 -0.758551  0.000000
H  -1.092995  0.969785  0.000000
H   0.878534 -1.048458  0.000000
H   0.437145  1.080376  0.891772
H   0.437145  1.080376 -0.891772
"""


def _identity(smiles: str, *, charge: int = 0, multiplicity: int = 1):
    return SpeciesEntryIdentityPayload(
        smiles=smiles, charge=charge, multiplicity=multiplicity
    )


@contextmanager
def _rolled_back_session(db_engine):
    with Session(db_engine) as session:
        trans = session.begin()
        try:
            yield session
        finally:
            trans.rollback()


# ---------------------------------------------------------------------------
# Chemistry layer: the canonical isotope key
# ---------------------------------------------------------------------------


class TestCanonicalIsotopeKey:
    def test_all_standard_species_has_a_null_key(self) -> None:
        """The all-standard key is ``None`` — not an RDKit-dependent string.

        This is what makes the key stable: every species entry that existed
        before atom-resolved isotopes keeps exactly the identity it had.
        """

        assert canonical_isotope_key("CO") is None
        assert canonical_isotope_key("[CH2]") is None

    def test_explicit_standard_isotope_normalizes_to_the_null_key(self) -> None:
        """``[1H]``/``[12C]`` state the default and must not fork identity."""

        assert canonical_isotope_key("[1H]CO") is None
        assert canonical_isotope_key("[12CH3]O") is None
        assert canonical_isotope_key("[1H]O[12CH3]") is None

    def test_deuteration_produces_a_key(self) -> None:
        assert canonical_isotope_key("[2H]CO") == "[2H]CO"

    def test_key_is_independent_of_uploaded_atom_order(self) -> None:
        """Same molecule written two ways must give one key, or it forks."""

        assert canonical_isotope_key("OC[2H]") == canonical_isotope_key("[2H]CO")

    def test_isotopomers_get_different_keys(self) -> None:
        """CH2D-OH and CH3-OD are the same isotopologue, different molecules.

        They have different vibrational frequencies, rotational constants
        and ZPE, so an isotopologue-level (formula-counting) key would be
        scientifically wrong here. The key is atom-resolved.
        """

        assert canonical_isotope_key("[2H]CO") != canonical_isotope_key("[2H]OC")

    def test_unknown_isotope_is_rejected_not_guessed(self) -> None:
        with pytest.raises(ValueError, match="not a known isotope"):
            canonical_isotope_key("[12H]CO")

    def test_species_level_identity_is_isotope_blind(self) -> None:
        """Isotopologues share one molecular graph, so they share a species.

        The InChIKey stays isotope-blind too, so an InChIKey lookup finds
        every isotopologue of a compound rather than only the ordinary one.
        """

        plain = canonical_species_identity(_identity("CO"))
        heavy = canonical_species_identity(_identity("[2H]C([2H])([2H])O"))
        assert plain == heavy

    def test_isotope_substitution_multiset(self) -> None:
        assert isotope_substitutions("CO") == {}
        assert isotope_substitutions("[2H]C([2H])([2H])O") == {("H", 2): 3}
        assert isotope_substitutions("[13CH3]O[2H]") == {("C", 13): 1, ("H", 2): 1}


# ---------------------------------------------------------------------------
# Resolution layer: dedupe vs fork
# ---------------------------------------------------------------------------


def test_deposits_differing_only_in_deuteration_are_distinct_entries(db_engine) -> None:
    """The headline requirement: deuteration forks the entry, not the species."""

    with _rolled_back_session(db_engine) as session:
        light = resolve_species_entry(session, _identity("CO"))
        heavy = resolve_species_entry(session, _identity("[2H]C([2H])([2H])O"))
        session.flush()

        assert light.id != heavy.id
        # One shared species: same graph, same PES, different nuclear masses.
        assert light.species_id == heavy.species_id
        assert light.isotope_key is None
        assert heavy.isotope_key == "[2H]C([2H])([2H])O"


def test_identical_all_standard_deposits_dedupe_to_one_entry(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        first = resolve_species_entry(session, _identity("CO"))
        session.flush()
        second = resolve_species_entry(session, _identity("OC"))
        session.flush()

        assert first.id == second.id
        assert first.isotope_key is None


def test_explicitly_all_standard_deposit_dedupes_with_an_unlabelled_one(
    db_engine,
) -> None:
    """An existing entry must keep its identity when someone spells out [1H].

    This is the migration-safety property expressed at the service layer:
    the all-standard key is ``None`` however it is written, so a
    pre-existing row is never forked by a more verbose re-deposit.
    """

    with _rolled_back_session(db_engine) as session:
        plain = resolve_species_entry(session, _identity("CO"))
        session.flush()
        spelled_out = resolve_species_entry(session, _identity("[1H]C([1H])([1H])O"))
        session.flush()

        assert plain.id == spelled_out.id
        assert spelled_out.isotope_key is None


def test_isotopomers_resolve_to_distinct_entries(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        c_deuterated = resolve_species_entry(session, _identity("[2H]CO"))
        o_deuterated = resolve_species_entry(session, _identity("[2H]OC"))
        session.flush()

        assert c_deuterated.id != o_deuterated.id
        assert c_deuterated.species_id == o_deuterated.species_id


def test_isotope_key_is_never_accepted_from_an_upload_payload() -> None:
    """Derived keys may not be supplied by a client (repo invariant)."""

    with pytest.raises(ValueError):
        SpeciesEntryIdentityPayload(
            smiles="CO", charge=0, multiplicity=1, isotope_key="[2H]CO"
        )
    with pytest.raises(ValueError):
        SpeciesEntryIdentityPayload(
            smiles="CO", charge=0, multiplicity=1, isotopologue_label="d3"
        )


# ---------------------------------------------------------------------------
# Geometry layer: per-atom nuclides
# ---------------------------------------------------------------------------


def test_geometry_hash_is_unchanged_when_no_isotopes_are_declared() -> None:
    """Existing geometries must not be rehashed by this feature."""

    import hashlib

    parsed = parse_xyz(GeometryPayload(xyz_text=METHANOL_XYZ))
    assert parsed.hash_text == parsed.canonical_xyz_text
    assert (
        hashlib.sha256(parsed.hash_text.encode("utf-8")).hexdigest()
        == hashlib.sha256(parsed.canonical_xyz_text.encode("utf-8")).hexdigest()
    )


def test_explicit_standard_isotope_does_not_change_the_geometry_hash() -> None:
    plain = parse_xyz(GeometryPayload(xyz_text=METHANOL_XYZ))
    spelled_out = parse_xyz(
        GeometryPayload(xyz_text=METHANOL_XYZ, isotopes={1: 12, 3: 1})
    )
    assert spelled_out.isotopes == ()
    assert spelled_out.hash_text == plain.hash_text


def test_isotopically_labelled_geometry_is_a_distinct_row(db_engine) -> None:
    """Identical coordinates + different nuclides = different physics."""

    with _rolled_back_session(db_engine) as session:
        plain = resolve_geometry_payload(
            session, GeometryPayload(xyz_text=METHANOL_XYZ)
        )
        session.flush()
        labelled = resolve_geometry_payload(
            session,
            GeometryPayload(xyz_text=METHANOL_XYZ, isotopes={3: 2, 5: 2, 6: 2}),
        )
        session.flush()

        assert plain.id != labelled.id
        assert plain.geom_hash != labelled.geom_hash

        masses = {
            atom.atom_index: atom.isotope_mass_number
            for atom in session.scalars(
                select(GeometryAtom).where(GeometryAtom.geometry_id == labelled.id)
            )
        }
        assert masses == {1: None, 2: None, 3: 2, 4: None, 5: 2, 6: 2}

        plain_masses = session.scalars(
            select(GeometryAtom.isotope_mass_number).where(
                GeometryAtom.geometry_id == plain.id
            )
        ).all()
        assert set(plain_masses) == {None}


def test_geometry_isotope_index_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside 1..6"):
        parse_xyz(GeometryPayload(xyz_text=METHANOL_XYZ, isotopes={7: 2}))


def test_geometry_isotope_impossible_for_element_is_rejected() -> None:
    # Atom 1 is carbon; mass number 2 is not an isotope of carbon.
    with pytest.raises(ValueError, match="not a known isotope"):
        parse_xyz(GeometryPayload(xyz_text=METHANOL_XYZ, isotopes={1: 2}))


# ---------------------------------------------------------------------------
# Cross-check: the identity and the geometry must agree
# ---------------------------------------------------------------------------


def test_geometry_and_identity_isotopes_must_agree(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        with pytest.raises(ValueError, match="does not match the uploaded geometry"):
            resolve_species_entry(
                session,
                _identity("[2H]C([2H])([2H])O"),
                geometry=GeometryPayload(xyz_text=METHANOL_XYZ),
            )


def test_geometry_isotopes_without_identity_isotopes_are_rejected(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        with pytest.raises(ValueError, match="does not match the uploaded geometry"):
            resolve_species_entry(
                session,
                _identity("CO"),
                geometry=GeometryPayload(
                    xyz_text=METHANOL_XYZ, isotopes={3: 2, 5: 2, 6: 2}
                ),
            )


def test_consistent_isotope_deposit_is_accepted(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        entry = resolve_species_entry(
            session,
            _identity("[2H]C([2H])([2H])O"),
            geometry=GeometryPayload(
                xyz_text=METHANOL_XYZ, isotopes={3: 2, 5: 2, 6: 2}
            ),
        )
        session.flush()
        assert entry.isotope_key == "[2H]C([2H])([2H])O"
