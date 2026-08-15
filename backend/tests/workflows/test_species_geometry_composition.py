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

**Scope: conformer geometries, and only those.** The check runs from
``resolve_species_entry``, so it reaches every conformer geometry on the
computed-species bundle, ``/uploads/conformers``, the computed-reaction bundle
and the PDep bundle. It does **not** reach ``CalculationIn.input_geometries``
or ``output_geometries``, which are attached to a ``calculation`` row and
never resolved through ``resolve_species_entry``. Those were unchecked
everywhere until #143 and are now owned by
``app.services.calculation_geometry_composition``, tested in
``tests/services/test_calculation_geometry_composition.py``. The two rules
compare the same way on purpose; what differs is the subject each compares
against.

The interesting half of this file is the accepting half. A blocking check that
refuses correct science is worse than no check, so isotopologues, charged
species, hydrogens written ``D`` or ``T``, two-letter element symbols written
in whatever case an ESS felt like, and deposits with no geometry at all must
all still go through.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation, CalculationGeometryValidation
from app.db.models.common import ValidationStatus
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
#: it — chlorine shouted, carbon and hydrogen whispered. ``geometry.xyz_text``
#: keeps that spelling; ``geometry_atom.element`` canonicalises it.
_XYZ_CH3CL_MIXED_CASE = (
    "5\nchloromethane, elements as an ESS wrote them\n"
    "c  0.000  0.000  0.000\n"
    "CL 1.781  0.000  0.000\n"
    "h -0.372  1.028  0.000\n"
    "h -0.372 -0.514  0.890\n"
    "h -0.372 -0.514 -0.890"
)
#: Heavy water, written the way an ESS is entitled to write it: ``D`` in the
#: element column. Gaussian, ORCA, Molpro and CFOUR all emit or accept the
#: token, and ``geometry_atom.element`` keeps it by design — ingestion
#: canonicalises case and deliberately leaves nuclide labelling alone.
_XYZ_D2O = (
    "3\nheavy water, hydrogens written as D\n"
    "O  0.000  0.000  0.117\n"
    "D  0.000  0.757 -0.469\n"
    "D  0.000 -0.757 -0.469"
)
#: Tritiated methane, CH3T, with the tritium written ``T``.
_XYZ_CH3T = (
    "5\nmethane with one tritium\n"
    "C  0.000  0.000  0.000\n"
    "T  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
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
def _isolated_session(db_conn) -> Iterator[Session]:
    session = Session(bind=db_conn, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="species_composition_tests"))
        session.flush()
        yield session
    finally:
        session.close()


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


def test_methane_declared_with_a_methyl_geometry_is_refused(db_conn) -> None:
    """The exact deposit that used to succeed: CH4's entry given CH3's XYZ."""

    with _isolated_session(db_conn) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, _bundle(smiles="C", xyz=_XYZ_CH3))
    message = str(excinfo.value)
    assert "species_geometry_composition_mismatch" in message
    assert "geometry is CH3" in message
    assert "is CH4" in message


def test_a_geometry_that_omits_hydrogen_entirely_is_refused(db_conn) -> None:
    """The fixture-rot shape: heavy atoms listed, hydrogens simply absent.

    Ethyl deposited as ``C C H``. Three atoms where C2H5 has seven, which is
    what went unnoticed for as long as those fixtures existed.
    """

    with _isolated_session(db_conn) as session:
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


def test_a_later_conformer_with_the_wrong_atoms_is_refused(db_conn) -> None:
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
    with _isolated_session(db_conn) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)
    assert "species_geometry_composition_mismatch" in str(excinfo.value)


# ---------------------------------------------------------------------------
# It does not fire on correct chemistry
# ---------------------------------------------------------------------------


def test_a_geometry_that_matches_its_smiles_is_accepted(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        outcome = _upload(session, _bundle(smiles="C", xyz=_XYZ_CH4))
    assert outcome.species_entry_id is not None


def test_a_deuterated_isotopologue_is_accepted(db_conn) -> None:
    """CD4 declares four ``[2H]``; its geometry declares four H at mass 2.

    The canonical form stores element ``H`` for ``[2H]``, and the XYZ writes
    ``H`` too — the mass number rides in ``geometry.isotopes``, not in the
    element column. Counting isotope-resolved would refuse every isotopologue
    in the database, so the comparison is on elements. Isotope agreement is a
    separate check with its own multiset comparison, and it runs on this
    deposit as well.
    """

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session,
            _bundle(
                smiles="[2H]C([2H])([2H])[2H]",
                xyz=_XYZ_CH4,
                isotopes={2: 2, 3: 2, 4: 2, 5: 2},
            ),
        )
    assert outcome.species_entry_id is not None


def test_a_partially_deuterated_isotopologue_is_accepted(db_conn) -> None:
    """CH3D: one deuterium, three protium, still C1H4 by element."""

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session,
            _bundle(smiles="[2H]C", xyz=_XYZ_CH4, isotopes={2: 2}),
        )
    assert outcome.species_entry_id is not None


def test_an_anion_is_accepted(db_conn) -> None:
    """Hydroxide. RDKit's implicit-H handling on a charged heteroatom is the
    thing most likely to be got wrong here: ``[OH-]`` carries one hydrogen,
    not zero and not two."""

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session, _bundle(smiles="[OH-]", xyz=_XYZ_OH, charge=-1)
        )
    assert outcome.species_entry_id is not None


def test_a_cation_is_accepted(db_conn) -> None:
    """Ammonium. Four hydrogens on a positively charged nitrogen."""

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session, _bundle(smiles="[NH4+]", xyz=_XYZ_NH4, charge=1)
        )
    assert outcome.species_entry_id is not None


