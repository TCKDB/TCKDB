"""Regression tests for the scientific_read test factories themselves.

These guard invariants the factories must keep so the rest of the
scientific test suite stays deterministic — in particular that rapid
successive ``make_chem_reaction`` calls do not collide on
``ChemReaction.public_ref``.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_literature,
    make_species,
    make_workflow_tool_release,
    next_inchi_key,
)


def test_make_chem_reaction_populates_stoichiometry_hash(db_session):
    """Factory mirrors production by computing ``stoichiometry_hash``
    so the public-ref listener takes the content-derived path instead
    of the id()-based fallback."""
    a = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("FHA"))
    b = make_species(db_session, smiles="O", inchi_key=next_inchi_key("FHB"))
    rxn = make_chem_reaction(db_session, reactants=[a], products=[b])
    assert rxn.stoichiometry_hash is not None
    assert len(rxn.stoichiometry_hash) == 64


def test_many_chem_reactions_get_distinct_public_refs(db_session):
    """Creating many ChemReaction rows through the factory in one
    transaction must not collide on ``public_ref``.

    Regression for a flake where the public-ref fallback used
    ``id(obj)`` whenever ``stoichiometry_hash`` was unset; CPython
    recycles memory addresses, so two successive factory instances
    occasionally hashed to the same canonical string and tripped the
    ``ix_chem_reaction_public_ref`` unique index.
    """
    refs: set[str] = set()
    hashes: set[str] = set()
    for i in range(20):
        rs = make_species(
            db_session, smiles="C", inchi_key=next_inchi_key(f"DR{i}")
        )
        ps = make_species(
            db_session, smiles="O", inchi_key=next_inchi_key(f"DP{i}")
        )
        rxn = make_chem_reaction(db_session, reactants=[rs], products=[ps])
        refs.add(rxn.public_ref)
        hashes.add(rxn.stoichiometry_hash)
    assert len(refs) == 20
    assert len(hashes) == 20


def test_make_chem_reaction_is_get_or_create(db_session):
    """Calling ``make_chem_reaction`` twice with the same participants
    returns the same row, not two duplicates that would trip the
    ``stoichiometry_hash`` unique constraint.
    """
    a = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("GOCA"))
    b = make_species(db_session, smiles="O", inchi_key=next_inchi_key("GOCB"))
    first = make_chem_reaction(db_session, reactants=[a], products=[b])
    second = make_chem_reaction(db_session, reactants=[a], products=[b])
    assert first.id == second.id
    assert first.public_ref == second.public_ref


def test_make_chem_reaction_explicit_hash_override_creates_distinct_row(
    db_session,
):
    """Callers that need two distinct ChemReaction rows for the same
    participants can force it by supplying an explicit
    ``stoichiometry_hash``. Sanity-checks the documented escape hatch.
    """
    a = make_species(db_session, smiles="C", inchi_key=next_inchi_key("OVA"))
    b = make_species(db_session, smiles="N", inchi_key=next_inchi_key("OVB"))
    first = make_chem_reaction(db_session, reactants=[a], products=[b])
    forced = make_chem_reaction(
        db_session,
        reactants=[a],
        products=[b],
        stoichiometry_hash="0" * 64,
    )
    assert forced.id != first.id
    assert forced.public_ref != first.public_ref


def test_make_workflow_tool_release_default_is_duplicate_safe(db_session):
    """Two default-args calls in one transaction must not violate the
    ``WorkflowTool.name`` unique constraint.

    Regression: the factory used to ``INSERT`` a fresh
    ``WorkflowTool(name="arc")`` on every call, which collided as soon as
    two test helpers (or two consecutive test setups in one transaction)
    both leaned on the default.
    """
    first = make_workflow_tool_release(db_session)
    second = make_workflow_tool_release(db_session)
    assert first.id == second.id
    assert first.workflow_tool_id == second.workflow_tool_id

    tools = db_session.scalars(
        select(WorkflowTool).where(WorkflowTool.name == "arc")
    ).all()
    assert len(tools) == 1


def test_make_workflow_tool_release_distinct_versions_create_distinct_releases(
    db_session,
):
    """Same tool, different versions → one tool row, two release rows.

    Confirms the factory only dedups on the natural-identity tuple, not
    on ``WorkflowTool`` alone.
    """
    a = make_workflow_tool_release(db_session, name="arc", version="1.0.0")
    b = make_workflow_tool_release(db_session, name="arc", version="2.0.0")
    assert a.workflow_tool_id == b.workflow_tool_id
    assert a.id != b.id

    releases = db_session.scalars(
        select(WorkflowToolRelease).where(
            WorkflowToolRelease.workflow_tool_id == a.workflow_tool_id
        )
    ).all()
    assert {rel.version for rel in releases} == {"1.0.0", "2.0.0"}


def test_make_literature_default_dois_are_unique_and_independent(db_session):
    """Default-DOI literature rows must be unique and must not share state
    with :func:`_next_fsf_value` (which previously also generated DOIs).
    """
    from tests.services.scientific_read._factories import (
        _next_fsf_value,
        _next_literature_doi,
    )

    fsf_before = _next_fsf_value()
    a = make_literature(db_session)
    b = make_literature(db_session)
    fsf_after = _next_fsf_value()

    assert a.doi != b.doi
    # Between the two ``_next_fsf_value`` calls we only made literature
    # rows; the FSF counter must therefore have advanced by exactly one
    # step (the second call's own increment). If the literature factory
    # were still pulling from the FSF counter, this delta would be 3.
    delta_steps = round((fsf_after - fsf_before) / 0.0001)
    assert delta_steps == 1, (
        f"FSF counter advanced by {delta_steps} steps across two "
        "make_literature() calls — literature DOI helper is leaking "
        "into the FSF counter."
    )

    # And the literature DOI helper produces a stable shape.
    assert _next_literature_doi().startswith("10.0000/test-")
