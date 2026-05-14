"""Service implementation for /api/v1/scientific/calculations/{handle}.

Default-shape calculation detail read. Heavy include sections (results,
dependencies, parameters, constraints, artifacts, geometries, validation,
scf_stability, scan, irc, path_search) are validated as legal include
tokens but not yet expanded — non-empty heavy includes are rejected with
422 ``include_not_implemented_yet`` to avoid silently advertising tokens
that do nothing. See ``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationConstraint,
    CalculationDependency,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationPathSearchResult,
    CalculationSCFStability,
    CalculationScanResult,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import SubmissionRecordLink
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_calculation import (
    AvailableCalculationSections,
    CalculationCoreBlock,
    CalculationDetailRequest,
    CalculationEvidenceProvenanceSummary,
    CalculationOwnerSummary,
    RequestEcho,
    ScientificCalculationDetailResponse,
    ScientificCalculationRecord,
    SpeciesEntryOwnerSummary,
    TransitionStateEntryOwnerSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import resolve_calculation_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)


# Heavy include sections promised by the spec but not yet implemented in
# this slice. Listing them in ``_LEGAL_INCLUDE_TOKENS`` lets the
# validation layer recognize them and produce a clear, stable
# ``include_not_implemented_yet`` error rather than the generic
# ``unknown_include_token`` (which would mislead clients into thinking
# the spec was wrong about the token's name).
_HEAVY_INCLUDE_TOKENS: frozenset[str] = frozenset(
    {
        "results",
        "dependencies",
        "parameters",
        "constraints",
        "artifacts",
        "input_geometries",
        "output_geometries",
        "geometry_validation",
        "scf_stability",
        "scan",
        "irc",
        "path_search",
        "review",
    }
)
_LEGAL_INCLUDE_TOKENS: set[str] = {
    *_HEAVY_INCLUDE_TOKENS,
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


def get_calculation(
    session: Session,
    *,
    calculation_handle: str,
    request: CalculationDetailRequest,
) -> ScientificCalculationDetailResponse:
    """Resolve *calculation_handle* and return its scientific projection.

    Path-handle semantics match the rest of the scientific read API:

    - Integer ``calculation.id`` string: SELECT by id.
    - Public ref ``calc_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    The default response surfaces ``calculation_ref``, an
    owner summary (species-entry **or** transition-state-entry per the
    schema's ``one_owner`` invariant), the level-of-theory / software
    release / workflow-tool release summaries, an optional literature
    pointer, a small evidence-and-provenance summary, and an
    ``available_sections`` boolean map describing which heavy
    include sections have data.

    :param session: SQLAlchemy session bound to the read DB.
    :param calculation_handle: integer id or ``calc_…`` ref.
    :param request: parsed request model carrying the ``include`` set.
    :raises NotFoundError: 404 when the calculation does not exist.
    :raises ValueError: 422 for malformed/wrong-prefix handles, unknown
        include tokens, or any of the heavy include tokens that have
        not been implemented yet in this first slice.
    """
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/calculations/{calculation_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    # First slice: heavy include payloads are not yet wired. ``all``
    # is filtered through ``validate_includes`` and would silently
    # expand to every heavy token; that would be misleading because
    # none of those payloads are populated. Reject any heavy include
    # explicitly so callers see why they got nothing.
    requested_heavy = includes & _HEAVY_INCLUDE_TOKENS
    if requested_heavy:
        raise ValueError(
            "include_not_implemented_yet: include token(s) "
            f"{sorted(requested_heavy)!r} are reserved for "
            "/scientific/calculations/{calculation_ref_or_id} but the "
            "default-shape detail endpoint has not implemented them "
            "yet. Drop the token(s) and re-issue, or fetch the legacy "
            "Tier-A/B /api/v1/calculations/{id}/... routes for the same "
            "data. See backend/docs/specs/scientific_calculation_reads.md."
        )

    calculation_id = resolve_calculation_handle(session, calculation_handle)
    calc = session.get(Calculation, calculation_id)
    if calc is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"calculation not found (calculation_id={calculation_id})",
            code="handle_not_found",
        )

    badge = _load_review_badge(session, calculation_id)
    summary = review_summary([badge])

    owner = _build_owner(session, calc)
    lot_summary = _build_lot_summary(session, calc.lot_id)
    software_summary = _build_software_summary(session, calc.software_release_id)
    workflow_summary = _build_workflow_summary(
        session, calc.workflow_tool_release_id
    )
    literature_summary = _build_literature_summary(session, calc.literature_id)
    provenance, available = _build_provenance_and_sections(
        session, calc, calculation_id
    )

    record = ScientificCalculationRecord(
        calculation=CalculationCoreBlock(
            calculation_id=calc.id,
            calculation_ref=calc.public_ref,
            type=calc.type,
            quality=calc.quality,
            created_at=calc.created_at,
            review=badge,
        ),
        owner=owner,
        level_of_theory=lot_summary,
        software_release=software_summary,
        workflow_tool_release=workflow_summary,
        literature=literature_summary,
        provenance=provenance,
        available_sections=available,
    )

    return ScientificCalculationDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=summary,
        record=record,
    )


# ---------------------------------------------------------------------------
# Review badge
# ---------------------------------------------------------------------------


def _load_review_badge(
    session: Session, calculation_id: int
) -> RecordReviewBadge:
    """Load the calculation's review badge, defaulting to ``not_reviewed``."""
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=[calculation_id],
    )
    return badges.get(
        calculation_id,
        RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
    )


