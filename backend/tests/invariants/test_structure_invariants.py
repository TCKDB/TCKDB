"""Chemistry / structure consistency invariants.

Protect the backend against silently accepting a geometry whose atom
count or connectivity is inconsistent with the molecular representation
it is being attached to, and document the current reaction-balance
policy so a future change to that policy fails loudly rather than
quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.identity import (
    ELECTRON_CHARGE,
    ELECTRON_MULTIPLICITY,
    ELECTRON_SMILES,
)

from app.chemistry.geometry import parse_xyz
from app.db.models.common import MoleculeKind, StereoKind, ValidationStatus
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.calculation_ownership import (
    W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
    W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
    W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
    W_THERMO_STATMECH_OWNER_MISMATCH,
    assert_calculation_owned_by,
    assert_owned_by,
    assert_statmech_owned_by,
)
from app.services.geometry_validation import validate_calculation_geometry
from app.workflows.reaction import persist_reaction_upload

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
    # The reason names the check that ran -- a formula comparison -- rather
    # than the graph isomorphism the old sentence claimed and the code has
    # never performed.
    assert "does not match the declared species SMILES" in (
        result.validation_reason or ""
    )
    assert "molecular-formula check" in (result.validation_reason or "")


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

    The owner-consistency check only reads ``id``, ``species_entry_id``
    and ``transition_state_entry_id``; a full ORM row requires DB setup
    that adds no signal to this invariant.
    """

    def __init__(
        self,
        *,
        id: int,
        species_entry_id: int | None = None,
        transition_state_entry_id: int | None = None,
    ) -> None:
        self.id = id
        self.species_entry_id = species_entry_id
        self.transition_state_entry_id = transition_state_entry_id


def test_owner_guard_rejects_calculation_owned_by_other_species() -> None:
    """``assert_calculation_owned_by`` is the guard that prevents a
    product from attaching a calculation belonging to a different
    ``species_entry``.

    A silent regression here (e.g. flipped equality, or the guard being
    dropped during refactor) would let scientifically meaningless
    provenance links into the DB while every CRUD test still passes.
    """
    calc = _FakeCalc(id=42, species_entry_id=7)

    # Same owner → no error.
    assert_calculation_owned_by(
        calc,  # type: ignore[arg-type]
        code=W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
        target="thermo",
        context="same owner",
        species_entry_id=7,
    )

    # Different owner → raise, with the code and not a scraped field name.
    with pytest.raises(ValueError, match="another species entry") as excinfo:
        assert_calculation_owned_by(
            calc,  # type: ignore[arg-type]
            code=W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="thermo",
            context="cross-owner",
            species_entry_id=8,
        )
    assert excinfo.value.code == "thermo_source_calculation_owner_mismatch"


def test_owner_guard_rejects_calculation_owned_by_other_ts_entry() -> None:
    """The same guard, for the transition-state half of the relationship.

    The bundle statmech path checks a transition-state entry rather than a
    species entry, and before #162 that branch lived in its own inline
    copy of the comparison. It is the same claim and now the same code.
    """
    calc = _FakeCalc(id=42, transition_state_entry_id=3)

    assert_calculation_owned_by(
        calc,  # type: ignore[arg-type]
        code=W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
        target="statmech",
        context="same owner",
        transition_state_entry_id=3,
    )

    with pytest.raises(ValueError, match="another transition state entry"):
        assert_calculation_owned_by(
            calc,  # type: ignore[arg-type]
            code=W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="statmech",
            context="cross-owner",
            transition_state_entry_id=4,
        )


def test_owner_guard_refuses_to_run_with_nothing_to_compare() -> None:
    """A guard given no owner would accept every calculation.

    The recurring defect in this area is a check that cannot fail. If both
    owner ids are ``None`` the comparison is vacuous, so the call is a
    programming error and says so, rather than silently passing.
    """
    calc = _FakeCalc(id=42, species_entry_id=7)

    with pytest.raises(ValueError, match="guard nothing"):
        assert_calculation_owned_by(
            calc,  # type: ignore[arg-type]
            code=W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="thermo",
            context="no owner supplied",
        )


def test_owner_guard_rejects_statmech_owned_by_another_subject() -> None:
    """The statmech spelling of the same rule (#195).

    A thermo record cites a statmech row by ``existing_statmech_id`` and a
    kinetics interpretation cites one by ``statmech_ref``; both used to
    compare inline and raise a bare ``ValueError``. ``Statmech`` carries
    the same two owner columns a calculation does, so the comparison is
    shared and only the noun and the code differ -- which is what this
    checks, in both directions.
    """
    statmech = _FakeCalc(id=9, species_entry_id=7)

    assert_statmech_owned_by(
        statmech,  # type: ignore[arg-type]
        code=W_THERMO_STATMECH_OWNER_MISMATCH,
        target="thermo",
        context="same owner",
        species_entry_id=7,
    )

    with pytest.raises(ValueError, match="statmech record belongs") as excinfo:
        assert_statmech_owned_by(
            statmech,  # type: ignore[arg-type]
            code=W_THERMO_STATMECH_OWNER_MISMATCH,
            target="thermo",
            context="cross-owner",
            species_entry_id=8,
        )
    assert excinfo.value.code == "thermo_statmech_owner_mismatch"
    # The noun is a parameter, so the calculation wording must not have
    # followed it.
    assert "calculation" not in str(excinfo.value)


