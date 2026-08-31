"""Vocabulary / discovery reads for the scientific API (DR none; Phase 7).

Exact-string filters (method, basis, reaction family, software) are only
usable if a client can discover which values actually exist in the
database. These helpers return the distinct stored values with usage
counts so a modeler can see, e.g., which levels of theory or reaction
families are available before issuing a filtered search.

**What ``count`` counts.** Every helper in this module counts rows in the
same table the ``value`` column itself lives in — never a downstream
usage count (calculations, thermo records, …). ``list_methods`` and
``list_basis_sets`` count ``level_of_theory`` rows sharing a method/basis
(that table has no uniqueness on either column alone, so two LOTs can
legitimately share a method with a different basis — the count is
therefore informative). ``list_software`` and ``list_workflow_tools``
count ``software``/``workflow_tool`` rows sharing a name — both columns
carry a ``UniqueConstraint``, so today that count is always 1, and that is
the honest answer for an identity table, not a bug to paper over.
``list_software_versions`` and ``list_workflow_tool_versions`` continue
the same rule one level down: they count ``software_release`` /
``workflow_tool_release`` rows sharing a version *for the given parent* —
that table has no uniqueness on version alone (only on the
``(parent_id, version, revision/git_commit, build)`` tuple), so two
releases can share a version with a different revision/build and the
count is informative again, exactly like methods/basis-sets.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chemistry.reaction_family_display import reaction_family_display_name
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionFamily
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease


def _counted_distinct(session: Session, column) -> list[dict]:
    """Return ``[{"value": v, "count": n}, ...]`` for non-null values of a
    column, ordered by descending count then value."""
    rows = session.execute(
        select(column, func.count())
        .where(column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column.asc())
    ).all()
    return [{"value": value, "count": count} for value, count in rows]


def _counted_distinct_release_versions(
    session: Session,
    *,
    release_version_column,
    release_parent_id_column,
    parent_model,
    parent_name: str,
) -> list[dict]:
    """``[{"value": v, "count": n}, ...]`` of one named parent's releases.

    Backs both ``list_software_versions`` and ``list_workflow_tool_versions``
    — the two release tables have the identical shape (a nullable
    ``version`` column, a FK to a name-unique parent identity table), so
    one query does both. Joins on the parent's identity table and filters
    on its ``name`` so an unrecognised or unspecified parent yields an
    empty list, never every parent's versions merged together.
    """
    rows = session.execute(
        select(release_version_column, func.count())
        .join(parent_model, release_parent_id_column == parent_model.id)
        .where(
            parent_model.name == parent_name,
            release_version_column.is_not(None),
        )
        .group_by(release_version_column)
        .order_by(func.count().desc(), release_version_column.asc())
    ).all()
    return [{"value": value, "count": count} for value, count in rows]


def list_methods(session: Session) -> list[dict]:
    """Distinct level-of-theory ``method`` values with usage counts."""
    return _counted_distinct(session, LevelOfTheory.method)


def list_basis_sets(session: Session) -> list[dict]:
    """Distinct level-of-theory ``basis`` values with usage counts."""
    return _counted_distinct(session, LevelOfTheory.basis)


def list_software(session: Session) -> list[dict]:
    """Distinct software ``name`` values with usage counts."""
    return _counted_distinct(session, Software.name)


def list_workflow_tools(session: Session) -> list[dict]:
    """Distinct workflow tool ``name`` values with usage counts."""
    return _counted_distinct(session, WorkflowTool.name)


def list_software_versions(session: Session, software: str | None) -> list[dict]:
    """Distinct ``software_release.version`` values for one named software.

    ``software`` is required. This backs a dependent dropdown that only
    ever renders after a software has already been chosen; an unscoped
    call would merge version strings from every software package in the
    archive into one list with no way to say which package a given
    "1.0" belongs to, silently corrupting the vocabulary it exists to
    serve. Raises ``ValueError("missing_version_parent: ...")`` (→ HTTP
    422, coded) when ``software`` is missing or blank, so the refusal
    names the fix instead of falling through to FastAPI's generic
    validation error. A ``software`` naming no known package (or none at
    all) is a different case — an empty result, not a refusal — handled
    by the join finding nothing to return.
    """
    if not software:
        raise ValueError(
            "missing_version_parent: software is required for "
            "/meta/software-versions."
        )
    return _counted_distinct_release_versions(
        session,
        release_version_column=SoftwareRelease.version,
        release_parent_id_column=SoftwareRelease.software_id,
        parent_model=Software,
        parent_name=software,
    )


def list_workflow_tool_versions(
    session: Session, workflow_tool: str | None
) -> list[dict]:
    """Distinct ``workflow_tool_release.version`` values for one named tool.

    Mirrors :func:`list_software_versions`: ``workflow_tool`` is required
    for the same reason (a merged, unscoped list of every tool's versions
    is not a usable vocabulary), and raises
    ``ValueError("missing_version_parent: ...")`` when absent or blank.
    """
    if not workflow_tool:
        raise ValueError(
            "missing_version_parent: workflow_tool is required for "
            "/meta/workflow-tool-versions."
        )
    return _counted_distinct_release_versions(
        session,
        release_version_column=WorkflowToolRelease.version,
        release_parent_id_column=WorkflowToolRelease.workflow_tool_id,
        parent_model=WorkflowTool,
        parent_name=workflow_tool,
    )


def list_reaction_families(session: Session) -> list[dict]:
    """Canonical reaction families with usage counts (0 if unused).

    Lists the seeded ``reaction_family`` vocabulary and how many reactions
    reference each — the discoverable set of valid ``family=`` filter
    values for reaction search.

    ``value`` stays the raw RMG identifier: it is the filter token, and a
    client that echoes a display name back as ``family=`` must not match.
    ``display_name`` is the readable form, derived at read time by
    :func:`app.chemistry.reaction_family_display.reaction_family_display_name`
    — for a handful of families whose meaning is genuinely unresolved it is
    the identifier itself, unchanged, rather than a half-translation.
    """
    rows = session.execute(
        select(ReactionFamily.name, func.count(ChemReaction.id))
        .outerjoin(
            ChemReaction, ChemReaction.reaction_family_id == ReactionFamily.id
        )
        .group_by(ReactionFamily.name)
        .order_by(func.count(ChemReaction.id).desc(), ReactionFamily.name.asc())
    ).all()
    return [
        {
            "value": name,
            "display_name": reaction_family_display_name(name),
            "count": count,
        }
        for name, count in rows
    ]
