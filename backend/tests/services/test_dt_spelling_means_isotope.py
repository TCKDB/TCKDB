"""A ``D``/``T`` element spelling means 2H/3H everywhere (#672, ADR 0022).

Before this decision identity treated ``D``/``T`` as plain hydrogen ("isotope
silent") while ``normal_modes.atomic_mass`` weighed them as deuterium and
tritium, so a geometry spelling ``D`` under a protium species passed every
identity check yet was mass-weighted as an isotopologue. The decision: a
``D``/``T`` element token is an isotope declaration. ``parse_xyz`` reads it as
``H`` with mass number 2/3, the isotope identity check and the geometry hash see
it, ``xyz_text`` keeps what the depositor wrote, and rows deposited before the
decision (``D``/``T`` with a NULL mass number, unrewritable) are read by their
own symbol and flagged, never touched.

Every test here holds one clause of that. The ones that exercise a rule a
*service* enforces also reach it without the wire schema's validation
(``model_construct``) or by calling the service directly, because a refusal that
only the schema enforces is a refusal a restore, a bulk import or a direct call
walks past.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.reaction_atom_map import parse_xyz_elements

from app.chemistry.geometry import (
    W_GEOMETRY_ISOTOPE_SYMBOL_CONFLICT,
    parse_xyz,
    resolve_element_symbol,
)
from app.chemistry.isotopes import (
    HYDROGEN_ISOTOPE_SYMBOLS,
    implied_isotope_mass_number,
    validate_isotope,
)
from app.chemistry.normal_modes import atomic_mass
from app.db.models.common import CalculationType
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.calculation_levels import _structure_key
from app.services.consistency.isotope_identity import (
    GEOMETRY_DECLARES_ISOTOPE_ENTRY_IS_PROTIUM,
    NO_CONFLICT_FOUND,
    declared_nuclides,
)
from app.services.consistency.service import invoke
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.hessian_reanalysis import ReanalysisStatus, reanalyse_calculation
from app.services.species_resolution import assert_geometry_isotopes_match_identity
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_atoms,
    attach_hessian,
    attach_input_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)
from tests.services.test_hessian_reanalysis import _diatomic, _pack

_D2O = "3\nheavy water\nO 0.0 0.0 0.117\nD 0.0 0.757 -0.469\nD 0.0 -0.757 -0.469"
_CH3T = (
    "5\nmethane with one tritium\n"
    "C 0.0 0.0 0.0\nT 0.629 0.629 0.629\nH -0.629 -0.629 0.629\n"
    "H -0.629 0.629 -0.629\nH 0.629 -0.629 -0.629"
)


def _identity(smiles: str) -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1)


# ---------------------------------------------------------------------------
# Parsing: D/T is H plus an implied mass number; the text is untouched
# ---------------------------------------------------------------------------


def test_the_symbol_table_is_exactly_deuterium_and_tritium() -> None:
    """The one place the mapping lives; widening it widens every rule below."""
    assert HYDROGEN_ISOTOPE_SYMBOLS == {"D": 2, "T": 3}
    assert implied_isotope_mass_number("D") == 2
    assert implied_isotope_mass_number("d ") == 2  # character(2) blank padding, any case
    assert implied_isotope_mass_number("T") == 3
    assert implied_isotope_mass_number("H") is None
    assert implied_isotope_mass_number("Cl") is None


def test_parse_xyz_reads_d_and_t_as_hydrogen_with_a_mass_number() -> None:
    parsed = parse_xyz(GeometryPayload(xyz_text=_CH3T))
    assert [atom[0] for atom in parsed.atoms] == ["C", "H", "H", "H", "H"]
    assert parsed.isotopes == ((2, 3),)
    assert parsed.isotope_substitutions() == {("H", 3): 1}

    heavy = parse_xyz(GeometryPayload(xyz_text=_D2O))
    assert [atom[0] for atom in heavy.atoms] == ["O", "H", "H"]
    assert heavy.isotopes == ((2, 2), (3, 2))
    assert heavy.isotope_substitutions() == {("H", 2): 2}


def test_xyz_text_keeps_the_deposited_spelling() -> None:
    parsed = parse_xyz(GeometryPayload(xyz_text="3\nx\nO 0 0 0\nd 1 0 0\nT 0 1 0"))
    assert [line.split()[0] for line in parsed.canonical_xyz_text.splitlines()[2:]] == ["O", "d", "T"]


def test_the_stored_atoms_are_hydrogen_with_the_mass_number(db_session) -> None:
    geometry = resolve_geometry_payload(db_session, GeometryPayload(xyz_text=_D2O))
    db_session.flush()
    rows = db_session.scalars(
        select(GeometryAtom).where(GeometryAtom.geometry_id == geometry.id).order_by(GeometryAtom.atom_index)
    ).all()
    assert [(r.element.strip(), r.isotope_mass_number) for r in rows] == [("O", None), ("H", 2), ("H", 2)]
    assert [line.split()[0] for line in geometry.xyz_text.splitlines()[2:]] == ["O", "D", "D"]


def test_a_new_d_file_does_not_dedupe_onto_a_legacy_d_row(db_session) -> None:
    """The hash rule: new D deposits get their own, correctly indexed row.

    A legacy row is ``D``/NULL under the old hash (the canonical text alone). The
    same file deposited now hashes with the implied isotope, so it creates a
    second row whose atoms read ``H``/2 -- it never attaches to a row whose atoms
    would read as protium.
    """
    parsed = parse_xyz(GeometryPayload(xyz_text=_D2O))
    legacy_hash = hashlib.sha256(parsed.canonical_xyz_text.encode()).hexdigest()
    legacy = Geometry(natoms=3, geom_hash=legacy_hash, xyz_text=parsed.canonical_xyz_text)
    db_session.add(legacy)
    db_session.flush()
    attach_geometry_atoms(
        db_session,
        geometry=legacy,
        symbols=["O", "D", "D"],
        coords=[[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]],
    )

    created = resolve_geometry_payload(db_session, GeometryPayload(xyz_text=_D2O))
    assert created.id != legacy.id
    assert created.geom_hash != legacy_hash


# ---------------------------------------------------------------------------
# validate_isotope and the conflict code
# ---------------------------------------------------------------------------


def test_validate_isotope_resolves_d_and_t_before_rdkit() -> None:
    """``D`` plus a real mass number used to fail as "unknown element symbol 'D'"."""
    validate_isotope("D", 2, context="x")
    validate_isotope("T", 3, context="x")
    validate_isotope("d", 2, context="x")
    with pytest.raises(ValueError, match="unknown element symbol"):
        validate_isotope("Xx", 2, context="x")


def test_an_explicit_isotope_that_equals_the_spelling_is_accepted() -> None:
    parsed = parse_xyz(GeometryPayload(xyz_text=_D2O, isotopes={2: 2}))
    assert parsed.isotopes == ((2, 2), (3, 2))


@pytest.mark.parametrize(
    ("xyz", "isotopes", "implied"),
    [
        (_D2O, {2: 3}, 2),  # D with mass 3
        (_D2O, {3: 1}, 2),  # D with mass 1: refused, not dropped as a standard isotope
        (_CH3T, {2: 2}, 3),  # T with mass 2
    ],
)
def test_an_explicit_isotope_that_contradicts_the_spelling_is_refused(xyz, isotopes, implied) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_xyz(GeometryPayload(xyz_text=xyz, isotopes=isotopes))
    error = excinfo.value
    assert error.code == W_GEOMETRY_ISOTOPE_SYMBOL_CONFLICT == "geometry_isotope_symbol_conflict"
    assert error.context["implied_mass_number"] == implied
    # The message names the fix, with the depositor's own number.
    assert f"Write H with isotope {error.context['declared_mass_number']}" in str(error)
    assert "drop the conflicting entry" in str(error)
    assert error.context["declared_mass_number"] == next(iter(isotopes.values()))


def test_the_conflict_is_refused_without_the_wire_schema_validation() -> None:
    """``model_construct`` skips pydantic; the rule lives in ``parse_xyz``, not the schema."""
    payload = GeometryPayload.model_construct(xyz_text=_D2O, isotopes={2: 3})
    with pytest.raises(ValueError) as excinfo:
        parse_xyz(payload)
    assert excinfo.value.code == "geometry_isotope_symbol_conflict"


def test_an_h_spelled_atom_may_carry_any_isotope() -> None:
    """The refusal is about a contradicting *spelling*; plain H plus a mass is the canonical form."""
    parsed = parse_xyz(GeometryPayload(xyz_text="2\nx\nH 0 0 0\nH 0.74 0 0", isotopes={1: 3}))
    assert parsed.isotopes == ((1, 3),)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("smiles", "xyz"),
    [("O", _D2O), ("C", _CH3T)],
    ids=["D2O-under-O", "CH3T-under-C"],
)
def test_a_d_or_t_geometry_under_a_protium_species_is_refused(smiles, xyz) -> None:
    with pytest.raises(ValueError) as excinfo:
        assert_geometry_isotopes_match_identity(_identity(smiles), GeometryPayload(xyz_text=xyz))
    assert excinfo.value.code == "species_geometry_isotope_mismatch"


def test_a_2H_species_with_a_d_spelled_geometry_is_accepted() -> None:
    assert_geometry_isotopes_match_identity(_identity("[2H]O[2H]"), GeometryPayload(xyz_text=_D2O))
    assert_geometry_isotopes_match_identity(_identity("[3H]C"), GeometryPayload(xyz_text=_CH3T))


def test_the_identity_check_is_reached_without_schema_validation() -> None:
    """Direct call with ``model_construct``ed inputs: the rule is the service's own."""
    geometry = GeometryPayload.model_construct(xyz_text=_D2O, isotopes=None)
    identity = SpeciesEntryIdentityPayload.model_construct(
        smiles="O", charge=0, multiplicity=1, molecule_kind="molecule"
    )
    with pytest.raises(ValueError) as excinfo:
        assert_geometry_isotopes_match_identity(identity, geometry)
    assert excinfo.value.code == "species_geometry_isotope_mismatch"


