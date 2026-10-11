"""Declared statmech models: additive migration, owner rules as database facts, and the accepted-science freeze.

The migration round-trips inside the test transaction (PostgreSQL DDL is transactional). A statmech record stored before
the revision keeps its row, its review and its digest, and reads as declaring no model. A declaration, its members and its
sources are the declaring record's scientific content: frozen with it once it has been approved, and not frozen by the
approval of a record that is merely cited as a member.
"""

from __future__ import annotations

import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.base import Base
from app.db.models.common import (
    CalculationType,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    StructureDeterminationTargetKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.db.models.structure_determination import StructureDetermination
from tests.db._migration_chain import revision_under_test
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_output_geometry,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_geometry,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_REVISION = revision_under_test("e6b3a9d1f742")
_MIGRATION = Path(__file__).resolve().parents[2] / "alembic/versions/e6b3a9d1f742_declare_statmech_models.py"
TABLES = ("statmech_model_declaration", "statmech_model_member", "statmech_model_source")
ENUMS = ("statmech_model_scope_kind", "statmech_model_source_role")


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("statmech_model_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return module


def _present(db_session, table: str) -> bool:
    return bool(
        db_session.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_name = :t"), {"t": table})
    )


def _entry(db_session):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))


def _ts_entry(db_session):
    reactant = make_species(db_session, inchi_key=next_inchi_key())
    product = make_species(db_session, inchi_key=next_inchi_key())
    reactant_entry, product_entry = make_species_entry(db_session, reactant), make_species_entry(db_session, product)
    reaction = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        db_session, reaction=reaction, reactant_entries=[reactant_entry], product_entries=[product_entry]
    )
    return make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=entry))


def _declaration(db_session, statmech, scope=StatmechModelScopeKind.atom, **kw) -> StatmechModelDeclaration:
    values = {
        "statmech_id": statmech.id,
        "schema_version": 1,
        "scope_kind": scope,
        "literature_id": make_literature(db_session).id,
        "recipe": {},
        "content_hash": hashlib.sha256(uuid4().bytes).hexdigest(),
    }
    values.update(kw)
    row = StatmechModelDeclaration(**values)
    db_session.add(row)
    db_session.flush()
    return row


def _refused(db_session, build, fragment: str | None = None, error=DBAPIError) -> None:
    with pytest.raises(error) as caught, db_session.begin_nested():
        build()
        db_session.flush()
    if fragment is not None:
        assert fragment in str(caught.value)


