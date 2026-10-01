"""Composite schemes, lazy catalogue rows and level-of-theory binding (ADR 0021, P2).

What each group pins:

* the canonical JSON and the hash a named method's scheme is identified by;
* resolution binds a catalogued method (every spelling that keys to it), binds
  once, never moves the level's hash or ref, and leaves any other method alone;
* the get-or-create fallbacks that make it race-safe;
* a merged level is followed before binding;
* a composite level is refused as a scheme term input, bound or not yet bound;
* the database's own checks on the new tables.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.chemistry import composite_methods
from app.db.models.common import (
    CompositeBindingSource,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
)
from app.db.models.composite_scheme import (
    CompositeScheme,
    CompositeSchemeTerm,
    CompositeSchemeTermInput,
    LevelOfTheoryComposite,
)
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.services import composite_scheme_resolution as service
from app.services.calculation_resolution import _level_of_theory_hash, resolve_level_of_theory_ref
from app.services.composite_scheme_resolution import (
    CompositeInputLevelError,
    add_scheme_term_input,
    assert_ordinary_input_level,
    named_method_canonical_json,
    named_method_definition_hash,
)
from app.services.public_refs import make_content_ref

_MIGRATION_FILE = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("d7a3f1b9c284_*.py")
)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _binding(session, lot: LevelOfTheory) -> LevelOfTheoryComposite | None:
    return session.scalar(
        select(LevelOfTheoryComposite)
        .where(LevelOfTheoryComposite.level_of_theory_id == lot.id)
        .execution_options(populate_existing=True)
    )


# ---------------------------------------------------------------------------
# definition_hash
# ---------------------------------------------------------------------------


def test_named_method_canonical_json_is_pinned():
    """Changing this string changes the identity and ref of every named-method scheme."""
    assert named_method_canonical_json("cbs-qb3") == '{"kind":"named_method","method":"cbs-qb3"}'
    assert named_method_canonical_json("g4mp2") == '{"kind":"named_method","method":"g4mp2"}'


def test_named_method_definition_hash_is_pinned_and_stable():
    expected = hashlib.sha256(b'{"kind":"named_method","method":"cbs-qb3"}').hexdigest()
    assert named_method_definition_hash("cbs-qb3") == expected
    # A literal, so a change to the hash function is a visible edit to this file.
    assert expected == "4f4c3005feb6270899547a72867d6e848ce0be685826c3b3ff957da6d7c2ddfb"
    assert named_method_definition_hash("cbs-qb3") == named_method_definition_hash("cbs-qb3")
    assert named_method_definition_hash("cbs-qb3") != named_method_definition_hash("rocbs-qb3")
    assert len({named_method_definition_hash(e.key) for e in composite_methods.CATALOGUE}) == len(
        composite_methods.CATALOGUE
    )


def test_a_scheme_ref_is_content_derived_from_its_definition_hash():
    from app.services.public_refs import generate_ref_for

    h = named_method_definition_hash("cbs-qb3")
    one = CompositeScheme(kind=CompositeSchemeKind.named_method, name="a", definition_hash=h)
    two = CompositeScheme(kind=CompositeSchemeKind.named_method, name="a different name", definition_hash=h)
    other = CompositeScheme(
        kind=CompositeSchemeKind.named_method, name="a", definition_hash=named_method_definition_hash("g4")
    )
    assert generate_ref_for(one) == generate_ref_for(two) == make_content_ref("csch", f"csch:definition_hash={h}")
    assert generate_ref_for(other) != generate_ref_for(one)


def test_a_scheme_with_no_hash_yet_gets_an_opaque_ref_rather_than_a_shared_one():
    from app.services.public_refs import generate_ref_for

    a = CompositeScheme(kind=CompositeSchemeKind.named_method, name="x")
    b = CompositeScheme(kind=CompositeSchemeKind.named_method, name="x")
    assert generate_ref_for(a).startswith("csch_") and generate_ref_for(a) != generate_ref_for(b)


# ---------------------------------------------------------------------------
# Resolution binds
# ---------------------------------------------------------------------------


def test_resolving_a_named_method_binds_it_without_touching_its_hash_or_ref(db_session):
    ref = LevelOfTheoryRef(method="CBS-QB3")
    lot = resolve_level_of_theory_ref(db_session, ref)

    assert lot.lot_hash == _level_of_theory_hash(ref)
    assert lot.public_ref == make_content_ref("lot", f"lot_hash:{lot.lot_hash}")
    binding = _binding(db_session, lot)
    assert binding is not None
    assert binding.binding_source is CompositeBindingSource.named_method_catalogue
    scheme = db_session.get(CompositeScheme, binding.scheme_id)
    assert scheme.kind is CompositeSchemeKind.named_method
    assert scheme.name == "CBS-QB3"
    assert scheme.definition_hash == named_method_definition_hash("cbs-qb3")
    assert scheme.public_ref == make_content_ref("csch", f"csch:definition_hash={scheme.definition_hash}")
    assert scheme.public_ref.startswith("csch_")


def test_the_scheme_states_what_the_catalogue_states_and_nothing_else(db_session):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    scheme = db_session.get(CompositeScheme, _binding(db_session, lot).scheme_id)
    entry = composite_methods.BY_KEY["cbs-qb3"]

    assert scheme.recipe_zpe_scale_factor == entry.recipe_zpe_scale_factor == 0.99
    geometry = db_session.get(LevelOfTheory, scheme.geometry_level_of_theory_id)
    frequency = db_session.get(LevelOfTheory, scheme.frequency_level_of_theory_id)
    assert (geometry.method, geometry.basis) == ("B3LYP", "CBSB7")
    assert (frequency.method, frequency.basis) == ("B3LYP", "CBSB7")
    # No term list in the catalogue, so none is invented; no literature row either.
    assert scheme.terms == []
    assert scheme.source_literature_id is None
    assert "10.1063/1.477924" in scheme.note
    # The internal levels are ordinary: never bound.
    assert _binding(db_session, geometry) is None


def test_an_entry_that_states_no_internal_level_leaves_the_columns_null(db_session):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="W1U"))
    scheme = db_session.get(CompositeScheme, _binding(db_session, lot).scheme_id)
    assert scheme.geometry_level_of_theory_id is None
    assert scheme.frequency_level_of_theory_id is None
    assert scheme.recipe_zpe_scale_factor is None


@pytest.mark.parametrize(
    "spelling",
    ["CBS-QB3", "cbs-qb3", "cbsqb3", "CBSQB3", " cbs-qb3 "],
)
def test_alias_spellings_share_one_level_one_binding_one_scheme(db_session, spelling):
    first = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    again = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=spelling))

    assert again.id == first.id
    assert _count(db_session, LevelOfTheoryComposite) == 1
    assert _count(db_session, CompositeScheme) == 1


@pytest.mark.parametrize(
    ("alias", "key"),
    [("g4(mp2)", "g4mp2"), ("G3(MP2)B3", "g3mp2b3"), ("rocbsqb3", "rocbs-qb3"), ("cbsapno", "cbs-apno")],
)
def test_alias_spellings_bind_to_the_scheme_of_the_key(db_session, alias, key):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=alias))
    scheme = db_session.get(CompositeScheme, _binding(db_session, lot).scheme_id)
    assert scheme.definition_hash == named_method_definition_hash(key)


def test_resolution_is_idempotent(db_session):
    ref = LevelOfTheoryRef(method="G4")
    a = resolve_level_of_theory_ref(db_session, ref)
    b = resolve_level_of_theory_ref(db_session, ref)
    c = resolve_level_of_theory_ref(db_session, ref)
    assert a.id == b.id == c.id
    assert _count(db_session, LevelOfTheoryComposite) == 1
    assert _count(db_session, CompositeScheme) == 1


def test_two_levels_with_the_same_method_share_the_scheme(db_session):
    a = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    b = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3", solvent="water"))
    assert a.id != b.id
    assert _binding(db_session, a).scheme_id == _binding(db_session, b).scheme_id
    assert _count(db_session, LevelOfTheoryComposite) == 2


@pytest.mark.parametrize(
    ("method", "basis"),
    [
        ("B3LYP", "def2-tzvp"),
        ("wb97xd", "def2tzvp"),
        ("CCSD(T)", "cc-pVQZ+1"),
        # Correction-table names are not methods and are never aliased.
        ("cbs-qb3-paraskevas", None),
        ("cbsqb32023", None),
        # W2-2 is not W2.
        ("W2-2", None),
    ],
)
def test_an_unbound_method_stays_unbound(db_session, method, basis):
    schemes_before = _count(db_session, CompositeScheme)
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=method, basis=basis))
    assert _binding(db_session, lot) is None
    assert _count(db_session, CompositeScheme) == schemes_before
    assert _count(db_session, LevelOfTheoryComposite) == 0


def test_the_binding_does_not_change_the_level_row_or_its_hash(db_session):
    ref = LevelOfTheoryRef(method="CBS-QB3")
    before_hash = _level_of_theory_hash(ref)
    lot = resolve_level_of_theory_ref(db_session, ref)
    db_session.expire_all()
    stored = db_session.get(LevelOfTheory, lot.id)
    assert stored.lot_hash == before_hash
    assert (stored.method, stored.basis, stored.keywords) == ("CBS-QB3", None, None)


# ---------------------------------------------------------------------------
# Race safety: the loser of a get-or-create falls back to a select
# ---------------------------------------------------------------------------


def test_a_lost_scheme_race_falls_back_to_the_winners_row(db_session, monkeypatch):
    """The scheme exists but this caller did not see it when it looked.

    The insert then hits ``uq_composite_scheme_definition_hash``; the loser
    must end up with the winner's row, not an error and not a second row.
    """
    winner = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="G4"))
    winning_scheme_id = _binding(db_session, winner).scheme_id
    real = service._select_scheme
    calls = {"n": 0}

    def blind_first_time(session, definition_hash):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session, definition_hash)

    monkeypatch.setattr(service, "_select_scheme", blind_first_time)
    scheme = service.get_or_create_named_method_scheme(db_session, composite_methods.BY_KEY["g4"])

    assert scheme.id == winning_scheme_id
    assert calls["n"] == 2
    assert _count(db_session, CompositeScheme) == 1


def test_a_lost_binding_race_falls_back_to_the_winners_row(db_session, monkeypatch):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="G4"))
    winning = _binding(db_session, lot)
    winning_key = (winning.level_of_theory_id, winning.scheme_id)
    # A real loser never holds the winner's instance in its identity map.
    db_session.expunge(winning)
    real_get = db_session.get

    def blind_to_the_binding(entity, ident, *args, **kwargs):
        if entity is LevelOfTheoryComposite:
            return None
        return real_get(entity, ident, *args, **kwargs)

    monkeypatch.setattr(db_session, "get", blind_to_the_binding)
    binding = service.ensure_named_method_binding(db_session, lot)
    monkeypatch.undo()

    assert binding is not None
    assert (binding.level_of_theory_id, binding.scheme_id) == winning_key
    assert _count(db_session, LevelOfTheoryComposite) == 1


# ---------------------------------------------------------------------------
# Merged levels
# ---------------------------------------------------------------------------


def test_a_level_merged_into_another_is_followed_and_the_kept_row_is_bound(db_session):
    ref = LevelOfTheoryRef(method="CBS-QB3", basis="zz")
    merged = LevelOfTheory(method="CBS-QB3", basis="zz", lot_hash=_level_of_theory_hash(ref))
    kept = LevelOfTheory(method="cbs-qb3", basis="zz-kept", lot_hash="e" * 64)
    db_session.add_all([merged, kept])
    db_session.flush()
    db_session.add(LevelOfTheoryMerge(merged_lot_id=merged.id, into_lot_id=kept.id))
    db_session.flush()

    resolved = resolve_level_of_theory_ref(db_session, ref)

    assert resolved.id == kept.id
    assert _binding(db_session, kept) is not None
    assert _binding(db_session, merged) is None


# ---------------------------------------------------------------------------
# Input levels are ordinary
# ---------------------------------------------------------------------------


def _scheme_with_term(session) -> CompositeSchemeTerm:
    scheme = CompositeScheme(
        kind=CompositeSchemeKind.extrapolation,
        name="test extrapolation",
        definition_hash=hashlib.sha256(b"test-scheme").hexdigest(),
    )
    session.add(scheme)
    session.flush()
    term = CompositeSchemeTerm(
        scheme_id=scheme.id,
        position=0,
        operation=CompositeTermOperation.base,
        energy_component=EnergyComponentKind.total,
    )
    session.add(term)
    session.flush()
    return term


def test_an_ordinary_level_is_accepted_as_a_term_input(db_session):
    term = _scheme_with_term(db_session)
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ"))
    row = add_scheme_term_input(db_session, term, slot=CompositeInputSlot.value, level_of_theory_id=lot.id)
    assert row.level_of_theory_id == lot.id


def test_a_bound_level_is_refused_as_a_term_input(db_session):
    term = _scheme_with_term(db_session)
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    assert _binding(db_session, lot) is not None
    with pytest.raises(CompositeInputLevelError):
        add_scheme_term_input(db_session, term, slot=CompositeInputSlot.value, level_of_theory_id=lot.id)
    assert _count(db_session, CompositeSchemeTermInput) == 0


def test_a_catalogued_level_with_no_binding_yet_is_refused_too(db_session):
    """Bindings are lazy: a ``CBS-QB3`` level that was never resolved has none."""
    term = _scheme_with_term(db_session)
    raw = LevelOfTheory(method="CBS-QB3", lot_hash="d" * 64)
    db_session.add(raw)
    db_session.flush()
    assert _binding(db_session, raw) is None
    with pytest.raises(CompositeInputLevelError):
        add_scheme_term_input(db_session, term, slot=CompositeInputSlot.value, level_of_theory_id=raw.id)


def test_a_level_merged_into_a_composite_level_is_refused(db_session):
    term = _scheme_with_term(db_session)
    kept = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    merged = LevelOfTheory(method="b3lyp", basis="was-merged", lot_hash="c" * 64)
    db_session.add(merged)
    db_session.flush()
    db_session.add(LevelOfTheoryMerge(merged_lot_id=merged.id, into_lot_id=kept.id))
    db_session.flush()
    with pytest.raises(CompositeInputLevelError):
        assert_ordinary_input_level(db_session, merged.id)
    with pytest.raises(CompositeInputLevelError):
        add_scheme_term_input(db_session, term, slot=CompositeInputSlot.value, level_of_theory_id=merged.id)


def test_a_term_input_names_the_kept_row_when_its_level_was_merged(db_session):
    term = _scheme_with_term(db_session)
    kept = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVQZ"))
    merged = LevelOfTheory(method="ccsd(t)", basis="ccpvqz-old", lot_hash="b" * 64)
    db_session.add(merged)
    db_session.flush()
    db_session.add(LevelOfTheoryMerge(merged_lot_id=merged.id, into_lot_id=kept.id))
    db_session.flush()
    row = add_scheme_term_input(db_session, term, slot=CompositeInputSlot.value, level_of_theory_id=merged.id)
    assert row.level_of_theory_id == kept.id


def test_an_unknown_level_is_a_lookup_error(db_session):
    with pytest.raises(LookupError):
        assert_ordinary_input_level(db_session, 987654321)


# ---------------------------------------------------------------------------
# The database's own checks
# ---------------------------------------------------------------------------


def _fails(db_session, statement: str, **params) -> None:
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(text(statement), params)


def test_database_refuses_a_formula_on_a_non_extrapolation_term(db_session):
    term = _scheme_with_term(db_session)
    _fails(
        db_session,
        "INSERT INTO composite_scheme_term (scheme_id, position, operation, energy_component, formula) "
        "VALUES (:s, 5, CAST('difference' AS composite_term_operation), "
        "CAST('total' AS energy_component_kind), CAST('inverse_power' AS composite_extrapolation_formula))",
        s=term.scheme_id,
    )


def test_database_refuses_an_exponent_without_a_formula(db_session):
    term = _scheme_with_term(db_session)
    _fails(
        db_session,
        "INSERT INTO composite_scheme_term (scheme_id, position, operation, energy_component, exponent) "
        "VALUES (:s, 5, CAST('extrapolation' AS composite_term_operation), "
        "CAST('total' AS energy_component_kind), 3.0)",
        s=term.scheme_id,
    )


def test_database_refuses_a_repeated_position(db_session):
    term = _scheme_with_term(db_session)
    _fails(
        db_session,
        "INSERT INTO composite_scheme_term (scheme_id, position, operation, energy_component) "
        "VALUES (:s, 0, CAST('value' AS composite_term_operation), CAST('total' AS energy_component_kind))",
        s=term.scheme_id,
    )


def test_database_refuses_a_cardinal_slot_without_a_cardinal_number(db_session):
    term = _scheme_with_term(db_session)
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ"))
    _fails(
        db_session,
        "INSERT INTO composite_scheme_term_input (term_id, slot, level_of_theory_id) "
        "VALUES (:t, CAST('cardinal' AS composite_input_slot), :l)",
        t=term.id,
        l=lot.id,
    )


def test_database_allows_two_cardinal_inputs_that_differ_in_cardinal_number(db_session):
    term = _scheme_with_term(db_session)
    tz = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ"))
    qz = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVQZ"))
    add_scheme_term_input(
        db_session, term, slot=CompositeInputSlot.cardinal, level_of_theory_id=tz.id, cardinal_number=3
    )
    add_scheme_term_input(
        db_session, term, slot=CompositeInputSlot.cardinal, level_of_theory_id=qz.id, cardinal_number=4
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_scheme_term_input(
                db_session, term, slot=CompositeInputSlot.cardinal, level_of_theory_id=qz.id, cardinal_number=4
            )


def test_database_refuses_a_second_scheme_with_one_definition_hash(db_session):
    resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="G4"))
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                CompositeScheme(
                    kind=CompositeSchemeKind.named_method,
                    name="duplicate G4",
                    definition_hash=named_method_definition_hash("g4"),
                )
            )
            db_session.flush()


# ---------------------------------------------------------------------------
# The migration's frozen copy agrees with the application
# ---------------------------------------------------------------------------


def _migration():
    spec = importlib.util.spec_from_file_location("_mig_d7a3_unit", _MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_catalogue_copy_agrees_with_the_catalogue():
    mig = _migration()
    assert set(mig._NAMED_METHODS) == set(composite_methods.BY_KEY)
    for key, (name, geometry, frequency, zpe, doi) in mig._NAMED_METHODS.items():
        entry = composite_methods.BY_KEY[key]
        assert name == entry.name, key
        assert geometry == ((entry.geometry_level.method, entry.geometry_level.basis) if entry.geometry_level else None)
        assert frequency == (
            (entry.frequency_level.method, entry.frequency_level.basis) if entry.frequency_level else None
        )
        assert zpe == entry.recipe_zpe_scale_factor, key
        assert doi == entry.paper_doi, key


def test_migration_hash_and_ref_formulas_agree_with_the_application():
    mig = _migration()
    for entry in composite_methods.CATALOGUE:
        assert mig._canonical_json(entry.key) == named_method_canonical_json(entry.key)
        assert mig._definition_hash(entry.key) == named_method_definition_hash(entry.key)
        h = named_method_definition_hash(entry.key)
        assert mig._content_ref("csch", f"csch:definition_hash={h}") == make_content_ref(
            "csch", f"csch:definition_hash={h}"
        )
        for level in (entry.geometry_level, entry.frequency_level):
            if level is None:
                continue
            assert mig._internal_level_hash(level.method, level.basis) == _level_of_theory_hash(
                LevelOfTheoryRef(method=level.method, basis=level.basis)
            )


def test_migration_alias_copy_reaches_the_same_key_as_the_application():
    mig = _migration()
    for spelling in (
        "cbsqb3", "CBS-QB3", "rocbsqb3", "cbs4m", "cbsapno", "g4(mp2)", "G3(MP2)", "g3(mp2)b3",
        "W1U", "w1-bd", "cbs-qb3-paraskevas", "B3LYP",
    ):
        in_catalogue = composite_methods.composite_method_for(spelling)
        assert (mig._method_key(spelling) in mig._NAMED_METHODS) == (in_catalogue is not None), spelling
        if in_catalogue is not None:
            assert mig._method_key(spelling) == in_catalogue.key


def test_internal_levels_are_never_composite():
    for entry in composite_methods.CATALOGUE:
        for level in (entry.geometry_level, entry.frequency_level):
            if level is not None:
                assert composite_methods.composite_method_for(level.method) is None, entry.key


def test_the_canonical_json_is_valid_json_of_exactly_two_members():
    payload = json.loads(named_method_canonical_json("cbs-qb3"))
    assert payload == {"kind": "named_method", "method": "cbs-qb3"}