def test_composition_still_counts_d_and_t_as_hydrogen() -> None:
    """The element answer is unchanged: ``D`` is H, so a D2O geometry is H2O by element."""
    assert resolve_element_symbol("D") == "H"
    assert resolve_element_symbol("t") == "H"
    assert resolve_element_symbol("Cl") == "Cl"


# ---------------------------------------------------------------------------
# Wire boundary
# ---------------------------------------------------------------------------


def test_the_wire_element_list_reads_d_and_t_as_hydrogen() -> None:
    """An atom map compares elements; D on one side and H on the other must agree."""
    assert parse_xyz_elements(_D2O) == ["O", "H", "H"]
    assert parse_xyz_elements(_CH3T) == ["C", "H", "H", "H", "H"]


# ---------------------------------------------------------------------------
# Masses: unchanged, and pinned
# ---------------------------------------------------------------------------


def test_atomic_mass_is_unchanged_for_d_and_t_and_for_the_new_storage_form() -> None:
    """A D-spelled row whose masses are right today must stay right.

    New rows store ``H`` + 2/3; legacy rows hold ``D``/``T`` with a NULL mass.
    Both must weigh the same, and an explicit mass still overrides a symbol.
    """
    assert atomic_mass("D", None) == pytest.approx(2.014101778, abs=1e-8)
    assert atomic_mass("T", None) == pytest.approx(3.016049278, abs=1e-8)
    assert atomic_mass("H", 2) == atomic_mass("D", None)
    assert atomic_mass("H", 3) == atomic_mass("T", None)
    assert atomic_mass("H", None) == pytest.approx(1.00782503207, abs=1e-8)
    assert atomic_mass("D", 1) == pytest.approx(1.00782503207, abs=1e-8)