def test_owner_guard_refuses_a_vacuous_call_whatever_the_noun() -> None:
    """The "nothing to compare against" guard survived being parameterised.

    ``assert_calculation_owned_by`` is covered above; this is the core
    function the two wrappers now delegate to, reached with a noun neither
    of them uses. Without it the check could be lost for every caller that
    does not go through a wrapper -- which is the conformer-selection call
    site in ``app.workflows.kinetics``.
    """
    with pytest.raises(ValueError, match="guard nothing"):
        assert_owned_by(
            subject_noun="conformer selection",
            row_id=1,
            row_species_entry_id=7,
            row_transition_state_entry_id=None,
            code=W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
            target="reactant 1",
            context="no owner supplied",
        )


def test_every_product_routes_its_owner_check_through_one_implementation() -> None:
    """#162: the rule was written five times and pinned once.

    Two of those copies had already drifted -- thermo's logged nothing
    where statmech's and transport's logged the row ids an operator needs
    -- and nothing went red, because only thermo's copy was covered. The
    invariant is now the consolidation itself: no workflow may hold a
    private owner-consistency comparison, and every module that needs one
    must reach the shared implementation.

    ``app/workflows/kinetics.py`` joined the list in #195. It cites
    statmech records and conformer selections rather than calculations, so
    it reaches the module by a different name -- which is why the check is
    on the *import* rather than on one function's name.
    """
    modules = [
        "app/workflows/thermo.py",
        "app/workflows/statmech.py",
        "app/workflows/transport.py",
        "app/workflows/computed_species.py",
        "app/workflows/computed_reaction.py",
        "app/workflows/kinetics.py",
        "app/services/statmech_resolution.py",
    ]
    root = Path(__file__).resolve().parents[2]
    for relative in modules:
        source = (root / relative).read_text()
        assert "from app.services.calculation_ownership import" in source, relative
        # The private per-product copies this consolidated (#162).
        assert "def _assert_calculation_owned_by" not in source, relative
        assert "def assert_statmech_calculation_owned_by" not in source, relative


def _uncoded_owner_comparisons(source: str) -> list[tuple[int, str]]:
    """``(line, code)`` for every ``if x._entry_id != y:`` that bare-raises.

    The shape all seven #195 sites had: compare an owner column, then
    ``raise ValueError`` -- which reaches a depositor as
    ``validation_error`` and cannot be branched on. Detected structurally
    rather than by message, because the messages were all different and
    that is precisely why nothing caught them.
    """
    import ast

    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        compares = [
            inner
            for inner in ast.walk(node.test)
            if isinstance(inner, ast.Compare)
            and any(isinstance(op, ast.NotEq) for op in inner.ops)
            and isinstance(inner.left, ast.Attribute)
            and inner.left.attr.endswith("_entry_id")
        ]
        if not compares:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Raise) or not isinstance(inner.exc, ast.Call):
                continue
            name = getattr(inner.exc.func, "id", None) or getattr(
                inner.exc.func, "attr", None
            )
            if name in {"ValueError", "AssertionError"}:
                found.append((inner.lineno, name))
    return found


def test_the_uncoded_comparison_detector_can_say_no() -> None:
    """Guard the guard: it must fire on the shape and not on its repair.

    The same discipline as ``test_the_origin_detector_can_say_no`` in the
    catalogue tests. A detector that matches nothing passes the invariant
    below over an empty set, which is this repository's dominant defect.
    """
    offending = _uncoded_owner_comparisons(
        "def f(row, owner):\n"
        "    if row.species_entry_id != owner:\n"
        "        raise ValueError('not owned by this species entry.')\n"
    )
    assert offending, "the detector cannot see the shape it was written for"

    assert not _uncoded_owner_comparisons(
        "def f(row, owner):\n"
        "    assert_calculation_owned_by(row, species_entry_id=owner)\n"
    )
    # A coded refusal in the same shape is the *repair*, not the defect.
    assert not _uncoded_owner_comparisons(
        "def f(row, owner):\n"
        "    if row.species_entry_id != owner:\n"
        "        raise CodedValueError('some_code', 'not owned.')\n"
    )


