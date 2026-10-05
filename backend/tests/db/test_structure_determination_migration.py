"""Structure determinations, sources, findings and the actual-protocol column: additive, legacy rows untouched.

Drives the revision's own ``upgrade``/``downgrade`` inside the test transaction (PostgreSQL DDL is
transactional, so the fixture's rollback restores the schema). A calculation stored before the revision must read
NULL, keep the digests stored reviews are keyed on and stay frozen if it was approved. A determination and its
sources are immutable from creation, a finding is append-only, and the checks and composite keys are the
database's own, not the writer's.
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
from app.db.models.calculation import Calculation
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.consistency.core import snapshot
from app.services.reproducibility_rubric import _mapped_columns
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/d3a8f6c1b952_declare_structure_determination.py"
)
TABLES = ("structure_determination", "structure_determination_source", "structure_evidence_finding")
ENUMS = (
    "structure_determination_target_kind",
    "structure_determination_quantity",
    "structure_source_role",
    "structure_finding_kind",
    "structure_finding_scope",
    "structure_finding_verdict",
    "structure_finding_authority",
)


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("structure_determination_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return module


def _tables(db_session) -> set[str]:
    return set(
        db_session.scalars(text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:t)"), {"t": list(TABLES)})
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


def _insert_determination(db_session, **overrides) -> int:
    values = {
        "ref": "sdet_" + uuid4().hex[:26],
        "species": None,
        "ts": None,
        "obs": None,
        "kind": "geometry",
        "quantity": None,
        "geometry": None,
        "lit": None,
        "key": "k",
        "convention": None,
        "ident": hashlib.sha256(uuid4().bytes).hexdigest(),
        "content": hashlib.sha256(uuid4().bytes).hexdigest(),
    }
    values.update(overrides)
    if values["geometry"] is None:
        values["geometry"] = make_geometry(db_session).id
    if values["lit"] is None:
        values["lit"] = make_literature(db_session).id
    return db_session.scalar(
        text(
            "INSERT INTO structure_determination (public_ref, species_entry_id, transition_state_entry_id, "
            "conformer_observation_id, target_kind, quantity, evaluated_geometry_id, literature_id, determination_key, "
            "energy_convention, identity_hash, content_hash) VALUES (:ref, :species, :ts, :obs, "
            "CAST(:kind AS structure_determination_target_kind), CAST(:quantity AS structure_determination_quantity), "
            ":geometry, :lit, :key, CAST(:convention AS jsonb), :ident, :content) RETURNING id"
        ),
        values,
    )


def _insert_source(
    db_session, determination_id: int, calculation: Calculation, *, role="energy", writing=True, **overrides
) -> int:
    """A source row, written the way the write path writes one: under the creation marker (``writing=False`` is a
    direct insert)."""
    values = {
        "det": determination_id,
        "role": role,
        "calc": calculation.id,
        "species": calculation.species_entry_id,
        "ts": calculation.transition_state_entry_id,
        "obs": None,
    }
    values.update(overrides)
    if writing:
        db_session.execute(
            text("SELECT set_config('tckdb.structure_determination_writing', :id, true)"), {"id": str(determination_id)}
        )
    # No ``finally``: when the insert is refused the caller's savepoint is rolled back, which also takes back the
    # transaction-local marker, and a statement run here would only fail on the aborted transaction and mask the
    # refusal being tested.
    result = db_session.scalar(
        text(
            "INSERT INTO structure_determination_source (determination_id, role, calculation_id, species_entry_id, "
            "transition_state_entry_id, conformer_observation_id) "
            "VALUES (:det, CAST(:role AS structure_source_role), :calc, :species, :ts, :obs) RETURNING id"
        ),
        values,
    )
    if writing:
        db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
    return result


def _insert_finding(db_session, **overrides) -> int:
    values = {
        "ref": "sfnd_" + uuid4().hex[:26],
        "kind": "identity_incompatibility",
        "scope": "geometry",
        "geometry": None,
        "calc": None,
        "det": None,
        "role": None,
        "verdict": "invalidates",
        "authority": "producer_assertion",
        "rationale": "the stated identity does not match the structure",
        "version": 1,
        "supersedes": None,
    }
    values.update(overrides)
    if values["scope"] == "geometry" and values["geometry"] is None:
        values["geometry"] = make_geometry(db_session).id
    return db_session.scalar(
        text(
            "INSERT INTO structure_evidence_finding (public_ref, kind, scope, subject_geometry_id, "
            "subject_calculation_id, subject_determination_id, role, verdict, authority, rationale, semantic_version, "
            "supersedes_finding_id) VALUES (:ref, CAST(:kind AS structure_finding_kind), "
            "CAST(:scope AS structure_finding_scope), :geometry, :calc, :det, CAST(:role AS structure_source_role), "
            "CAST(:verdict AS structure_finding_verdict), CAST(:authority AS structure_finding_authority), "
            ":rationale, :version, :supersedes) RETURNING id"
        ),
        values,
    )


def _refuses(db_session, match: str, statement, *args, **kwargs):
    with pytest.raises((IntegrityError, DBAPIError), match=match), db_session.begin_nested():
        statement(db_session, *args, **kwargs)


# ---------------------------------------------------------------------------
# legacy rows and the round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_existing_calculations_stay_null_and_keep_their_digests(db_session, migration, approved):
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id)
    if approved:
        # Approval locks the row through the ORM, so it happens while the ORM's column exists. The downgrade
        # then upgrade is the deployment: ADD COLUMN fires no UPDATE trigger, so the accepted-science guard must
        # neither refuse it nor see a mutation.
        _approve(db_session, SubmissionRecordType.calculation, calc.id, _actor(db_session))
    calc_id = calc.id
    migration.downgrade()
    assert not _tables(db_session)
    migration.upgrade()

    assert db_session.scalar(
        text("SELECT actual_protocol_declaration FROM calculation WHERE id = :id"), {"id": calc_id}
    ) is None
    row = db_session.get(Calculation, calc_id)
    # Neither digest a stored review or assessment is keyed on moves: the column holds the value the row
    # implicitly had, so it stays out of both snapshots.
    assert "actual_protocol_declaration" not in snapshot(row)
    assert "actual_protocol_declaration" not in _mapped_columns(row)
    if approved:
        assert (
            db_session.scalar(
                text("SELECT status FROM record_review WHERE record_type = 'calculation' AND record_id = :id"),
                {"id": calc_id},
            )
            == RecordReviewStatus.approved.value
        )


def test_a_declared_protocol_does_enter_the_digests(db_session):
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id)
    calc.actual_protocol_declaration = {"version": 1, "source": {"origin": "producer_declared"}}
    db_session.flush()
    assert "actual_protocol_declaration" in snapshot(calc)
    assert "actual_protocol_declaration" in _mapped_columns(calc)


def test_upgrade_downgrade_upgrade_round_trips_with_rows(db_session, migration, capsys):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    calc.actual_protocol_declaration = {"version": 1, "source": {"origin": "producer_declared"}}
    det = _insert_determination(db_session, species=entry.id)
    _insert_source(db_session, det, calc)
    _insert_finding(db_session)
    calc_id = calc.id

    migration.downgrade()
    assert (
        "forgets: 1 structure determination(s), 1 source row(s), 1 finding(s) and the actual-protocol "
        "declaration on 1 calculation(s)" in capsys.readouterr().out
    )
    assert not _tables(db_session)
    for typename in ENUMS:
        assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname = :t"), {"t": typename}) == 0, typename
    # The calculation survives; only its declaration is forgotten.
    assert db_session.scalar(text("SELECT count(*) FROM calculation WHERE id = :id"), {"id": calc_id}) == 1

    migration.upgrade()
    assert _tables(db_session) == set(TABLES)
    assert db_session.scalar(text("SELECT actual_protocol_declaration FROM calculation WHERE id = :id"), {"id": calc_id}) is None


def test_downgrade_removes_exactly_what_upgrade_added(db_session, migration):
    migration.downgrade()
    for name in (
        "uq_calculation_scope_species",
        "uq_calculation_scope_ts",
        "uq_calculation_scope_observation",
        "ck_calculation_actual_protocol_declaration_versioned_object",
    ):
        assert db_session.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": name}) == 0, name
    for function in (
        "tckdb_structure_row_immutable",
        "tckdb_structure_finding_append_only",
        "tckdb_structure_determination_guard",
        "tckdb_structure_source_insert_guard",
        "tckdb_structure_source_delete_guard",
    ):
        assert db_session.scalar(text("SELECT count(*) FROM pg_proc WHERE proname = :n"), {"n": function}) == 0, function
    # The pre-existing calculation constraints survive.
    assert db_session.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_calculation_one_owner'")) == 1
    migration.upgrade()


def test_the_declaration_column_is_a_versioned_object(db_session):
    calc = make_calculation(db_session, species_entry_id=_entry(db_session).id)
    for bad in ('"a string"', "[]", '{"source": {}}', '{"version": "1"}'):
        with pytest.raises(IntegrityError, match="actual_protocol_declaration_versioned_object"), db_session.begin_nested():
            db_session.execute(
                text("UPDATE calculation SET actual_protocol_declaration = CAST(:v AS jsonb) WHERE id = :id"),
                {"v": bad, "id": calc.id},
            )


# ---------------------------------------------------------------------------
# the determination's checks
# ---------------------------------------------------------------------------


def test_a_determination_has_exactly_one_owner(db_session):
    entry, ts = _entry(db_session), _ts_entry(db_session)
    _refuses(db_session, "one_owner", _insert_determination)
    _refuses(db_session, "one_owner", _insert_determination, species=entry.id, ts=ts.id)
    assert _insert_determination(db_session, species=entry.id)
    assert _insert_determination(db_session, ts=ts.id)


def test_a_target_must_fit_its_owner(db_session):
    entry, ts = _entry(db_session), _ts_entry(db_session)
    group = make_conformer_group(db_session, entry)
    observation = make_conformer_observation(db_session, conformer_group=group)
    # A basin belongs to a species entry and names its observation.
    _refuses(db_session, "target_matches_owner", _insert_determination, species=entry.id, kind="conformer_basin")
    _refuses(db_session, "target_matches_owner", _insert_determination, ts=ts.id, kind="conformer_basin", obs=observation.id)
    assert _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=observation.id)
    # A saddle belongs to a transition state entry and names no observation.
    _refuses(db_session, "target_matches_owner", _insert_determination, species=entry.id, kind="saddle_point")
    assert _insert_determination(db_session, ts=ts.id, kind="saddle_point")
    # A bare geometry names no observation.
    _refuses(db_session, "target_matches_owner", _insert_determination, species=entry.id, obs=observation.id)


def test_an_energy_convention_goes_with_zero_kelvin_energy_exactly(db_session):
    entry = _entry(db_session)
    convention = '{"zero_point_treatment": "scaled_harmonic"}'
    _refuses(db_session, "convention_iff_zero_kelvin", _insert_determination, species=entry.id, quantity="zero_kelvin_energy")
    _refuses(db_session, "convention_iff_zero_kelvin", _insert_determination, species=entry.id, quantity="electronic_energy", convention=convention)
    # An evidence-only determination (NULL quantity) takes none: the coalesce keeps NULL from passing the check.
    _refuses(db_session, "convention_iff_zero_kelvin", _insert_determination, species=entry.id, convention=convention)
    assert _insert_determination(db_session, species=entry.id, quantity="zero_kelvin_energy", convention=convention)
    assert _insert_determination(db_session, species=entry.id, quantity="electronic_energy")
    assert _insert_determination(db_session, species=entry.id)


def test_a_determination_needs_a_source_attribution_a_bounded_key_and_hex_digests(db_session):
    entry = _entry(db_session)
    geometry = make_geometry(db_session).id
    with pytest.raises(IntegrityError, match="source_required"), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO structure_determination (public_ref, species_entry_id, target_kind, evaluated_geometry_id, "
                "determination_key, identity_hash, content_hash) VALUES (:r, :s, 'geometry', :g, 'k', :h, :h)"
            ),
            {"r": "sdet_" + uuid4().hex[:26], "s": entry.id, "g": geometry, "h": hashlib.sha256(b"x").hexdigest()},
        )
    _refuses(db_session, "key_bounded", _insert_determination, species=entry.id, key="  ")
    _refuses(db_session, "key_bounded", _insert_determination, species=entry.id, key="k" * 129)
    _refuses(db_session, "identity_hash_sha256_hex", _insert_determination, species=entry.id, ident="XYZ")
    _refuses(db_session, "content_hash_sha256_hex", _insert_determination, species=entry.id, content="0" * 63)


def test_one_identity_is_one_row(db_session):
    entry = _entry(db_session)
    ident = hashlib.sha256(b"same").hexdigest()
    _insert_determination(db_session, species=entry.id, ident=ident)
    _refuses(db_session, "identity_hash", _insert_determination, species=entry.id, ident=ident)


def test_an_owner_a_source_and_a_key_name_one_determination_even_where_columns_are_null(db_session):
    entry, other = _entry(db_session), _entry(db_session)
    literature = make_literature(db_session).id
    first = _insert_determination(db_session, species=entry.id, key="k", lit=literature)
    # The same owner, source and key again, whatever the content: a second row is refused (NULL owner and
    # observation columns count as equal, so a bare geometry claim and a saddle claim are not exempt).
    _refuses(db_session, "uq_structure_determination_key", _insert_determination, species=entry.id, key="k", lit=literature)
    _refuses(
        db_session, "uq_structure_determination_key", _insert_determination, species=entry.id, key="k", lit=literature,
        quantity="electronic_energy",
    )
    # Another key, another owner or another source is another determination.
    assert _insert_determination(db_session, species=entry.id, key="other", lit=literature)
    assert _insert_determination(db_session, species=other.id, key="k", lit=literature)
    assert _insert_determination(db_session, species=entry.id, key="k", lit=make_literature(db_session).id)
    assert first


def test_a_basin_names_an_observation_of_its_own_species_entry(db_session):
    entry, other = _entry(db_session), _entry(db_session)
    mine = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, entry))
    theirs = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, other))
    assert _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=mine.id)
    with pytest.raises((IntegrityError, DBAPIError), match="structure_determination_observation_owner"), db_session.begin_nested():
        _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=theirs.id)


def test_a_basin_source_is_a_calculation_anchored_to_that_observation(db_session):
    entry = _entry(db_session)
    group = make_conformer_group(db_session, entry)
    mine = make_conformer_observation(db_session, conformer_group=group)
    elsewhere = make_conformer_observation(db_session, conformer_group=group)
    det = _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=mine.id)
    anchored = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=mine.id)
    other = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=elsewhere.id)
    unanchored = make_calculation(db_session, species_entry_id=entry.id)
    assert _insert_source(db_session, det, anchored, obs=mine.id)
    for calc in (other, unanchored):
        with pytest.raises(IntegrityError, match="scope_calc_observation"), db_session.begin_nested():
            _insert_source(db_session, det, calc, obs=mine.id)
    # A source that does not repeat its determination's observation is refused, and so is one that names another.
    with pytest.raises((IntegrityError, DBAPIError), match="structure_determination_source_observation"), db_session.begin_nested():
        _insert_source(db_session, det, anchored, role="curvature", obs=None)
    with pytest.raises((IntegrityError, DBAPIError), match="structure_determination_source_observation"), db_session.begin_nested():
        _insert_source(db_session, det, other, role="curvature", obs=elsewhere.id)


# ---------------------------------------------------------------------------
# pinned at creation, and protected under an accepted parent
# ---------------------------------------------------------------------------


def test_a_source_is_pinned_when_its_determination_is_created_and_never_added_or_removed_afterwards(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    extra = make_calculation(db_session, species_entry_id=entry.id)
    det = _insert_determination(db_session, species=entry.id)
    with pytest.raises(DBAPIError, match="structure_determination_sources_are_pinned_at_creation"), db_session.begin_nested():
        _insert_source(db_session, det, calc, writing=False)
    # The marker names one determination; it does not open another.
    other = _insert_determination(db_session, species=entry.id)
    with pytest.raises(DBAPIError, match="structure_determination_sources_are_pinned_at_creation"), db_session.begin_nested():
        db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', :i, true)"), {"i": str(other)})
        _insert_source(db_session, det, calc, writing=False)
    source = _insert_source(db_session, det, calc)
    assert _insert_source(db_session, det, extra, role="curvature")
    with pytest.raises(DBAPIError, match="structure_determination_sources_are_pinned_at_creation"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_determination_source WHERE id = :i"), {"i": source})
    assert db_session.scalar(text("SELECT count(*) FROM structure_determination_source WHERE determination_id = :d"), {"d": det}) == 2


def _accept(db_session, record_type, record_id):
    _approve(db_session, record_type, record_id, _actor(db_session))


def test_nothing_can_be_added_changed_or_removed_under_an_accepted_transition_state_entry(db_session):
    ts = _ts_entry(db_session)
    calc = make_calculation(db_session, transition_state_entry_id=ts.id)
    det = _insert_determination(db_session, ts=ts.id, kind="saddle_point")
    _insert_source(db_session, det, calc)
    _accept(db_session, SubmissionRecordType.transition_state_entry, ts.id)
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        _insert_determination(db_session, ts=ts.id, kind="saddle_point", key="another")
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_determination WHERE id = :i"), {"i": det})
    other_calc = make_calculation(db_session, transition_state_entry_id=ts.id)
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        _insert_source(db_session, det, other_calc, role="curvature")
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_determination_source WHERE determination_id = :i"), {"i": det})
    assert db_session.scalar(text("SELECT count(*) FROM structure_determination WHERE id = :i"), {"i": det}) == 1


def test_nothing_can_be_added_under_an_accepted_conformer_observation(db_session):
    entry = _entry(db_session)
    obs = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, entry))
    calc = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=obs.id)
    det = _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=obs.id)
    _insert_source(db_session, det, calc, obs=obs.id)
    _accept(db_session, SubmissionRecordType.conformer_observation, obs.id)
    with pytest.raises(DBAPIError, match="accepted conformer_observation record .* is immutable"), db_session.begin_nested():
        _insert_determination(db_session, species=entry.id, kind="conformer_basin", obs=obs.id, key="another")
    with pytest.raises(DBAPIError, match="accepted conformer_observation record .* is immutable"), db_session.begin_nested():
        _insert_source(db_session, det, calc, role="curvature", obs=obs.id)


def test_an_accepted_calculation_may_still_be_cited_by_a_determination_of_an_unaccepted_owner(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    _accept(db_session, SubmissionRecordType.calculation, calc.id)
    det = _insert_determination(db_session, species=entry.id)
    assert _insert_source(db_session, det, calc)  # a source cites its calculation; it is not that calculation's child


def test_the_three_tables_cannot_be_truncated(db_session):
    for table in TABLES:
        with pytest.raises(DBAPIError, match="cannot be truncated"), db_session.begin_nested():
            db_session.execute(text(f"TRUNCATE {table} CASCADE"))


def test_a_finding_may_be_appended_about_accepted_science(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    _accept(db_session, SubmissionRecordType.calculation, calc.id)
    assert _insert_finding(db_session, scope="calculation", calc=calc.id)


# ---------------------------------------------------------------------------
# immutability, and the sources' composite keys
# ---------------------------------------------------------------------------


def test_a_determination_and_its_sources_cannot_be_updated(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    det = _insert_determination(db_session, species=entry.id, quantity="electronic_energy")
    source = _insert_source(db_session, det, calc)
    with pytest.raises(DBAPIError, match="structure_row_is_immutable"), db_session.begin_nested():
        db_session.execute(text("UPDATE structure_determination SET determination_key = 'other' WHERE id = :i"), {"i": det})
    with pytest.raises(DBAPIError, match="structure_row_is_immutable"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE structure_determination_source SET role = 'curvature' WHERE id = :i"), {"i": source}
        )
    assert db_session.scalar(text("SELECT determination_key FROM structure_determination WHERE id = :i"), {"i": det}) == "k"


def test_a_source_must_be_a_calculation_of_the_determinations_own_owner(db_session):
    entry, other = _entry(db_session), _entry(db_session)
    ts = _ts_entry(db_session)
    det = _insert_determination(db_session, species=entry.id)
    own = make_calculation(db_session, species_entry_id=entry.id)
    foreign = make_calculation(db_session, species_entry_id=other.id)
    ts_calc = make_calculation(db_session, transition_state_entry_id=ts.id)

    assert _insert_source(db_session, det, own)
    # A calculation of another species entry, stated with the determination's owner, is not that calculation's.
    with pytest.raises(IntegrityError, match="scope_calc_species"), db_session.begin_nested():
        _insert_source(db_session, det, foreign, species=entry.id, ts=None)
    # Stated with its own owner, it is not the determination's owner.
    with pytest.raises(IntegrityError, match="scope_det_species"), db_session.begin_nested():
        _insert_source(db_session, det, foreign)
    # A transition-state calculation under a species determination: neither owner column can satisfy both keys.
    with pytest.raises(IntegrityError, match="scope_det_ts"), db_session.begin_nested():
        _insert_source(db_session, det, ts_calc)
    with pytest.raises(IntegrityError, match="one_owner"), db_session.begin_nested():
        _insert_source(db_session, det, own, ts=ts.id)


def test_a_role_is_pinned_to_one_calculation_once_but_a_calculation_can_play_several(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    det = _insert_determination(db_session, species=entry.id)
    _insert_source(db_session, det, calc, role="energy")
    _insert_source(db_session, det, calc, role="geometry_optimization")
    with pytest.raises(IntegrityError, match="structure_determination_source_role"), db_session.begin_nested():
        _insert_source(db_session, det, calc, role="energy")


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


def test_a_finding_names_the_one_subject_its_scope_says(db_session):
    entry = _entry(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    det = _insert_determination(db_session, species=entry.id)
    geometry = make_geometry(db_session).id
    assert _insert_finding(db_session, scope="geometry", geometry=geometry)
    assert _insert_finding(db_session, scope="calculation", calc=calc.id)
    assert _insert_finding(db_session, scope="determination", det=det)
    _refuses(db_session, "subject_matches_scope", _insert_finding, scope="calculation", geometry=geometry)
    _refuses(db_session, "subject_matches_scope", _insert_finding, scope="geometry", geometry=geometry, calc=calc.id)
    _refuses(db_session, "subject_matches_scope", _insert_finding, scope="determination", det=det, calc=calc.id)


def test_only_a_role_invalidation_names_a_role(db_session):
    _refuses(db_session, "role_iff_role_invalidation", _insert_finding, kind="role_invalidation")
    _refuses(db_session, "role_iff_role_invalidation", _insert_finding, kind="identity_incompatibility", role="energy")
    assert _insert_finding(db_session, kind="role_invalidation", role="curvature")


def test_an_adjudication_supersedes_an_earlier_finding_and_needs_authority(db_session):
    earlier = _insert_finding(db_session)
    _refuses(db_session, "adjudication_needs_authority", _insert_finding, kind="adjudication", authority="authorized_adjudication")
    _refuses(
        db_session, "adjudication_needs_authority", _insert_finding, kind="adjudication", supersedes=earlier,
        authority="producer_assertion",
    )
    assert _insert_finding(
        db_session, kind="adjudication", supersedes=earlier, authority="authorized_adjudication", verdict="does_not_invalidate"
    )


def test_a_finding_is_append_only(db_session):
    finding = _insert_finding(db_session)
    with pytest.raises(DBAPIError, match="structure_evidence_finding_is_append_only"), db_session.begin_nested():
        db_session.execute(text("UPDATE structure_evidence_finding SET verdict = 'unresolved' WHERE id = :i"), {"i": finding})
    with pytest.raises(DBAPIError, match="structure_evidence_finding_is_append_only"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_evidence_finding WHERE id = :i"), {"i": finding})
    assert db_session.scalar(text("SELECT verdict FROM structure_evidence_finding WHERE id = :i"), {"i": finding}) == "invalidates"


def test_a_finding_rationale_is_present_and_bounded_and_the_version_positive(db_session):
    _refuses(db_session, "rationale_bounded", _insert_finding, rationale="   ")
    _refuses(db_session, "rationale_bounded", _insert_finding, rationale="r" * 2001)
    _refuses(db_session, "semantic_version_positive", _insert_finding, version=0)


def test_the_guard_registry_covers_every_ownership_column_to_an_accepted_root_and_exists_in_the_database(
    db_session, migration
):
    """Every column of the new tables that points at an accepted-science root is guarded, and every guard exists.

    The shared accepted-science registry (``c6f2a9d4e7b1``) requires each guarded column to be NOT NULL; a
    determination is owned by one of two entries, so its owner columns are nullable by design and the guard
    function skips a NULL one. They are therefore registered here, in this revision's own tuples, and checked
    against the model and against ``pg_trigger`` by this test instead of the shared one.
    """
    roots = {"transition_state_entry", "conformer_observation"}
    tables = Base.metadata.tables
    declared = {
        (migration._DETERMINATION, column, record_type) for _, record_type, column in migration._DETERMINATION_GUARDS
    } | {(migration._SOURCE, column, record_type) for _, record_type, column in migration._SOURCE_GUARDS}
    expected = set()
    for table in (migration._DETERMINATION, migration._SOURCE):
        for column in tables[table].c:
            for fk in column.foreign_keys:
                if fk.column.table.name in roots:
                    expected.add((table, column.name, fk.column.table.name))
    assert declared == expected and len(expected) == 4
    for name, _, _ in (*migration._DETERMINATION_GUARDS, *migration._SOURCE_GUARDS):
        assert db_session.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname = :n"), {"n": name}) == 1, name
    for table in TABLES:
        assert (
            db_session.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname = :n"), {"n": f"trg_{table}_truncate"})
            == 1
        ), table