# ---------------------------------------------------------------------------
# Legacy rows: D/T with a NULL mass number
# ---------------------------------------------------------------------------


def _legacy_deposit(session, symbols, *, isotope_key=None, mass_numbers=None):
    """A frequency calculation on a legacy-shaped geometry, filed under an entry."""
    elements, coords, matrix, expected = _diatomic()
    species = make_species(session, inchi_key=next_inchi_key("DTLG"))
    entry = make_species_entry(session, species, isotope_key=isotope_key)
    calc = make_calculation(session, type=CalculationType.freq, species_entry_id=entry.id)
    geometry = make_geometry(session, natoms=len(symbols))
    atoms = attach_geometry_atoms(session, geometry=geometry, symbols=list(symbols), coords=coords.tolist())
    if mass_numbers is not None:
        for atom, mass_number in zip(atoms, mass_numbers, strict=True):
            atom.isotope_mass_number = mass_number
        session.flush()
    attach_input_geometry(session, calculation=calc, geometry=geometry)
    # The stored list is whatever the deposit says it is; reanalysis only needs it to exist.
    attach_freq_result(session, calculation=calc, frequencies_cm1=[round(f, 4) for f in expected])
    attach_hessian(session, calculation=calc, geometry=geometry, natoms=len(symbols), lower_triangle=_pack(matrix))
    return calc, entry


