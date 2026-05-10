"""Service-layer tests for get_reaction_full."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_provenance import (
    ReactionFullReadRequest,
    ReviewDetail,
)
from app.services.scientific_read.provenance import get_reaction_full
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _setup_reaction_with_kinetics(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("FA"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("FB"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    return chem, entry


# ---------------------------------------------------------------------------
# Path scope + 404
# ---------------------------------------------------------------------------


def test_unknown_reaction_entry_id_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        get_reaction_full(
            db_session,
            reaction_entry_id=999_999,
            request=ReactionFullReadRequest(),
        )


# ---------------------------------------------------------------------------
# Default include set
# ---------------------------------------------------------------------------


def test_default_returns_species_kinetics_transition_states_only(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(),
    )
    # Always populated.
    assert response.reaction_entry.id == entry.id
    assert response.species is not None
    assert response.kinetics == []  # no kinetics yet, but key present.
    assert response.transition_states == []  # no TS, but key present.
    # Not requested → omitted.
    assert response.calculations is None
    assert response.path_search is None
    assert response.artifacts is None


def test_include_all_populates_every_section(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["all"]),
    )
    assert response.species is not None
    assert response.kinetics == []
    assert response.transition_states == []
    assert response.calculations == []
    assert response.path_search == []
    assert response.irc == []
    assert response.scans == []
    assert response.conformers == []
    assert response.artifacts == []


def test_include_calculations_does_not_add_other_sections(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["calculations"]),
    )
    assert response.calculations == []
    assert response.path_search is None
    assert response.artifacts is None


# ---------------------------------------------------------------------------
# Kinetics: TS-backed and non-TS-backed coexistence
# ---------------------------------------------------------------------------


def test_full_includes_non_ts_backed_kinetics_with_null_ts_links(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["kinetics"]),
    )
    assert response.kinetics is not None and len(response.kinetics) == 1
    rec = response.kinetics[0]
    assert rec.scientific_origin == ScientificOriginKind.experimental
    assert rec.provenance.transition_state_entry_id is None
    assert rec.provenance.ts_opt_calculation_id is None


def test_full_returns_empty_transition_states_for_experimental_only_entry(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["transition_states", "kinetics"]),
    )
    assert response.kinetics is not None and len(response.kinetics) == 1
    assert response.transition_states == []


# ---------------------------------------------------------------------------
# Top-level filters
# ---------------------------------------------------------------------------


def test_min_review_status_filters_kinetics_section_but_keeps_entry(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    k_unreviewed = make_kinetics(db_session, reaction_entry=entry)

    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(
            min_review_status=RecordReviewStatus.approved,
            include=["kinetics"],
        ),
    )
    assert response.reaction_entry.id == entry.id
    assert response.kinetics == []
    # Parent entry remains regardless.
    assert response.review_summary is not None


# ---------------------------------------------------------------------------
# Review records (include_review=full)
# ---------------------------------------------------------------------------


def test_review_records_default_summary_omits_audit_array(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(),
    )
    assert response.review_records is None


def test_review_records_full_adds_audit_array(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
    )

    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include_review=ReviewDetail.full),
    )
    assert response.review_records is not None
    assert any(
        r.record_type == "reaction_entry" and r.record_id == entry.id
        for r in response.review_records
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        get_reaction_full(
            db_session,
            reaction_entry_id=entry.id,
            request=ReactionFullReadRequest(sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_reaction_full(
            db_session,
            reaction_entry_id=entry.id,
            request=ReactionFullReadRequest(include=["banana"]),
        )


def test_two_identical_calls_return_byte_equal_bodies(db_session):
    _, entry = _setup_reaction_with_kinetics(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    r1 = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["all"]),
    )
    r2 = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["all"]),
    )
    assert r1.model_dump() == r2.model_dump()


# ---------------------------------------------------------------------------
# Phase 7.1 regression: TS calculation dependency formatting
# ---------------------------------------------------------------------------
#
# Prior to Phase 7.1 the /full endpoint accessed ``CalculationDependency.role``,
# which does not exist (the column is ``dependency_role``). The path was
# unexercised because no test created a dependency row across the TS calc
# graph. The test below builds that graph end-to-end via real SQLAlchemy
# flushes so the AttributeError surfaces immediately if the access is wrong.


def test_full_formats_ts_calculation_dependency_with_real_db_row(db_session):
    """Regression: ``/full`` must read CalculationDependency.dependency_role."""
    from app.db.models.calculation import Calculation
    from app.db.models.common import (
        CalculationDependencyRole,
        CalculationType,
    )
    from app.db.models.transition_state import (
        TransitionState,
        TransitionStateEntry,
    )
    from app.schemas.reads.scientific_provenance import ReactionFullReadRequest

    from tests.services.scientific_read._factories import attach_dependency

    _, entry = _setup_reaction_with_kinetics(db_session)

    # Build the minimal TS chain: TransitionState → TransitionStateEntry →
    # two calculations linked by a CalculationDependency edge.
    ts = TransitionState(reaction_entry_id=entry.id)
    db_session.add(ts)
    db_session.flush()

    ts_entry = TransitionStateEntry(
        transition_state_id=ts.id, charge=0, multiplicity=1
    )
    db_session.add(ts_entry)
    db_session.flush()

    parent = Calculation(
        type=CalculationType.opt, transition_state_entry_id=ts_entry.id
    )
    child = Calculation(
        type=CalculationType.freq, transition_state_entry_id=ts_entry.id
    )
    db_session.add_all([parent, child])
    db_session.flush()

    attach_dependency(
        db_session,
        parent=parent,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )

    # The pre-fix code raised AttributeError here (d.role on the ORM row).
    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["transition_states"]),
    )

    assert response.transition_states is not None
    assert len(response.transition_states) == 1
    ts_record = response.transition_states[0]
    assert ts_record.transition_state_entry_id == ts_entry.id

    # The dependency surfaces in the response with the right role string.
    dep_records = ts_record.dependencies
    assert len(dep_records) == 1
    dep = dep_records[0]
    assert dep.parent_calculation_id == parent.id
    assert dep.child_calculation_id == child.id
    assert dep.role == CalculationDependencyRole.freq_on.value


def test_full_handles_multiple_ts_dependency_roles(db_session):
    """Multiple dependency edges with different roles all serialize correctly."""
    from app.db.models.calculation import Calculation
    from app.db.models.common import (
        CalculationDependencyRole,
        CalculationType,
    )
    from app.db.models.transition_state import (
        TransitionState,
        TransitionStateEntry,
    )
    from app.schemas.reads.scientific_provenance import ReactionFullReadRequest

    from tests.services.scientific_read._factories import attach_dependency

    _, entry = _setup_reaction_with_kinetics(db_session)

    ts = TransitionState(reaction_entry_id=entry.id)
    db_session.add(ts)
    db_session.flush()
    ts_entry = TransitionStateEntry(
        transition_state_id=ts.id, charge=0, multiplicity=1
    )
    db_session.add(ts_entry)
    db_session.flush()

    opt = Calculation(
        type=CalculationType.opt, transition_state_entry_id=ts_entry.id
    )
    freq = Calculation(
        type=CalculationType.freq, transition_state_entry_id=ts_entry.id
    )
    sp = Calculation(
        type=CalculationType.sp, transition_state_entry_id=ts_entry.id
    )
    db_session.add_all([opt, freq, sp])
    db_session.flush()

    attach_dependency(
        db_session,
        parent=opt,
        child=freq,
        role=CalculationDependencyRole.freq_on,
    )
    attach_dependency(
        db_session,
        parent=opt,
        child=sp,
        role=CalculationDependencyRole.single_point_on,
    )

    response = get_reaction_full(
        db_session,
        reaction_entry_id=entry.id,
        request=ReactionFullReadRequest(include=["transition_states"]),
    )
    deps = response.transition_states[0].dependencies
    roles = {d.role for d in deps}
    assert roles == {
        CalculationDependencyRole.freq_on.value,
        CalculationDependencyRole.single_point_on.value,
    }