# ---------------------------------------------------------------------------
# Owner builders (species-entry XOR transition-state-entry)
# ---------------------------------------------------------------------------


def _build_owner(
    session: Session, calc: Calculation
) -> CalculationOwnerSummary:
    """Build the owner block for *calc*.

    The schema's ``one_owner`` constraint guarantees exactly one of
    ``species_entry_id`` / ``transition_state_entry_id`` is non-null.
    """
    if calc.species_entry_id is not None:
        return CalculationOwnerSummary(
            kind="species_entry",
            species_entry=_build_species_owner(session, calc.species_entry_id),
        )
    if calc.transition_state_entry_id is not None:
        return CalculationOwnerSummary(
            kind="transition_state_entry",
            transition_state_entry=_build_ts_owner(
                session, calc.transition_state_entry_id
            ),
        )
    # The CHECK constraint forbids this, but raise a clear server-side
    # error if a row ever slips through (instead of returning a half-
    # populated owner block).
    raise NotFoundError(
        "calculation has no owner (one_owner constraint violated)",
        code="owner_missing",
    )


def _build_species_owner(
    session: Session, species_entry_id: int
) -> SpeciesEntryOwnerSummary:
    row = session.execute(
        select(
            SpeciesEntry.id.label("entry_id"),
            SpeciesEntry.public_ref.label("entry_ref"),
            SpeciesEntry.kind.label("entry_kind"),
            SpeciesEntry.electronic_state_kind.label("electronic_state_kind"),
            Species.id.label("species_id"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("smiles"),
            Species.inchi_key.label("inchi_key"),
            Species.charge.label("charge"),
            Species.multiplicity.label("multiplicity"),
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id == species_entry_id)
    ).one()
    return SpeciesEntryOwnerSummary(
        species_id=row.species_id,
        species_ref=row.species_ref,
        species_entry_id=row.entry_id,
        species_entry_ref=row.entry_ref,
        canonical_smiles=row.smiles,
        inchi_key=row.inchi_key,
        charge=row.charge,
        multiplicity=row.multiplicity,
        species_entry_kind=row.entry_kind,
        electronic_state_kind=row.electronic_state_kind,
    )


def _build_ts_owner(
    session: Session, transition_state_entry_id: int
) -> TransitionStateEntryOwnerSummary:
    row = session.execute(
        select(
            TransitionStateEntry.id.label("entry_id"),
            TransitionStateEntry.public_ref.label("entry_ref"),
            TransitionStateEntry.charge.label("charge"),
            TransitionStateEntry.multiplicity.label("multiplicity"),
            TransitionStateEntry.status.label("status"),
            TransitionState.id.label("ts_id"),
            TransitionState.public_ref.label("ts_ref"),
            TransitionState.label.label("ts_label"),
            ReactionEntry.id.label("reaction_entry_id"),
            ReactionEntry.public_ref.label("reaction_entry_ref"),
        )
        .join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        )
        .join(
            ReactionEntry,
            ReactionEntry.id == TransitionState.reaction_entry_id,
            isouter=True,
        )
        .where(TransitionStateEntry.id == transition_state_entry_id)
    ).one()
    return TransitionStateEntryOwnerSummary(
        transition_state_id=row.ts_id,
        transition_state_ref=row.ts_ref,
        transition_state_entry_id=row.entry_id,
        transition_state_entry_ref=row.entry_ref,
        label=row.ts_label,
        charge=row.charge,
        multiplicity=row.multiplicity,
        status=row.status,
        reaction_entry_id=row.reaction_entry_id,
        reaction_entry_ref=row.reaction_entry_ref,
    )