def test_reanalysis_of_a_d_geometry_under_a_protium_entry_is_an_identity_conflict(db_session) -> None:
    calc, _entry = _legacy_deposit(db_session, ["D", "Cl"])
    result = reanalyse_calculation(db_session, calc)
    assert result.status is ReanalysisStatus.isotope_identity_conflict
    assert result.modes == () and result.within_tolerance is None
    assert result.calculation_ref == calc.public_ref


def test_a_stored_mass_number_under_a_protium_entry_is_the_same_conflict(db_session) -> None:
    """The new storage form (``H`` + 2), reached without the upload refusal (e.g. a restore)."""
    calc, _entry = _legacy_deposit(db_session, ["H", "Cl"], mass_numbers=[2, None])
    assert reanalyse_calculation(db_session, calc).status is ReanalysisStatus.isotope_identity_conflict


def test_the_conflict_covers_any_element_not_only_hydrogen(db_session) -> None:
    """Scope, stated: any non-standard nuclide under a protium entry is the same contradiction."""
    calc, _entry = _legacy_deposit(db_session, ["H", "Cl"], mass_numbers=[None, 37])
    assert reanalyse_calculation(db_session, calc).status is ReanalysisStatus.isotope_identity_conflict
    assert declared_nuclides([type("A", (), {"element": "Cl", "isotope_mass_number": 37})()]) == {"37Cl": 1}


def test_reanalysis_is_not_blocked_when_the_entry_declares_the_isotope(db_session) -> None:
    calc, _entry = _legacy_deposit(db_session, ["D", "Cl"], isotope_key="[2H]Cl")
    assert reanalyse_calculation(db_session, calc).status is not ReanalysisStatus.isotope_identity_conflict


def test_reanalysis_of_an_ordinary_geometry_under_a_protium_entry_is_untouched(db_session) -> None:
    calc, _entry = _legacy_deposit(db_session, ["H", "Cl"])
    assert reanalyse_calculation(db_session, calc).status is ReanalysisStatus.analysed


def test_a_legacy_d_atom_and_a_new_h2_atom_are_one_structure(db_session) -> None:
    """``_structure_key`` reads a legacy row by its own symbol, so the two do not diverge."""
    legacy = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(db_session, geometry=legacy, symbols=["D"], coords=[[0.0, 0.0, 0.0]])
    modern = make_geometry(db_session, natoms=1)
    (atom,) = attach_geometry_atoms(db_session, geometry=modern, symbols=["H"], coords=[[1.0, 0.0, 0.0]])
    atom.isotope_mass_number = 2
    db_session.flush()
    db_session.refresh(legacy)
    db_session.refresh(modern)
    assert _structure_key(legacy) == _structure_key(modern) == ("atom", "H", 2)
    # A D atom is a different structure from a protium atom (the key carries the mass).
    plain = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(db_session, geometry=plain, symbols=["H"], coords=[[2.0, 0.0, 0.0]])
    db_session.refresh(plain)
    assert _structure_key(plain) == ("atom", "H", None) != _structure_key(modern)