def _determination(db_session, ts_entry, *, kind=StructureDeterminationTargetKind.saddle_point) -> StructureDetermination:
    row = StructureDetermination(
        transition_state_entry_id=ts_entry.id,
        target_kind=kind,
        evaluated_geometry_id=make_geometry(db_session).id,
        literature_id=make_literature(db_session).id,
        determination_key="k-" + uuid4().hex[:8],
        identity_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        content_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    db_session.add(row)
    db_session.flush()
    return row


# --- migration -----------------------------------------------------------------------------------------------------


def test_the_revision_chains_from_the_revision_before_it_in_the_chain():
    assert _REVISION.parent == "a7c3e9d1f5b2"


def test_the_migration_round_trips_and_leaves_an_existing_record_untouched(db_session, migration):
    entry = _entry(db_session)
    legacy = make_statmech(db_session, species_entry=entry)
    before = db_session.scalar(text("SELECT count(*) FROM statmech WHERE id = :i"), {"i": legacy.id})
    assert all(_present(db_session, t) for t in TABLES)

    migration.downgrade()
    assert not any(_present(db_session, t) for t in TABLES)
    for enum in ENUMS:
        assert not db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname = :n"), {"n": enum})
    assert db_session.scalar(text("SELECT count(*) FROM statmech WHERE id = :i"), {"i": legacy.id}) == before

    migration.upgrade()
    assert all(_present(db_session, t) for t in TABLES)
    assert db_session.scalar(text("SELECT count(*) FROM statmech WHERE id = :i"), {"i": legacy.id}) == before
    assert db_session.scalar(text("SELECT count(*) FROM statmech_model_declaration")) == 0  # nothing is backfilled


def test_downgrade_forgets_declarations_and_says_how_many(db_session, migration, capsys):
    _declaration(db_session, make_statmech(db_session, species_entry=_entry(db_session)))
    migration.downgrade()
    assert "forgets: 1 statmech model declaration(s)" in capsys.readouterr().out


# --- declaration rules ---------------------------------------------------------------------------------------------


def test_a_record_declares_at_most_one_model(db_session):
    statmech = make_statmech(db_session, species_entry=_entry(db_session))
    _declaration(db_session, statmech)
    _refused(db_session, lambda: _declaration(db_session, statmech), error=IntegrityError)


def test_scope_and_pointers_agree_and_attribution_is_required(db_session):
    entry = _entry(db_session)
    group = make_conformer_group(db_session, entry)
    sm = make_statmech(db_session, species_entry=entry)
    _refused(db_session, lambda: _declaration(db_session, sm, StatmechModelScopeKind.basin), error=IntegrityError)
    sm2 = make_statmech(db_session, species_entry=entry)
    _refused(
        db_session, lambda: _declaration(db_session, sm2, StatmechModelScopeKind.atom, conformer_group_id=group.id), error=IntegrityError
    )
    sm3 = make_statmech(db_session, species_entry=entry)
    _refused(db_session, lambda: _declaration(db_session, sm3, literature_id=None), error=IntegrityError)
    sm4 = make_statmech(db_session, species_entry=entry)
    assert _declaration(db_session, sm4, StatmechModelScopeKind.basin, conformer_group_id=group.id).id


def test_a_basin_is_a_group_of_the_records_own_species_entry(db_session):
    entry, other = _entry(db_session), _entry(db_session)
    foreign_group = make_conformer_group(db_session, other)
    sm = make_statmech(db_session, species_entry=entry)
    _refused(
        db_session,
        lambda: _declaration(db_session, sm, StatmechModelScopeKind.basin, conformer_group_id=foreign_group.id),
        "statmech_model_declaration_owner",
    )


def test_a_ts_scope_names_a_saddle_determination_of_the_records_own_ts_entry(db_session):
    ts_entry, other_ts = _ts_entry(db_session), _ts_entry(db_session)
    host = Statmech(transition_state_entry_id=ts_entry.id, scientific_origin="computed")
    db_session.add(host)
    db_session.flush()
    foreign = _determination(db_session, other_ts)
    _refused(
        db_session,
        lambda: _declaration(db_session, host, StatmechModelScopeKind.ts_determination, structure_determination_id=foreign.id),
        "statmech_model_declaration_owner",
    )
    geometry_only = _determination(db_session, ts_entry, kind=StructureDeterminationTargetKind.geometry)
    _refused(
        db_session,
        lambda: _declaration(
            db_session, host, StatmechModelScopeKind.ts_determination, structure_determination_id=geometry_only.id
        ),
        "statmech_model_declaration_owner",
    )
    own = _determination(db_session, ts_entry)
    assert _declaration(db_session, host, StatmechModelScopeKind.ts_determination, structure_determination_id=own.id).id


def test_a_species_owned_record_cannot_declare_a_ts_scope_and_a_ts_owned_one_not_an_atom(db_session):
    ts_entry = _ts_entry(db_session)
    determination = _determination(db_session, ts_entry)
    species_host = make_statmech(db_session, species_entry=_entry(db_session))
    _refused(
        db_session,
        lambda: _declaration(
            db_session, species_host, StatmechModelScopeKind.ts_determination, structure_determination_id=determination.id
        ),
        "statmech_model_declaration_owner",
    )
    ts_host = Statmech(transition_state_entry_id=ts_entry.id, scientific_origin="computed")
    db_session.add(ts_host)
    db_session.flush()
    _refused(db_session, lambda: _declaration(db_session, ts_host, StatmechModelScopeKind.atom), "statmech_model_declaration_owner")


# --- members -------------------------------------------------------------------------------------------------------


def _member(db_session, declaration, member_statmech, key="m1", **kw) -> StatmechModelMember:
    row = StatmechModelMember(declaration_id=declaration.id, member_key=key, member_statmech_id=member_statmech.id, **kw)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def ensemble(db_session):
    entry = _entry(db_session)
    host = make_statmech(db_session, species_entry=entry)
    declaration = _declaration(db_session, host, StatmechModelScopeKind.explicit_equilibrium_ensemble)
    return entry, host, declaration


def test_a_member_is_another_record_of_the_same_species_entry_never_the_declaring_one(db_session, ensemble):
    entry, host, declaration = ensemble
    sibling = make_statmech(db_session, species_entry=entry)
    foreign = make_statmech(db_session, species_entry=_entry(db_session))
    _refused(db_session, lambda: _member(db_session, declaration, foreign), "statmech_model_member_owner")
    _refused(db_session, lambda: _member(db_session, declaration, host), "statmech_model_member_self")
    assert _member(db_session, declaration, sibling, counting_multiplicity=None).counting_multiplicity is None  # not one


def test_only_an_ensemble_has_members(db_session):
    entry = _entry(db_session)
    atom_declaration = _declaration(db_session, make_statmech(db_session, species_entry=entry))
    sibling = make_statmech(db_session, species_entry=entry)
    _refused(db_session, lambda: _member(db_session, atom_declaration, sibling), "statmech_model_member_scope")


def test_a_basin_of_another_species_entry_is_not_a_members_basin(db_session, ensemble):
    entry, _, declaration = ensemble
    sibling = make_statmech(db_session, species_entry=entry)
    foreign_group = make_conformer_group(db_session, _entry(db_session))
    _refused(
        db_session, lambda: _member(db_session, declaration, sibling, conformer_group_id=foreign_group.id), "statmech_model_member_owner"
    )


def test_a_repeated_record_or_key_is_not_another_state_but_a_basin_may_repeat(db_session, ensemble):
    entry, _, declaration = ensemble
    a, b = make_statmech(db_session, species_entry=entry), make_statmech(db_session, species_entry=entry)
    group = make_conformer_group(db_session, entry)
    _member(db_session, declaration, a, "m1", conformer_group_id=group.id)
    _refused(db_session, lambda: _member(db_session, declaration, a, "m2"), error=IntegrityError)  # same record
    _refused(db_session, lambda: _member(db_session, declaration, b, "m1"), error=IntegrityError)  # same key
    # a second member in the same basin is allowed: one observation per basin is not a chemical uniqueness rule
    assert _member(db_session, declaration, b, "m3", conformer_group_id=group.id).conformer_group_id == group.id


def test_member_numbers_are_bounded_and_finite(db_session, ensemble):
    entry, _, declaration = ensemble
    s = make_statmech(db_session, species_entry=entry)
    _refused(db_session, lambda: _member(db_session, declaration, s, counting_multiplicity=0), error=IntegrityError)
    _refused(db_session, lambda: _member(db_session, declaration, s, relative_energy_kj_mol=float("nan")), error=IntegrityError)
    _refused(db_session, lambda: _member(db_session, declaration, s, relative_energy_kj_mol=float("inf")), error=IntegrityError)
    assert _member(db_session, declaration, s, relative_energy_kj_mol=-3.5).relative_energy_kj_mol == -3.5


# --- sources -------------------------------------------------------------------------------------------------------


def _source(db_session, declaration, key="s1", role=StatmechModelSourceRole.curvature, **kw) -> StatmechModelSource:
    row = StatmechModelSource(declaration_id=declaration.id, source_key=key, role=role, **kw)
    db_session.add(row)
    db_session.flush()
    return row


def test_a_source_names_exactly_one_locator_and_the_scale_role_matches(db_session):
    entry = _entry(db_session)
    declaration = _declaration(db_session, make_statmech(db_session, species_entry=entry))
    calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    lit = make_literature(db_session)
    _refused(db_session, lambda: _source(db_session, declaration), error=IntegrityError)  # no locator
    _refused(
        db_session, lambda: _source(db_session, declaration, calculation_id=calc.id, literature_id=lit.id), error=IntegrityError
    )
    _refused(
        db_session,
        lambda: _source(db_session, declaration, role=StatmechModelSourceRole.scale_factor, calculation_id=calc.id),
        error=IntegrityError,
    )
    assert _source(db_session, declaration, "s2", literature_id=lit.id).id  # imported parameters need no calculation
    _refused(db_session, lambda: _source(db_session, declaration, "s3", literature_id=lit.id, geometry_id=make_geometry(db_session).id), error=IntegrityError)


def test_a_source_calculation_belongs_to_the_declaring_subject_and_a_stated_geometry_to_the_calculation(db_session):
    entry = _entry(db_session)
    declaration = _declaration(db_session, make_statmech(db_session, species_entry=entry))
    own = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    foreign = make_calculation(db_session, type=CalculationType.opt, species_entry_id=_entry(db_session).id)
    _refused(db_session, lambda: _source(db_session, declaration, calculation_id=foreign.id), "statmech_model_source_owner")
    geometry = make_geometry(db_session)
    _refused(
        db_session, lambda: _source(db_session, declaration, calculation_id=own.id, geometry_id=geometry.id), "statmech_model_source_geometry"
    )
    attach_output_geometry(db_session, calculation=own, geometry=geometry)
    assert _source(db_session, declaration, calculation_id=own.id, geometry_id=geometry.id).geometry_id == geometry.id
    scan = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    other_geometry = make_geometry(db_session)
    attach_input_geometry(db_session, calculation=scan, geometry=other_geometry)
    assert _source(db_session, declaration, "s2", calculation_id=scan.id, geometry_id=other_geometry.id).id


def test_a_member_scoped_source_names_a_member_of_the_same_declaration(db_session, ensemble):
    entry, _, declaration = ensemble
    member = _member(db_session, declaration, make_statmech(db_session, species_entry=entry), "m1")
    lit = make_literature(db_session)
    assert _source(db_session, declaration, "s1", literature_id=lit.id, member_key=member.member_key).id
    _refused(db_session, lambda: _source(db_session, declaration, "s2", literature_id=lit.id, member_key="nope"), error=IntegrityError)


# --- accepted-science freeze -----------------------------------------------------------------------------------------


@pytest.fixture
def full(db_session):
    entry = _entry(db_session)
    host = make_statmech(db_session, species_entry=entry)
    declaration = _declaration(db_session, host, StatmechModelScopeKind.explicit_equilibrium_ensemble)
    sibling = make_statmech(db_session, species_entry=entry)
    member = _member(db_session, declaration, sibling)
    source = _source(db_session, declaration, literature_id=make_literature(db_session).id)
    return host, sibling, declaration, member, source


def test_approving_the_declaring_record_freezes_its_declaration_members_and_sources(db_session, full):
    host, sibling, declaration, member, source = full
    _approve(db_session, SubmissionRecordType.statmech, host.id, _actor(db_session))

    _refused(db_session, lambda: setattr(declaration, "recipe", {"changed": True}), "is immutable")
    _refused(db_session, lambda: setattr(member, "counting_multiplicity", 2), "is immutable")
    _refused(db_session, lambda: setattr(source, "source_key", "renamed"), "is immutable")
    _refused(db_session, lambda: db_session.delete(member), "is immutable")
    _refused(db_session, lambda: db_session.delete(source), "is immutable")
    _refused(db_session, lambda: db_session.delete(declaration), "is immutable")
    # nothing may be added to what was accepted
    _refused(db_session, lambda: _source(db_session, declaration, "late", literature_id=make_literature(db_session).id), "is immutable")
    _refused(db_session, lambda: _member(db_session, declaration, make_statmech(db_session, species_entry=sibling.species_entry), "late"), "is immutable")


def test_a_record_with_no_declaration_cannot_gain_one_after_it_is_accepted(db_session):
    host = make_statmech(db_session, species_entry=_entry(db_session))
    _approve(db_session, SubmissionRecordType.statmech, host.id, _actor(db_session))
    _refused(db_session, lambda: _declaration(db_session, host), "is immutable")


def test_approving_a_cited_member_does_not_freeze_the_ensemble_that_cites_it(db_session, full):
    host, sibling, declaration, member, source = full
    _approve(db_session, SubmissionRecordType.statmech, sibling.id, _actor(db_session))
    # the ensemble's own record is still unapproved: its rows can still be added to and withdrawn whole
    late = _source(db_session, declaration, "late", literature_id=make_literature(db_session).id)
    db_session.delete(late)
    db_session.delete(member)
    db_session.flush()
    assert db_session.get(StatmechModelDeclaration, declaration.id) is not None


def test_an_unapproved_declaration_is_still_never_edited_and_truncate_is_refused(db_session, full):
    _, _, declaration, member, source = full
    for edit in (
        lambda: setattr(declaration, "recipe", {"edited": True}),
        lambda: setattr(declaration, "content_hash", "f" * 64),
        lambda: setattr(member, "region_key", "r1"),
        lambda: setattr(member, "counting_multiplicity", 2),
        lambda: setattr(source, "source_key", "renamed"),
    ):
        _refused(db_session, edit, "immutable declared model")
    for table in TABLES:
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(text(f"TRUNCATE {table} CASCADE"))


# --- the record's digest ---------------------------------------------------------------------------------------------


def test_a_declaration_enters_the_reproducibility_snapshot_only_when_there_is_one(db_session, full):
    from app.services.reproducibility_rubric import _target_snapshot

    host, _, declaration, member, source = full
    legacy = make_statmech(db_session, species_entry=_entry(db_session))
    legacy_snapshot = _target_snapshot(legacy, SubmissionRecordType.statmech)
    assert "model_declaration" not in legacy_snapshot["relationships"]  # a record with none keeps the digest it had

    db_session.refresh(host)
    before = _target_snapshot(host, SubmissionRecordType.statmech)["relationships"]["model_declaration"]
    assert before["columns"]["scope_kind"] == "explicit_equilibrium_ensemble"
    assert [m["member_key"] for m in before["members"]] == ["m1"] and [s["source_key"] for s in before["sources"]] == ["s1"]
    db_session.delete(member)
    db_session.flush()
    _member(db_session, declaration, member.member_statmech, "m1", counting_multiplicity=3)
    db_session.refresh(declaration)
    after = _target_snapshot(host, SubmissionRecordType.statmech)["relationships"]["model_declaration"]
    assert after != before  # what the declaration says is part of what the record's digest rests on


# --- an edit is refused, not just a bad insert -----------------------------------------------------------------------


def test_a_member_cannot_be_repointed_at_another_species_record_by_an_update(db_session, full):
    """The owner guard runs on INSERT; an UPDATE must not be a way around it, and must not leave a stale checksum."""
    _, _, declaration, member, _ = full
    foreign = make_statmech(db_session, species_entry=_entry(db_session))
    _refused(db_session, lambda: setattr(member, "member_statmech_id", foreign.id), "immutable declared model")
    db_session.refresh(member)
    assert member.member_statmech_id != foreign.id


def test_a_source_calculation_cannot_be_swapped_by_an_update(db_session):
    entry = _entry(db_session)
    declaration = _declaration(db_session, make_statmech(db_session, species_entry=entry))
    own = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    foreign = make_calculation(db_session, type=CalculationType.opt, species_entry_id=_entry(db_session).id)
    source = _source(db_session, declaration, calculation_id=own.id)
    _refused(db_session, lambda: setattr(source, "calculation_id", foreign.id), "immutable declared model")