# ---------------------------------------------------------------------------
# Provenance summaries (LoT / software / workflow / literature)
# ---------------------------------------------------------------------------


def _build_lot_summary(
    session: Session, lot_id: int | None
) -> LevelOfTheorySummary | None:
    if lot_id is None:
        return None
    lot = session.get(LevelOfTheory, lot_id)
    if lot is None:
        return None
    return LevelOfTheorySummary(
        level_of_theory_id=lot.id,
        level_of_theory_ref=lot.public_ref,
        method=lot.method,
        basis=lot.basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        label=None,
    )


def _build_software_summary(
    session: Session, software_release_id: int | None
) -> SoftwareReleaseSummary | None:
    if software_release_id is None:
        return None
    row = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id == software_release_id)
    ).one_or_none()
    if row is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=row.id,
        software_release_ref=row.public_ref,
        software=row.name,
        version=row.version,
    )


def _build_workflow_summary(
    session: Session, workflow_tool_release_id: int | None
) -> WorkflowToolReleaseSummary | None:
    if workflow_tool_release_id is None:
        return None
    row = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
    ).one_or_none()
    if row is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.id,
        workflow_tool_release_ref=row.public_ref,
        workflow_tool=row.name,
        version=row.version,
    )


def _build_literature_summary(
    session: Session, literature_id: int | None
) -> LiteratureSummary | None:
    if literature_id is None:
        return None
    lit = session.get(Literature, literature_id)
    if lit is None:
        return None
    return LiteratureSummary(
        id=lit.id,
        literature_ref=lit.public_ref,
        title=getattr(lit, "title", None),
        year=getattr(lit, "year", None),
        doi=getattr(lit, "doi", None),
    )


# ---------------------------------------------------------------------------
# Provenance summary + available_sections
# ---------------------------------------------------------------------------


# Calc-type → primary result table. Used both by ``has_result`` (in the
# evidence provenance summary) and by ``available_sections.has_results``.
_PRIMARY_RESULT_TABLE: dict[CalculationType, type] = {
    CalculationType.sp: CalculationSPResult,
    CalculationType.opt: CalculationOptResult,
    CalculationType.freq: CalculationFreqResult,
    CalculationType.scan: CalculationScanResult,
    CalculationType.irc: CalculationIRCResult,
    CalculationType.path_search: CalculationPathSearchResult,
}


def _exists_for_calc(session: Session, model_cls, calculation_id: int) -> bool:
    """Return True iff *model_cls* has at least one row for *calculation_id*."""
    return bool(
        session.scalar(
            select(
                exists().where(model_cls.calculation_id == calculation_id)
            )
        )
    )