def test_a_rigidly_moved_polyatomic_geometry_is_the_same_structure_across_the_two_d_forms(db_session) -> None:
    """The #667 rigid-motion merge reads a legacy ``D`` row and a new ``H`` + 2 row as one atom kind.

    Same coordinates (so Kabsch RMSD is zero), so only the per-atom nuclide
    decides: legacy ``D``/NULL against new ``H``/2 must agree, and either must
    differ from plain ``H``.
    """
    from app.services.calculation_levels import _atoms_of, _same_polyatomic_structure

    coords = [[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]]

    def build(symbols, masses):
        geometry = make_geometry(db_session, natoms=3)
        atoms = attach_geometry_atoms(db_session, geometry=geometry, symbols=symbols, coords=coords)
        for atom, mass_number in zip(atoms, masses, strict=True):
            atom.isotope_mass_number = mass_number
        db_session.flush()
        db_session.refresh(geometry)
        atoms_of = _atoms_of(geometry)
        assert atoms_of is not None
        return atoms_of

    legacy = build(["O", "D", "D"], [None, None, None])
    modern = build(["O", "H", "H"], [None, 2, 2])
    plain = build(["O", "H", "H"], [None, None, None])
    assert _same_polyatomic_structure(legacy, modern)
    assert not _same_polyatomic_structure(legacy, plain)
    assert not _same_polyatomic_structure(modern, plain)


# ---------------------------------------------------------------------------
# The advisory finding for legacy contradictions
# ---------------------------------------------------------------------------


def test_declared_nuclides_reads_the_rows_own_symbol_and_mass() -> None:
    class Atom:
        def __init__(self, element, mass):
            self.element, self.isotope_mass_number = element, mass

    assert declared_nuclides([Atom("H ", None), Atom("D ", None), Atom("T ", None), Atom("H ", 2)]) == {
        "2H": 2,
        "3H": 1,
    }
    assert declared_nuclides([Atom("H", 1), Atom("C", 12), Atom("C", 13)]) == {"13C": 1}
    assert declared_nuclides([]) == {}


def test_the_review_finding_flags_a_protium_entry_with_a_d_geometry(db_session) -> None:
    calc, entry = _legacy_deposit(db_session, ["D", "Cl"])
    result, row = invoke(db_session, check="isotope-identity", target_ref=entry.public_ref, commit=True)

    reasons = [json.loads(f.message)["reason"] for f in result.findings]
    assert reasons == [GEOMETRY_DECLARES_ISOTOPE_ENTRY_IS_PROTIUM]
    payload = json.loads(result.findings[0].message)
    assert payload["calculation_ref"] == calc.public_ref
    assert payload["declared_nuclides"] == {"2H": 1}
    assert payload["declared_by_legacy_symbol_only"] is True
    assert calc.public_ref in result.findings[0].evidence_keys

    stored = db_session.get(RecordMachineReviewRow, row.id)
    assert stored.record_type.value == "species_entry"
    assert stored.provider == "tckdb.scientific_checks"
    assert stored.model == "isotope_identity_consistency"
    assert stored.rubric_versions_json == {"isotope_identity_consistency_v1": "1"}


def test_the_review_finding_is_silent_on_an_ordinary_entry(db_session) -> None:
    _calc, entry = _legacy_deposit(db_session, ["H", "Cl"])
    result, _row = invoke(db_session, check="isotope-identity", target_ref=entry.public_ref)
    reasons = [json.loads(f.message)["reason"] for f in result.findings]
    assert reasons == [NO_CONFLICT_FOUND]


def test_the_review_finding_does_not_judge_an_isotope_labelled_entry(db_session) -> None:
    _calc, entry = _legacy_deposit(db_session, ["D", "Cl"], isotope_key="[2H]Cl")
    result, _row = invoke(db_session, check="isotope-identity", target_ref=entry.public_ref)
    reasons = [json.loads(f.message)["reason"] for f in result.findings]
    assert reasons == ["entry_declares_isotopes_not_judged"]


def test_the_review_finding_refuses_a_non_entry_reference(db_session) -> None:
    with pytest.raises(ValueError, match="spe_"):
        invoke(db_session, check="isotope-identity", target_ref="thm_nope")
    with pytest.raises(ValueError, match="alone"):
        invoke(db_session, check="isotope-identity", target_ref="spe_x", temperature_grid=(300.0,))
