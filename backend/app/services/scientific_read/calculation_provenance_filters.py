"""Shared method/basis/software/workflow-tool provenance filter.

Factored out of ``transition_states_search.py``'s original
``_apply_method_basis_software_filters`` (the TS search/browse
implementation) so ``species.py``'s ``/species/browse`` provenance
filters do not fork the join logic. The extraction is behaviour-
preserving for the TS caller: ``_apply_method_basis_software_filters``
still takes the same arguments, still returns the same statement shape,
and now does so by delegating to :func:`apply_calculation_provenance_filter`
below with the same joins in the same order. Confirmed by running the
full existing TS search/browse test suites
(``backend/tests/services/scientific_read/test_transition_states_search.py``,
``backend/tests/api/scientific/test_api_scientific_transition_states_search.py``,
and the browse counterparts) unchanged after the refactor — see the PR
description for the exact command and result.

Semantics: **OR-across-calculation**. A candidate — whatever unit the
caller's ``base_calculation_select`` scopes to (one transition-state
entry, one species) — passes if *at least one* of its ``calculation``
rows matches every supplied method/basis/software/workflow-tool
constraint simultaneously (AND across fields on that one row, OR across
the candidate's calculation rows). A calculation missing the relevant
provenance link (no ``lot_id``, no ``software_release_id``, no
``workflow_tool_release_id``) simply cannot satisfy that field's clause
and is excluded like any other non-match — never null-matches.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.sql import Select

from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease


class ProvenanceFilterRequest(Protocol):
    """Structural type: any request model exposing these six fields.

    Both ``TransitionStatesSearchRequest``/``TransitionStatesBrowseRequest``
    and ``SpeciesBrowseRequest`` satisfy this without inheriting from a
    common base — duck typing, matched at the field names the TS search
    service already established.
    """

    method: str | None
    basis: str | None
    software: str | None
    software_version: str | None
    workflow_tool: str | None
    workflow_tool_version: str | None


def apply_calculation_provenance_filter(
    stmt,
    request: ProvenanceFilterRequest,
    base_calculation_select: Select,
):
    """Narrow ``stmt`` to rows with >=1 matching ``calculation`` row.

    :param stmt: The outer candidate statement (one row per TS entry, per
        species, etc.) to narrow.
    :param request: Anything satisfying :class:`ProvenanceFilterRequest`.
    :param base_calculation_select: A ``select(Calculation.id))`` already
        scoped — via ``.where(...)`` and/or ``.join(...)`` — to exactly
        the ``calculation`` rows belonging to one candidate row of
        ``stmt``. For example
        ``select(Calculation.id).where(Calculation.transition_state_entry_id
        == TransitionStateEntry.id)`` for TS search/browse, or a
        species-entry join correlated to ``Species.id`` for species
        browse. This function only adds the ``LevelOfTheory`` /
        ``SoftwareRelease`` + ``Software`` / ``WorkflowToolRelease`` +
        ``WorkflowTool`` joins and predicates on top of it, then turns
        the result into a correlated ``EXISTS`` clause applied to
        ``stmt``.
    :returns: ``stmt`` unchanged if no method/basis/software/
        software_version/workflow_tool/workflow_tool_version field is
        set on ``request``; otherwise ``stmt`` with the ``EXISTS`` clause
        applied.
    """
    method_or_basis = request.method is not None or request.basis is not None
    sw_filter = (
        request.software is not None or request.software_version is not None
    )
    wf_filter = (
        request.workflow_tool is not None
        or request.workflow_tool_version is not None
    )
    if not (method_or_basis or sw_filter or wf_filter):
        return stmt

    sub_select = base_calculation_select
    if method_or_basis:
        sub_select = sub_select.join(
            LevelOfTheory, LevelOfTheory.id == Calculation.lot_id
        )
        if request.method is not None:
            sub_select = sub_select.where(
                LevelOfTheory.method == request.method
            )
        if request.basis is not None:
            sub_select = sub_select.where(
                LevelOfTheory.basis == request.basis
            )
    if sw_filter:
        sub_select = sub_select.join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
        ).join(Software, Software.id == SoftwareRelease.software_id)
        if request.software is not None:
            sub_select = sub_select.where(Software.name == request.software)
        if request.software_version is not None:
            sub_select = sub_select.where(
                SoftwareRelease.version == request.software_version
            )
    if wf_filter:
        sub_select = sub_select.join(
            WorkflowToolRelease,
            WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
        ).join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        if request.workflow_tool is not None:
            sub_select = sub_select.where(
                WorkflowTool.name == request.workflow_tool
            )
        if request.workflow_tool_version is not None:
            sub_select = sub_select.where(
                WorkflowToolRelease.version == request.workflow_tool_version
            )
    return stmt.where(sub_select.exists())