def test_a_radical_is_accepted(db_conn) -> None:
    """Methyl, where the implicit-H count depends on reading the radical."""

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session, _bundle(smiles="[CH3]", xyz=_XYZ_CH3, multiplicity=2)
        )
    assert outcome.species_entry_id is not None


def test_deuterium_written_as_the_element_D_is_accepted(db_conn) -> None:
    """``D`` is hydrogen, and this check must know it.

    ``D`` is a legal, common XYZ token — Gaussian, ORCA, Molpro and CFOUR all
    emit or accept it — and ``geometry_atom.element`` keeps it. A raw
    comparison reads this geometry as containing an element water's SMILES
    never mentions and refuses a deposit that was accepted before any
    composition check existed. That is a regression on correct chemistry, and
    the error message it produced ended by promising that "isotope labels are
    counted as their element, so an isotopologue is not a mismatch" — while
    refusing the depositor for an isotope label.

    Note what the ``D`` does *not* do: it carries no isotope identity. Isotope
    identity lives in ``geometry.isotopes`` and in SMILES isotope notation, so
    this deposit is the ordinary H2O it declares itself to be. That is
    unchanged from before the composition check, and deliberately not widened
    here.
    """

    with _isolated_session(db_conn) as session:
        outcome = _upload(session, _bundle(smiles="O", xyz=_XYZ_D2O))
    assert outcome.species_entry_id is not None


def test_tritium_written_as_the_element_T_is_accepted(db_conn) -> None:
    """``T`` is hydrogen too, and for exactly the same reason."""

    with _isolated_session(db_conn) as session:
        outcome = _upload(session, _bundle(smiles="C", xyz=_XYZ_CH3T))
    assert outcome.species_entry_id is not None


def test_a_D_geometry_with_the_wrong_atom_count_is_still_refused(
    db_conn,
) -> None:
    """Resolving ``D`` to ``H`` must not blunt the check.

    Otherwise the fix for the deuterium regression would have bought
    acceptance by making the rule stop counting hydrogens at all.
    """

    with _isolated_session(db_conn) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(
                session,
                _bundle(
                    smiles="O",
                    xyz="2\nhydroxyl written with a D\nO 0.0 0.0 0.0\nD 0.0 0.0 0.96",
                ),
            )
    message = str(excinfo.value)
    assert "species_geometry_composition_mismatch" in message
    assert "geometry is HO" in message
    assert "is H2O" in message


def test_element_symbols_in_any_case_are_accepted(db_conn) -> None:
    """``CL`` is chlorine and ``c`` is carbon.

    Electronic-structure codes are not consistent about capitalisation, and
    refusing correct chemistry over a string is what ADR 0008 puts out of
    bounds for a blocking check; this exact bug shipped once already. Since
    ``b4e7c1d20f83`` the case is settled on the way into
    ``geometry_atom.element`` rather than at each comparison, but the deposit
    under test is unchanged and so is what it must be allowed to do.

    The upload succeeding is only half of it. ``validate_calculation_geometry``
    compares the very same symbols on the very same deposit, and it compared
    them raw — so this bundle was accepted at the blocking tier and
    simultaneously recorded ``is_isomorphic=False`` /
    ``validation_status='fail'`` in ``calc_geometry_validation``, a false
    ``fail`` on correct chemistry with two checks in one tree disagreeing about
    one deposit. Per ADR 0008 §9 a fact gets one owner; asserting the persisted
    row here is what keeps the advisory tier from contradicting the blocking
    one.
    """

    with _isolated_session(db_conn) as session:
        outcome = _upload(
            session, _bundle(smiles="CCl", xyz=_XYZ_CH3CL_MIXED_CASE)
        )
        assert outcome.species_entry_id is not None

        session.flush()
        # Scoped to this deposit's own calculation: the database is shared
        # for the whole pytest process and other trees commit into it, so an
        # unfiltered count would depend on what else ran first.
        validation = session.scalar(
            select(CalculationGeometryValidation).where(
                CalculationGeometryValidation.calculation_id.in_(
                    select(Calculation.id).where(
                        Calculation.species_entry_id == outcome.species_entry_id
                    )
                )
            )
        )
        assert validation is not None
        assert validation.is_isomorphic is True
        assert validation.validation_status == ValidationStatus.passed


def test_a_deposit_with_no_geometry_is_accepted(db_conn) -> None:
    """Absence is incompleteness, not contradiction.

    A thermo or transport record uploaded from a paper has an identity and no
    structure — those workflows call ``resolve_species_entry`` with no geometry
    at all. There is nothing to disagree with, so there is nothing to refuse.
    """

    with _isolated_session(db_conn) as session:
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


def test_the_formula_is_rendered_in_hill_notation() -> None:
    """A chemist must recognise the formula in the error, or it is noise.

    Ordering the elements alphabetically renders chloromethane ``CClH3``, which
    nobody reads as the molecule they deposited. Hill notation — carbon, then
    hydrogen, then the rest alphabetically — renders it ``CH3Cl``, and is what
    every formula in the literature uses.
    """

    with pytest.raises(ValueError) as excinfo:
        assert_geometry_composition_matches_identity(
            _identity("CCl"), GeometryPayload(xyz_text=_XYZ_CH3)
        )
    message = str(excinfo.value)
    assert "is CH3Cl" in message
    assert "CClH3" not in message


def test_a_carbon_free_formula_stays_alphabetical() -> None:
    """Hill notation only privileges C and H when there is a carbon.

    Water is ``H2O``, not ``OH2``: with no carbon, hydrogen takes its
    alphabetical place like everything else.
    """

    with pytest.raises(ValueError) as excinfo:
        assert_geometry_composition_matches_identity(
            _identity("O"), GeometryPayload(xyz_text=_XYZ_CH3)
        )
    assert "is H2O" in str(excinfo.value)
