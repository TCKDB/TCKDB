"""Vocabulary / discovery reads for the scientific API (DR none; Phase 7).

Exact-string filters (method, basis, reaction family, software) are only
usable if a client can discover which values actually exist in the
database. These helpers return the distinct stored values with usage
counts so a modeler can see, e.g., which levels of theory or reaction
families are available before issuing a filtered search.

**What ``count`` counts, and why it differs by helper.**

- ``list_methods`` / ``list_basis_sets`` count ``level_of_theory`` rows
  sharing a method/basis. That table has no uniqueness on either column
  alone, so two LOTs can legitimately share a method with a different
  basis — an in-table row count is informative there.
- ``list_software`` / ``list_workflow_tools`` count **calculations**: the
  number of ``calculation`` rows attributing that software/tool (via
  ``software_release`` / ``workflow_tool_release``). An in-table row count
  would not be informative here — ``Software.name`` and
  ``WorkflowTool.name`` each carry a ``UniqueConstraint``, so a row count
  is structurally always 1 regardless of usage. More importantly, these
  two are now **usage-derived lists**, not registry dumps: a software or
  workflow-tool row that no calculation references does not appear at
  all (2026-08, closing the "Arkane shows up with zero attributable
  calculations" gap). Each accepts an optional ``record_kind`` filter
  (:class:`app.db.models.common.CalculationRecordKind`) narrowing to
  calculations owned by a ``species`` or ``transition_state`` record;
  omitted, it is any kind.
- ``list_software_versions`` / ``list_workflow_tool_versions`` continue
  counting ``software_release`` / ``workflow_tool_release`` rows sharing a
  version *for the given parent* (that table has no uniqueness on version
  alone, so the in-table count stays informative, same as
  methods/basis-sets) — but the release-row population is now restricted
  to releases actually referenced by at least one calculation. A release
  with zero calculations neither appears as a version nor contributes to
  a sibling version's count, matching the same "usage, not registration"
  rule applied one level down.
- ``list_reaction_families`` is deliberately unaffected: it lists the
  seeded canonical taxonomy with usage counts that are allowed to be 0.
  See its own docstring.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.chemistry.reaction_family_display import reaction_family_display_name
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationRecordKind
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


def _record_kind_filter(record_kind: CalculationRecordKind | None):
    """SQL predicate narrowing ``calculation`` rows to one owner kind.

    ``None`` means "any kind" and yields no predicate at all (the caller
    skips adding a ``WHERE`` clause), rather than a predicate that happens
    to always be true — so an unrecognised future kind cannot silently
    become a no-op filter.
    """
    if record_kind is None:
        return None
    if record_kind is CalculationRecordKind.species:
        return Calculation.species_entry_id.is_not(None)
    if record_kind is CalculationRecordKind.transition_state:
        return Calculation.transition_state_entry_id.is_not(None)
    raise ValueError(f"unknown record_kind: {record_kind!r}")  # pragma: no cover


def _counted_calculation_usage(
    session: Session,
    *,
    name_column,
    release_id_column,
    release_parent_id_column,
    calculation_release_fk_column,
    parent_model,
    record_kind: CalculationRecordKind | None,
) -> list[dict]:
    """``[{"value": name, "count": n}, ...]`` of parents used by ≥1 calculation.

    Backs both ``list_software`` and ``list_workflow_tools`` — the two
    identity/release table pairs have the same shape (a name-unique
    identity table, a release table FK'd to it, and ``calculation`` FK'd
    to the release). ``count`` is the number of ``calculation`` rows
    attributing the parent (across every release/version of it), and a
    parent with zero attributing calculations is absent from the result
    — an ``INNER JOIN`` from ``calculation``, not an ``OUTER JOIN`` from
    the identity table, so nothing here can leak a registered-but-unused
    row back in.
    """
    stmt = (
        select(name_column, func.count(Calculation.id))
        .select_from(Calculation)
        .join(
            release_id_column.class_,
            calculation_release_fk_column == release_id_column,
        )
        .join(parent_model, release_parent_id_column == parent_model.id)
    )
    kind_filter = _record_kind_filter(record_kind)
    if kind_filter is not None:
        stmt = stmt.where(kind_filter)
    stmt = stmt.group_by(name_column).order_by(
        func.count(Calculation.id).desc(), name_column.asc()
    )
    rows = session.execute(stmt).all()
    return [{"value": name, "count": count} for name, count in rows]


def _counted_distinct_release_versions(
    session: Session,
    *,
    release_version_column,
    release_id_column,
    release_parent_id_column,
    calculation_release_fk_column,
    parent_model,
    parent_name: str,
) -> list[dict]:
    """``[{"value": v, "count": n}, ...]`` of one named parent's *used* releases.

    Backs both ``list_software_versions`` and ``list_workflow_tool_versions``
    — the two release tables have the identical shape (a nullable
    ``version`` column, a FK to a name-unique parent identity table), so
    one query does both. Joins on the parent's identity table and filters
    on its ``name`` so an unrecognised or unspecified parent yields an
    empty list, never every parent's versions merged together.

    A release row is only counted if at least one ``calculation`` cites it
    (``EXISTS`` correlated subquery) — a version whose only releases are
    unused does not appear, and an unused release does not inflate a
    sibling version's count either.
    """
    used = exists(
        select(1).where(calculation_release_fk_column == release_id_column)
    )
    rows = session.execute(
        select(release_version_column, func.count())
        .join(parent_model, release_parent_id_column == parent_model.id)
        .where(
            parent_model.name == parent_name,
            release_version_column.is_not(None),
            used,
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


def list_software(
    session: Session, record_kind: CalculationRecordKind | None = None
) -> list[dict]:
    """Software names referenced by at least one calculation.

    Derived from usage, not from the ``software`` registry: a package with
    no attributing calculation (e.g. registered but never actually run —
    the ``Arkane`` case) does not appear. ``count`` is the number of
    calculations attributing that software. ``record_kind`` optionally
    narrows to calculations owned by a ``species`` or ``transition_state``
    record; omitted, it is any kind.
    """
    return _counted_calculation_usage(
        session,
        name_column=Software.name,
        release_id_column=SoftwareRelease.id,
        release_parent_id_column=SoftwareRelease.software_id,
        calculation_release_fk_column=Calculation.software_release_id,
        parent_model=Software,
        record_kind=record_kind,
    )


def list_workflow_tools(
    session: Session, record_kind: CalculationRecordKind | None = None
) -> list[dict]:
    """Workflow tool names referenced by at least one calculation.

    Mirrors :func:`list_software`: derived from usage, not the
    ``workflow_tool`` registry; ``count`` is the number of attributing
    calculations; ``record_kind`` optionally narrows by owner kind.
    """
    return _counted_calculation_usage(
        session,
        name_column=WorkflowTool.name,
        release_id_column=WorkflowToolRelease.id,
        release_parent_id_column=WorkflowToolRelease.workflow_tool_id,
        calculation_release_fk_column=Calculation.workflow_tool_release_id,
        parent_model=WorkflowTool,
        record_kind=record_kind,
    )


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

    Only releases referenced by at least one calculation are considered
    (see the module docstring); a version whose releases are all unused
    is absent, same as an unrecognised software name.
    """
    if not software:
        raise ValueError(
            "missing_version_parent: software is required for "
            "/meta/software-versions."
        )
    return _counted_distinct_release_versions(
        session,
        release_version_column=SoftwareRelease.version,
        release_id_column=SoftwareRelease.id,
        release_parent_id_column=SoftwareRelease.software_id,
        calculation_release_fk_column=Calculation.software_release_id,
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
    Only releases referenced by at least one calculation are considered.
    """
    if not workflow_tool:
        raise ValueError(
            "missing_version_parent: workflow_tool is required for "
            "/meta/workflow-tool-versions."
        )
    return _counted_distinct_release_versions(
        session,
        release_version_column=WorkflowToolRelease.version,
        release_id_column=WorkflowToolRelease.id,
        release_parent_id_column=WorkflowToolRelease.workflow_tool_id,
        calculation_release_fk_column=Calculation.workflow_tool_release_id,
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