def _build_provenance_and_sections(
    session: Session, calc: Calculation, calculation_id: int
) -> tuple[CalculationEvidenceProvenanceSummary, AvailableCalculationSections]:
    """Compute the evidence provenance summary and ``available_sections``.

    Both blocks pull from the same set of EXISTS-style probes plus a
    couple of cheap row fetches (validation outcome, scf stability,
    convergence flag, submission link). Combined here so we issue the
    EXISTS queries once.
    """
    has_input_geometries = _exists_for_calc(
        session, CalculationInputGeometry, calculation_id
    )
    has_output_geometries = _exists_for_calc(
        session, CalculationOutputGeometry, calculation_id
    )
    has_constraints = _exists_for_calc(
        session, CalculationConstraint, calculation_id
    )
    has_parameters = _exists_for_calc(
        session, CalculationParameter, calculation_id
    )
    has_artifacts = _exists_for_calc(
        session, CalculationArtifact, calculation_id
    )
    has_dependencies = bool(
        session.scalar(
            select(
                exists().where(
                    (
                        CalculationDependency.parent_calculation_id
                        == calculation_id
                    )
                    | (
                        CalculationDependency.child_calculation_id
                        == calculation_id
                    )
                )
            )
        )
    )

    primary_table = _PRIMARY_RESULT_TABLE.get(calc.type)
    has_results = (
        _exists_for_calc(session, primary_table, calculation_id)
        if primary_table is not None
        else False
    )
    has_scan = _exists_for_calc(session, CalculationScanResult, calculation_id)
    has_irc = _exists_for_calc(session, CalculationIRCResult, calculation_id)
    has_path_search = _exists_for_calc(
        session, CalculationPathSearchResult, calculation_id
    )

    validation_row = session.scalar(
        select(CalculationGeometryValidation.validation_status).where(
            CalculationGeometryValidation.calculation_id == calculation_id
        )
    )
    validation_status = (
        validation_row.value if validation_row is not None else "not_present"
    )
    has_geometry_validation = validation_row is not None

    stability_row = session.scalar(
        select(CalculationSCFStability.status).where(
            CalculationSCFStability.calculation_id == calculation_id
        )
    )
    scf_stability_status = (
        stability_row.value if stability_row is not None else "not_present"
    )
    has_scf_stability = stability_row is not None

    converged = _load_converged_flag(session, calc, calculation_id)

    submission_id, submission_ref = _load_submission_link(
        session, calculation_id
    )

    provenance = CalculationEvidenceProvenanceSummary(
        has_result=has_results,
        converged=converged,
        geometry_validation_status=validation_status,
        scf_stability_status=scf_stability_status,
        submission_id=submission_id,
        submission_ref=submission_ref,
    )
    sections = AvailableCalculationSections(
        has_results=has_results,
        has_dependencies=has_dependencies,
        has_parameters=has_parameters,
        has_constraints=has_constraints,
        has_artifacts=has_artifacts,
        has_input_geometries=has_input_geometries,
        has_output_geometries=has_output_geometries,
        has_geometry_validation=has_geometry_validation,
        has_scf_stability=has_scf_stability,
        has_scan=has_scan,
        has_irc=has_irc,
        has_path_search=has_path_search,
    )
    return provenance, sections


def _load_converged_flag(
    session: Session, calc: Calculation, calculation_id: int
) -> bool | None:
    """Return the convergence flag for calc types that carry one.

    Only ``opt``, ``irc``, ``scan``, and ``path_search`` results model a
    convergence boolean today; for other calculation types the response
    surfaces ``null`` (never fabricates a flag).
    """
    if calc.type is CalculationType.opt:
        return session.scalar(
            select(CalculationOptResult.converged).where(
                CalculationOptResult.calculation_id == calculation_id
            )
        )
    if calc.type is CalculationType.irc:
        return session.scalar(
            select(CalculationIRCResult.converged).where(
                CalculationIRCResult.calculation_id == calculation_id
            )
        )
    if calc.type is CalculationType.scan:
        return session.scalar(
            select(CalculationScanResult.converged).where(
                CalculationScanResult.calculation_id == calculation_id
            )
        )
    if calc.type is CalculationType.path_search:
        return session.scalar(
            select(CalculationPathSearchResult.converged).where(
                CalculationPathSearchResult.calculation_id == calculation_id
            )
        )
    return None


def _load_submission_link(
    session: Session, calculation_id: int
) -> tuple[int | None, str | None]:
    """Return ``(submission_id, submission_ref)`` for the calc's submission.

    A calculation may have zero or one ``SubmissionRecordLink`` row in
    today's data; if multiple exist we pick the lowest-id one
    deterministically and let curation tooling clean up duplicates.
    """
    row = session.execute(
        select(SubmissionRecordLink.submission_id)
        .where(
            SubmissionRecordLink.record_type
            == SubmissionRecordType.calculation,
            SubmissionRecordLink.record_id == calculation_id,
        )
        .order_by(SubmissionRecordLink.submission_id.asc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    submission_id = int(row[0])
    # The Submission table carries a public_ref via PublicRefMixin.
    from app.db.models.submission import Submission  # local import: avoids cycle

    submission_ref = session.scalar(
        select(Submission.public_ref).where(Submission.id == submission_id)
    )
    return submission_id, submission_ref


__all__ = [
    "get_calculation",
]
