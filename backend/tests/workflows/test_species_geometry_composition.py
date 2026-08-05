"""A species' geometry must be made of the atoms its own SMILES declares.

Nothing compared the two before. A species entry declaring methane could store
the coordinates of methyl and deposit successfully, and every number computed
downstream from that structure — energies, frequencies, partition functions,
thermo — would then describe a molecule nobody deposited, under a label saying
otherwise.

This is not a hypothetical failure mode. The pressure-dependent fixtures stored
geometries with hydrogen omitted entirely (ethyl as ``C C H``, ethylperoxy as
``C C O O``, HO2 as ``O O``) for as long as they existed, and nothing looked,
because the only composition check in the codebase compared a reaction's two
*sides* against each other and never a structure against its own label.

Formula agreement between a structure and its own identifier is definitional
under ADR 0008 — no correct calculation can produce a geometry that is not made
of its own molecule's atoms — so it blocks.

The interesting half of this file is the accepting half. A blocking check that
refuses correct science is worse than no check, so isotopologues, charged
species, two-letter element symbols written in whatever case an ESS felt like,
and deposits with no geometry at all must all still go through.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)
from app.services.species_resolution import (
    assert_geometry_composition_matches_identity,
    resolve_species_entry,
)
from app.workflows.computed_species import persist_computed_species_upload

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_USER_ID = 50_711

#: Methane, CH4. Five atoms: C at the origin, four H at the tetrahedral
#: vertices.
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
#: Methyl, CH3. Four atoms: C at the origin and three H in the plane. One
#: hydrogen short of the methane it will be deposited against.
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
#: Chloromethane, CH3Cl. Five atoms: C (index 1), Cl (index 2), three H
#: (indices 3-5). Written the way plenty of electronic-structure output writes
#: it — chlorine shouted, carbon and hydrogen whispered — because
#: ``geometry_atom.element`` stores whatever the XYZ said, verbatim.
_XYZ_CH3CL_MIXED_CASE = (
    "5\nchloromethane, elements as an ESS wrote them\n"
    "c  0.000  0.000  0.000\n"
    "CL 1.781  0.000  0.000\n"
    "h -0.372  1.028  0.000\n"
    "h -0.372 -0.514  0.890\n"
    "h -0.372 -0.514 -0.890"
)
#: Hydroxide, OH-. Two atoms: O (index 1) and H (index 2) at 0.964 A.
_XYZ_OH = "2\nhydroxide\nO 0.000 0.000 0.000\nH 0.000 0.000 0.964"
#: Ammonium, NH4+. Five atoms: N (index 1) and four H at the tetrahedral
#: vertices, N-H 1.02 A.
_XYZ_NH4 = (
    "5\nammonium\n"
    "N  0.000  0.000  0.000\n"
    "H  0.589  0.589  0.589\n"
    "H -0.589 -0.589  0.589\n"
    "H -0.589  0.589 -0.589\n"
    "H  0.589 -0.589 -0.589"
)


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="species_composition_tests"))
        session.flush()
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _bundle(
    *,
    smiles: str,
    xyz: str,
    charge: int = 0,
    multiplicity: int = 1,
    isotopes: dict[int, int] | None = None,
) -> dict:
    geometry: dict = {"xyz_text": xyz}
    if isotopes is not None:
        geometry["isotopes"] = isotopes
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
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }


def _upload(session: Session, payload: dict):
    return persist_computed_species_upload(
        session,
        ComputedSpeciesUploadRequest(**payload),
        created_by=_USER_ID,
    )


# ---------------------------------------------------------------------------
# It fires
# ---------------------------------------------------------------------------


def test_methane_declared_with_a_methyl_geometry_is_refused(db_engine) -> None:
    """The exact deposit that used to succeed: CH4's entry given CH3's XYZ."""

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, _bundle(smiles="C", xyz=_XYZ_CH3))
    message = str(excinfo.value)
    assert "species_geometry_composition_mismatch" in message
    assert "geometry is CH3" in message
    assert "is CH4" in message


def test_a_geometry_that_omits_hydrogen_entirely_is_refused(db_engine) -> None:
    """The fixture-rot shape: heavy atoms listed, hydrogens simply absent.

    Ethyl deposited as ``C C H``. Three atoms where C2H5 has seven, which is
    what went unnoticed for as long as those fixtures existed.
    """

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(
                session,
                _bundle(
                    smiles="[CH2]C",
                    multiplicity=2,
                    xyz=(
                        "3\nethyl with its hydrogens missing\n"
                        "C 0.00 0.00 0.00\n"
                        "C 1.49 0.00 0.00\n"
                        "H -0.38 1.01 0.00"
                    ),
                ),
            )
    message = str(excinfo.value)
    assert "species_geometry_composition_mismatch" in message
    assert "geometry is C2H" in message
    assert "is C2H5" in message


def test_a_later_conformer_with_the_wrong_atoms_is_refused(db_engine) -> None:
    """Every conformer is checked, not only the first.

    The first conformer is the one that drives stereo perception, and checking
    only that one would let a bundle deposit one correct structure and any
    number of wrong ones behind it.
    """

    payload = _bundle(smiles="C", xyz=_XYZ_CH4)
    second = {
        "key": "c1",
        "geometry": {"xyz_text": _XYZ_CH3},
        "primary_calculation": {
            "key": "opt1",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True},
        },
    }
    payload["conformers"].append(second)
    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)
    assert "species_geometry_composition_mismatch" in str(excinfo.value)


# ---------------------------------------------------------------------------
# It does not fire on correct chemistry
# ---------------------------------------------------------------------------


def test_a_geometry_that_matches_its_smiles_is_accepted(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        outcome = _upload(session, _bundle(smiles="C", xyz=_XYZ_CH4))
    assert outcome.species_entry_id is not None


def test_a_deuterated_isotopologue_is_accepted(db_engine) -> None:
    """CD4 declares four ``[2H]``; its geometry declares four H at mass 2.

    The canonical form stores element ``H`` for ``[2H]``, and the XYZ writes
    ``H`` too — the mass number rides in ``geometry.isotopes``, not in the
    element column. Counting isotope-resolved would refuse every isotopologue
    in the database, so the comparison is on elements. Isotope agreement is a
    separate check with its own multiset comparison, and it runs on this
    deposit as well.
    """

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session,
            _bundle(
                smiles="[2H]C([2H])([2H])[2H]",
                xyz=_XYZ_CH4,
                isotopes={2: 2, 3: 2, 4: 2, 5: 2},
            ),
        )
    assert outcome.species_entry_id is not None


def test_a_partially_deuterated_isotopologue_is_accepted(db_engine) -> None:
    """CH3D: one deuterium, three protium, still C1H4 by element."""

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session,
            _bundle(smiles="[2H]C", xyz=_XYZ_CH4, isotopes={2: 2}),
        )
    assert outcome.species_entry_id is not None


def test_an_anion_is_accepted(db_engine) -> None:
    """Hydroxide. RDKit's implicit-H handling on a charged heteroatom is the
    thing most likely to be got wrong here: ``[OH-]`` carries one hydrogen,
    not zero and not two."""

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session, _bundle(smiles="[OH-]", xyz=_XYZ_OH, charge=-1)
        )
    assert outcome.species_entry_id is not None


def test_a_cation_is_accepted(db_engine) -> None:
    """Ammonium. Four hydrogens on a positively charged nitrogen."""

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session, _bundle(smiles="[NH4+]", xyz=_XYZ_NH4, charge=1)
        )
    assert outcome.species_entry_id is not None


def test_a_radical_is_accepted(db_engine) -> None:
    """Methyl, where the implicit-H count depends on reading the radical."""

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session, _bundle(smiles="[CH3]", xyz=_XYZ_CH3, multiplicity=2)
        )
    assert outcome.species_entry_id is not None


def test_element_symbols_in_any_case_are_accepted(db_engine) -> None:
    """``CL`` is chlorine and ``c`` is carbon.

    ``geometry_atom.element`` holds whatever the depositor's XYZ said, and
    electronic-structure codes are not consistent about capitalisation.
    Refusing correct chemistry over a string is what ADR 0008 puts out of
    bounds for a blocking check; this exact bug shipped once already.
    """

    with _isolated_session(db_engine) as session:
        outcome = _upload(
            session, _bundle(smiles="CCl", xyz=_XYZ_CH3CL_MIXED_CASE)
        )
    assert outcome.species_entry_id is not None


def test_a_deposit_with_no_geometry_is_accepted(db_engine) -> None:
    """Absence is incompleteness, not contradiction.

    A thermo or transport record uploaded from a paper has an identity and no
    structure — those workflows call ``resolve_species_entry`` with no geometry
    at all. There is nothing to disagree with, so there is nothing to refuse.
    """

    with _isolated_session(db_engine) as session:
        entry = resolve_species_entry(
            session, _identity("C"), created_by=_USER_ID
        )
    assert entry.id is not None


# ---------------------------------------------------------------------------
# The pure seam
# ---------------------------------------------------------------------------


def _identity(smiles: str, **overrides) -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(
        **{"smiles": smiles, "charge": 0, "multiplicity": 1, **overrides}
    )


def test_an_unparseable_smiles_does_not_block() -> None:
    """A SMILES RDKit cannot read says nothing, so it contradicts nothing.

    In the upload path such a payload is already refused upstream by identity
    canonicalisation. The branch exists so this function is safe to call from
    anywhere, and asserting it keeps a future caller from discovering that
    "cannot parse" was quietly treated as "does not match".
    """

    payload = _identity("C").model_copy(update={"smiles": "not a smiles"})
    assert_geometry_composition_matches_identity(
        payload, GeometryPayload(xyz_text=_XYZ_CH3)
    )


def test_a_pseudo_species_is_exempt() -> None:
    """Mirrors ``validate_reaction_elemental_balance``.

    A lumped or phenomenological construct has no atom-resolved composition
    for a geometry to agree with.
    """

    payload = _identity("C", molecule_kind="pseudo")
    assert_geometry_composition_matches_identity(
        payload, GeometryPayload(xyz_text=_XYZ_CH3)
    )


def test_the_pure_seam_refuses_a_mismatch() -> None:
    with pytest.raises(ValueError) as excinfo:
        assert_geometry_composition_matches_identity(
            _identity("C"), GeometryPayload(xyz_text=_XYZ_CH3)
        )
    assert "species_geometry_composition_mismatch" in str(excinfo.value)