def test_no_module_refuses_an_owner_mismatch_without_a_code() -> None:
    """#195: seven guards compared an owner column and bare-raised.

    Each reached a client as ``validation_error``, so a depositor could
    not tell "you cited another species' partition function" from a
    malformed number, and three earlier changes each coded a subset and
    left the rest -- which is how there came to be seven.

    Scanned over the whole of ``app/`` rather than a list of the modules
    that carry the rule today, deliberately. A list is the mechanism that
    produced this defect: the module that gained the eighth copy would not
    be on it, and nothing would go red. The scan lands green on the whole
    tree, so there is no cost to the wider net -- measured, not assumed.

    ``_uncoded_owner_comparisons`` is exercised in both directions by the
    test above; the floor below is the other half of keeping this
    non-vacuous, since an assertion that nothing was found is satisfied
    perfectly by a walker that read nothing.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    scanned = 0
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text()
        scanned += 1
        relative = path.relative_to(root.parent)
        for line, name in _uncoded_owner_comparisons(source):
            problems.append(f"{relative}:{line} raises a bare {name}")
    assert scanned > 200, (
        f"only {scanned} modules were scanned; the walk is broken and a "
        "broken walk passes this test by finding nothing"
    )
    assert not problems, (
        "these compare an owner column and refuse without a machine-readable "
        "code, so a client receives validation_error and cannot branch on "
        "them: " + "; ".join(problems)
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


# ---------------------------------------------------------------------------
# Invariant 5: species identity does not carry ``kind``, and nothing reachable
# needs it to
# ---------------------------------------------------------------------------


def test_species_identity_key_excludes_molecule_kind() -> None:
    """``uq_species_identity`` and the content-derived public ref agree.

    Species identity is ``(smiles, charge, multiplicity)`` — DR-0031 — and
    ``Species.kind`` is not part of it. ``_canonical_species`` is keyed on the
    same tuple (plus ``stereo_kind``, which that constraint makes functionally
    dependent on it), so the two cannot drift into a state where two rows
    share a constraint slot but not a ref, or the reverse.

    This test exists so that adding ``kind`` to either side *alone* fails
    here rather than silently in a hosted database. Making ``kind`` part of
    species identity is a schema decision — a new unique constraint, a
    migration, and a story for existing rows — not a ref-generation one.
    """
    from app.db.models.species import Species
    from app.services.public_refs import _canonical_species

    identity = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in Species.__table__.constraints
        if getattr(constraint, "name", None) == "uq_species_identity"
    }
    assert identity["uq_species_identity"] == ["smiles", "charge", "multiplicity"]

    canonical = _canonical_species(
        Species(
            kind=MoleculeKind.molecule,
            smiles="CC",
            inchi_key="A" * 27,
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.achiral,
        )
    )
    assert "kind=" not in canonical.replace("stereo_kind=", "")


def test_only_molecule_and_electron_can_reach_a_species_row() -> None:
    """The write path admits two kinds, and they cannot share one identity.

    ``resolve_species`` is the application's only ``Species`` writer and it
    canonicalises through ``canonical_species_identity``, which refuses every
    ``molecule_kind`` but ``molecule`` and ``electron``. The collision the
    identity key would otherwise permit — a ``pseudo`` row occupying the slot
    a ``molecule`` needs, silently handing that molecule the pseudo exemption
    from elemental balance and charge conservation — is therefore unreachable.

    ``electron`` is reachable, and safe for a second, independent reason: its
    payload validator pins ``electron`` to ``[e-]``/-1/2 in both directions,
    so it occupies an identity tuple no molecule can express.

    Should ``pseudo`` ever become depositable, this test fails, and the
    question it fails on is the one worth answering first: ``resolve_species``
    returns an existing row without comparing its ``kind`` to the payload's,
    so the first writer's ``kind`` would silently win for every later deposit.
    """
    from app.chemistry.species import canonical_species_identity

    electron = SpeciesEntryIdentityPayload(
        molecule_kind=MoleculeKind.electron,
        smiles=ELECTRON_SMILES,
        charge=ELECTRON_CHARGE,
        multiplicity=ELECTRON_MULTIPLICITY,
    )
    assert canonical_species_identity(electron)[0] == ELECTRON_SMILES

    # A molecule may not occupy the electron's identity tuple ...
    with pytest.raises(ValidationError):
        SpeciesEntryIdentityPayload(
            molecule_kind=MoleculeKind.molecule,
            smiles=ELECTRON_SMILES,
            charge=ELECTRON_CHARGE,
            multiplicity=ELECTRON_MULTIPLICITY,
        )
    # ... and the electron may not occupy any other.
    with pytest.raises(ValidationError):
        SpeciesEntryIdentityPayload(
            molecule_kind=MoleculeKind.electron,
            smiles="CC",
            charge=0,
            multiplicity=1,
        )

    with pytest.raises(ValueError, match="only molecule species"):
        canonical_species_identity(
            SpeciesEntryIdentityPayload(
                molecule_kind=MoleculeKind.pseudo,
                smiles="CC",
                charge=0,
                multiplicity=1,
            )
        )
