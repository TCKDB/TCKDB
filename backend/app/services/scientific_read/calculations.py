"""Service implementation for /api/v1/scientific/calculations/{handle}.

Default-shape calculation detail read. Heavy include sections (results,
dependencies, parameters, constraints, artifacts, geometries, validation,
scf_stability, scan, irc, path_search) are validated as legal include
tokens but not yet expanded — non-empty heavy includes are rejected with
422 ``include_not_implemented_yet`` to avoid silently advertising tokens
that do nothing. See ``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select
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
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationSCFStability,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
    CalculationSPResult,
    CalculationWavefunctionDiagnostic,
)
from app.db.models.common import (
    CalculationType,
    IRCDirection,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_calculation import (
    AvailableCalculationSections,
    CalculationArtifactSummary,
    CalculationConstraintSummary,
    CalculationCoreBlock,
    CalculationDependencySummary,
    CalculationDetailRequest,
    CalculationEvidenceProvenanceSummary,
    CalculationFreqResultSummary,
    CalculationGeometryLinkSummary,
    CalculationGeometryValidationSummary,
    CalculationIRCSummary,
    CalculationIRCResultSummary,
    CalculationOptResultSummary,
    CalculationOwnerSummary,
    CalculationParameterSummary,
    CalculationPathSearchResultSummary,
    CalculationPathSearchSummary,
    CalculationResultSummary,
    CalculationReviewEntry,
    CalculationSCFStabilitySummary,
    CalculationSPResultSummary,
    CalculationWavefunctionDiagnosticSummary,
    CalculationScanResultSummary,
    CalculationScanSummary,
    RequestEcho,
    ScanCoordinateSummary,
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


# Heavy include sections promised by the spec. Implemented tokens fall
# through to per-section loaders; not-yet-implemented tokens listed in
# ``_NOT_IMPLEMENTED_INCLUDE_TOKENS`` get a clear ``include_not_implemented_yet``
# error rather than the generic ``unknown_include_token``.
# Empty: every heavy include token has shipped a summary loader, and
# ``include=all`` now expands deterministically to every legal public
# token (see ``_LEGAL_INCLUDE_TOKENS`` minus ``_INTERNAL_INCLUDE_TOKENS``).
_NOT_IMPLEMENTED_INCLUDE_TOKENS: frozenset[str] = frozenset()
_HEAVY_INCLUDE_TOKENS: frozenset[str] = frozenset(
    {
        "results",
        "dependencies",
        "artifacts",
        "input_geometries",
        "output_geometries",
        "geometry_validation",
        "scf_stability",
        "wavefunction_diagnostic",
        "parameters",
        "constraints",
        "review",
        "scan",
        "irc",
        "path_search",
    }
)
_LEGAL_INCLUDE_TOKENS: set[str] = {
    *_HEAVY_INCLUDE_TOKENS,
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# Note: an earlier ``_reject_include_all`` policy guard lived here while
# the heavy includes were landing one at a time. It was removed when
# ``include=all`` flipped to a positive expansion handled by
# :func:`validate_includes` (which expands ``all`` to
# ``_LEGAL_INCLUDE_TOKENS - _INTERNAL_INCLUDE_TOKENS``). The deletion
# kept the existing positive ``include=all`` tests passing without any
# special-case service code.


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

    # Heavy include sections that haven't shipped yet — reject loudly so
    # callers don't think they were silently dropped. The set is empty
    # now that every public heavy token is implemented, but the guard
    # stays so adding a new deferred token in the future just needs a
    # one-line set update.
    requested_unimplemented = includes & _NOT_IMPLEMENTED_INCLUDE_TOKENS
    if requested_unimplemented:
        raise ValueError(
            "include_not_implemented_yet: include token(s) "
            f"{sorted(requested_unimplemented)!r} are reserved for "
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
    record = build_record(session, calc, includes, badge=badge)
    summary = review_summary([badge])

    return ScientificCalculationDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=summary,
        record=record,
    )


def build_record(
    session: Session,
    calc: Calculation,
    includes: set[str],
    *,
    badge: RecordReviewBadge | None = None,
) -> ScientificCalculationRecord:
    """Construct a public ``ScientificCalculationRecord`` for *calc*.

    Shared between the calculation detail endpoint and the calculation
    search endpoint so both surfaces produce identical record shapes
    for the same include set.

    *includes* must be the **resolved** include set (post-validation,
    post-Phase-D internal-ids policy) — this builder does not validate
    it. *badge* may be supplied by callers that already loaded badges
    in bulk; otherwise this builder fetches the calc's badge itself.
    """
    if badge is None:
        badge = _load_review_badge(session, calc.id)

    owner = _build_owner(session, calc)
    lot_summary = _build_lot_summary(session, calc.lot_id)
    software_summary = _build_software_summary(session, calc.software_release_id)
    workflow_summary = _build_workflow_summary(
        session, calc.workflow_tool_release_id
    )
    literature_summary = _build_literature_summary(session, calc.literature_id)
    provenance, available = _build_provenance_and_sections(
        session, calc, calc.id
    )

    results_summary: CalculationResultSummary | None = None
    if "results" in includes:
        results_summary = _build_result_summary(session, calc, calc.id)

    dependencies_block: list[CalculationDependencySummary] | None = None
    if "dependencies" in includes:
        dependencies_block = _build_dependencies(session, calc.id)

    artifacts_block: list[CalculationArtifactSummary] | None = None
    if "artifacts" in includes:
        artifacts_block = _build_artifacts(session, calc.id)

    input_geometries_block: list[CalculationGeometryLinkSummary] | None = None
    if "input_geometries" in includes:
        input_geometries_block = _build_input_geometries(session, calc.id)

    output_geometries_block: list[CalculationGeometryLinkSummary] | None = None
    if "output_geometries" in includes:
        output_geometries_block = _build_output_geometries(session, calc.id)

    geometry_validation_block: (
        list[CalculationGeometryValidationSummary] | None
    ) = None
    if "geometry_validation" in includes:
        geometry_validation_block = _build_geometry_validation(
            session, calc.id
        )

    scf_stability_block: list[CalculationSCFStabilitySummary] | None = None
    if "scf_stability" in includes:
        scf_stability_block = _build_scf_stability(session, calc.id)

    wavefunction_diagnostic_block: (
        list[CalculationWavefunctionDiagnosticSummary] | None
    ) = None
    if "wavefunction_diagnostic" in includes:
        wavefunction_diagnostic_block = _build_wavefunction_diagnostic(
            session, calc.id
        )

    parameters_block: list[CalculationParameterSummary] | None = None
    if "parameters" in includes:
        parameters_block = _build_parameters(session, calc.id)

    constraints_block: list[CalculationConstraintSummary] | None = None
    if "constraints" in includes:
        constraints_block = _build_constraints(session, calc.id)

    review_history_block: list[CalculationReviewEntry] | None = None
    if "review" in includes:
        review_history_block = _build_review_history(session, calc.id)

    scan_block: CalculationScanSummary | None = None
    if "scan" in includes:
        scan_block = _build_scan_include_summary(session, calc.id)

    irc_block: CalculationIRCSummary | None = None
    if "irc" in includes:
        irc_block = _build_irc_include_summary(session, calc.id)

    path_search_block: CalculationPathSearchSummary | None = None
    if "path_search" in includes:
        path_search_block = _build_path_search_include_summary(
            session, calc.id
        )

    return ScientificCalculationRecord(
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
        results=results_summary,
        dependencies=dependencies_block,
        artifacts=artifacts_block,
        input_geometries=input_geometries_block,
        output_geometries=output_geometries_block,
        geometry_validation=geometry_validation_block,
        scf_stability=scf_stability_block,
        wavefunction_diagnostic=wavefunction_diagnostic_block,
        parameters=parameters_block,
        constraints=constraints_block,
        review_history=review_history_block,
        scan=scan_block,
        irc=irc_block,
        path_search=path_search_block,
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

    has_wavefunction_diagnostic = _exists_for_calc(
        session, CalculationWavefunctionDiagnostic, calculation_id
    )

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
        has_wavefunction_diagnostic=has_wavefunction_diagnostic,
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
    # Note: ``calc_irc_result`` and ``calc_scan_result`` do not store a
    # ``converged`` flag today — return None for those types so the
    # provenance summary surfaces "unknown" rather than fabricating one.
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
    submission_ref = session.scalar(
        select(Submission.public_ref).where(Submission.id == submission_id)
    )
    return submission_id, submission_ref


# ---------------------------------------------------------------------------
# include=results loaders
# ---------------------------------------------------------------------------


def _build_result_summary(
    session: Session, calc: Calculation, calculation_id: int
) -> CalculationResultSummary | None:
    """Load the matching primary-result row and project a public summary.

    Returns ``None`` when the calculation type has no result row (yet) or
    when the type does not have a primary result table (``conf``). Heavy
    point / mode arrays are intentionally omitted; those are the
    responsibility of ``include=scan`` / ``include=irc`` /
    ``include=path_search`` in later PRs.
    """
    builder = _RESULT_BUILDERS.get(calc.type)
    if builder is None:
        return None
    return builder(session, calculation_id)


def _build_sp_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationSPResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="sp",
        sp=CalculationSPResultSummary(
            electronic_energy_hartree=row.electronic_energy_hartree,
            electronic_energy_uncertainty_hartree=(
                row.electronic_energy_uncertainty_hartree
            ),
        ),
    )


def _build_opt_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationOptResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="opt",
        opt=CalculationOptResultSummary(
            converged=row.converged,
            n_steps=row.n_steps,
            final_energy_hartree=row.final_energy_hartree,
        ),
    )


def _build_freq_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationFreqResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="freq",
        freq=CalculationFreqResultSummary(
            n_imag=row.n_imag,
            imag_freq_cm1=row.imag_freq_cm1,
            zpe_hartree=row.zpe_hartree,
            zpe_uncertainty_hartree=row.zpe_uncertainty_hartree,
        ),
    )


def _build_scan_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationScanResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="scan",
        scan=CalculationScanResultSummary(
            dimension=row.dimension,
            is_relaxed=row.is_relaxed,
            zero_energy_reference_hartree=row.zero_energy_reference_hartree,
            note=row.note,
        ),
    )


def _build_irc_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationIRCResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="irc",
        irc=CalculationIRCResultSummary(
            direction=row.direction,
            has_forward=row.has_forward,
            has_reverse=row.has_reverse,
            ts_point_index=row.ts_point_index,
            point_count=row.point_count,
            zero_energy_reference_hartree=row.zero_energy_reference_hartree,
            note=row.note,
        ),
    )


def _build_path_search_summary(
    session: Session, calculation_id: int
) -> CalculationResultSummary | None:
    row = session.get(CalculationPathSearchResult, calculation_id)
    if row is None:
        return None
    return CalculationResultSummary(
        kind="path_search",
        path_search=CalculationPathSearchResultSummary(
            method=row.method,
            is_double_ended=row.is_double_ended,
            converged=row.converged,
            n_points=row.n_points,
            selected_ts_point_index=row.selected_ts_point_index,
            climbing_image_index=row.climbing_image_index,
            source_endpoint_count=row.source_endpoint_count,
            zero_energy_reference_hartree=row.zero_energy_reference_hartree,
            note=row.note,
        ),
    )


# ---------------------------------------------------------------------------
# include=dependencies loader
# ---------------------------------------------------------------------------


def _build_dependencies(
    session: Session, calculation_id: int
) -> list[CalculationDependencySummary]:
    """Return every dependency edge connected to *calculation_id*.

    Each row in ``calculation_dependency`` becomes one
    ``CalculationDependencySummary``. ``direction`` is set relative to
    the requested calculation:

    - ``"parent"`` when the requested calc is the edge's parent
      (i.e. other calcs depend on it),
    - ``"child"`` when the requested calc is the edge's child
      (i.e. it depends on other calcs).

    Public refs for the connected calculations are bulk-loaded in a
    single SQL round-trip so the include scales with edge count rather
    than degree-by-degree round-trips.

    Ordering (deterministic, doc'd in tests):

        ``dependency_role ASC``,
        ``direction ASC`` (``child`` < ``parent`` lexicographically),
        ``parent_calculation_id ASC``,
        ``child_calculation_id ASC``.
    """
    rows = session.execute(
        select(
            CalculationDependency.dependency_role,
            CalculationDependency.parent_calculation_id,
            CalculationDependency.child_calculation_id,
        ).where(
            (CalculationDependency.parent_calculation_id == calculation_id)
            | (CalculationDependency.child_calculation_id == calculation_id)
        )
    ).all()

    if not rows:
        return []

    # Bulk-load refs for every connected calculation in one shot.
    other_ids: set[int] = set()
    for row in rows:
        other_ids.add(row.parent_calculation_id)
        other_ids.add(row.child_calculation_id)
    refs_by_id: dict[int, str] = dict(
        session.execute(
            select(Calculation.id, Calculation.public_ref).where(
                Calculation.id.in_(other_ids)
            )
        ).all()
    )

    summaries: list[CalculationDependencySummary] = []
    for row in rows:
        parent_id = row.parent_calculation_id
        child_id = row.child_calculation_id
        direction = "parent" if parent_id == calculation_id else "child"
        summaries.append(
            CalculationDependencySummary(
                role=row.dependency_role,
                direction=direction,
                parent_calculation_ref=refs_by_id[parent_id],
                child_calculation_ref=refs_by_id[child_id],
                parent_calculation_id=parent_id,
                child_calculation_id=child_id,
            )
        )

    summaries.sort(
        key=lambda s: (
            s.role.value,
            s.direction,
            s.parent_calculation_id or 0,
            s.child_calculation_id or 0,
        )
    )
    return summaries


# ---------------------------------------------------------------------------
# include=artifacts loader
# ---------------------------------------------------------------------------


def _build_artifacts(
    session: Session, calculation_id: int
) -> list[CalculationArtifactSummary]:
    """Return artifact-metadata rows for *calculation_id*.

    **Metadata only.** No body bytes, no presigned URLs. The persisted
    ``uri`` is exposed verbatim — that's a storage URI (e.g.
    ``s3://bucket/key``), not a downloadable URL; resolving it to a
    download is an artifact-service responsibility outside this read.

    ``artifact_ref`` is always ``None`` because ``calculation_artifact``
    has no ``public_ref`` column today (see open question 1 in the
    spec). Adding the column later does not break this contract.

    Ordering (deterministic, doc'd in tests):

        ``kind ASC``,
        ``created_at ASC NULLS LAST``,
        ``id ASC``.
    """
    rows = session.execute(
        select(CalculationArtifact)
        .where(CalculationArtifact.calculation_id == calculation_id)
        .order_by(
            CalculationArtifact.kind.asc(),
            CalculationArtifact.created_at.asc().nullslast(),
            CalculationArtifact.id.asc(),
        )
    ).scalars().all()

    return [
        CalculationArtifactSummary(
            artifact_id=row.id,
            artifact_ref=None,  # no public_ref column on calculation_artifact yet
            kind=row.kind,
            uri=row.uri,
            filename=row.filename,
            sha256=row.sha256,
            bytes=row.bytes,
            created_at=row.created_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# include=input_geometries / include=output_geometries loaders
# ---------------------------------------------------------------------------


def _build_input_geometries(
    session: Session, calculation_id: int
) -> list[CalculationGeometryLinkSummary]:
    """Return input-geometry links for *calculation_id*.

    Each row is projected to a public link summary carrying
    ``geometry_ref`` plus a tiny amount of cheap metadata. **No XYZ
    text and no per-atom arrays** — those live behind
    ``/scientific/geometries/{geometry_ref}``.

    Ordering: ``input_order ASC, geometry_id ASC`` (matches the
    composite primary key on ``calculation_input_geometry``).
    """
    rows = session.execute(
        select(
            CalculationInputGeometry.geometry_id,
            CalculationInputGeometry.input_order,
            Geometry.public_ref,
            Geometry.natoms,
            Geometry.geom_hash,
        )
        .join(Geometry, Geometry.id == CalculationInputGeometry.geometry_id)
        .where(CalculationInputGeometry.calculation_id == calculation_id)
        .order_by(
            CalculationInputGeometry.input_order.asc(),
            CalculationInputGeometry.geometry_id.asc(),
        )
    ).all()

    return [
        CalculationGeometryLinkSummary(
            geometry_id=row.geometry_id,
            geometry_ref=row.public_ref,
            input_order=row.input_order,
            output_order=None,
            role=None,
            natoms=row.natoms,
            geom_hash=row.geom_hash,
        )
        for row in rows
    ]


def _build_output_geometries(
    session: Session, calculation_id: int
) -> list[CalculationGeometryLinkSummary]:
    """Return output-geometry links for *calculation_id*.

    Output links carry an optional ``CalculationGeometryRole`` (final,
    initial, scan_point, irc_forward, irc_reverse, path_search_point);
    input links don't have a role column.

    Ordering: ``output_order ASC, geometry_id ASC``.
    """
    rows = session.execute(
        select(
            CalculationOutputGeometry.geometry_id,
            CalculationOutputGeometry.output_order,
            CalculationOutputGeometry.role,
            Geometry.public_ref,
            Geometry.natoms,
            Geometry.geom_hash,
        )
        .join(Geometry, Geometry.id == CalculationOutputGeometry.geometry_id)
        .where(CalculationOutputGeometry.calculation_id == calculation_id)
        .order_by(
            CalculationOutputGeometry.output_order.asc(),
            CalculationOutputGeometry.geometry_id.asc(),
        )
    ).all()

    return [
        CalculationGeometryLinkSummary(
            geometry_id=row.geometry_id,
            geometry_ref=row.public_ref,
            input_order=None,
            output_order=row.output_order,
            role=row.role,
            natoms=row.natoms,
            geom_hash=row.geom_hash,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# include=constraints loader
# ---------------------------------------------------------------------------


def _build_constraints(
    session: Session, calculation_id: int
) -> list[CalculationConstraintSummary]:
    """Return ``calculation_constraint`` rows for *calculation_id*.

    Returns ``[]`` when the calc has no constraints. Heavy fields are
    not introduced — only the row's declared columns plus the
    ``atom_indices`` convenience list.

    Ordering (deterministic, doc'd in tests):

        ``constraint_index ASC``,
        ``constraint_kind ASC``,
        ``atom1_index ASC``,
        ``atom2_index ASC NULLS LAST``,
        ``atom3_index ASC NULLS LAST``,
        ``atom4_index ASC NULLS LAST``.
    """
    rows = session.execute(
        select(CalculationConstraint)
        .where(CalculationConstraint.calculation_id == calculation_id)
        .order_by(
            CalculationConstraint.constraint_index.asc(),
            CalculationConstraint.constraint_kind.asc(),
            CalculationConstraint.atom1_index.asc(),
            CalculationConstraint.atom2_index.asc().nullslast(),
            CalculationConstraint.atom3_index.asc().nullslast(),
            CalculationConstraint.atom4_index.asc().nullslast(),
        )
    ).scalars().all()

    return [
        CalculationConstraintSummary(
            calculation_id=row.calculation_id,
            constraint_index=row.constraint_index,
            constraint_kind=row.constraint_kind,
            atom1_index=row.atom1_index,
            atom2_index=row.atom2_index,
            atom3_index=row.atom3_index,
            atom4_index=row.atom4_index,
            atom_indices=[
                idx
                for idx in (
                    row.atom1_index,
                    row.atom2_index,
                    row.atom3_index,
                    row.atom4_index,
                )
                if idx is not None
            ],
            target_value=row.target_value,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# include=scan summary loader
# ---------------------------------------------------------------------------


def _build_scan_include_summary(
    session: Session, calculation_id: int
) -> CalculationScanSummary | None:
    """Return the bounded ``include=scan`` summary for *calculation_id*.

    Returns ``None`` (singleton, not ``[]``) when no
    ``calc_scan_result`` row exists for the calc — the schema PK is
    ``calculation_id`` so there is at most one.

    **No per-point arrays, no per-point geometry refs, no
    coordinate-value rows.** Aggregates (`coordinate_count`,
    `point_count`, `min/max_*_energy`) come from cheap SQL aggregates
    so the include is safe to populate on a search page. Full
    trajectory data lives behind the future specialized endpoint.
    """
    result_row = session.scalar(
        select(CalculationScanResult).where(
            CalculationScanResult.calculation_id == calculation_id
        )
    )
    if result_row is None:
        return None

    coord_rows = session.execute(
        select(CalculationScanCoordinate)
        .where(CalculationScanCoordinate.calculation_id == calculation_id)
        .order_by(CalculationScanCoordinate.coordinate_index.asc())
    ).scalars().all()
    coordinate_count = len(coord_rows)

    point_count = (
        session.scalar(
            select(func.count())
            .select_from(CalculationScanPoint)
            .where(CalculationScanPoint.calculation_id == calculation_id)
        )
        or 0
    )

    energy_aggregates = session.execute(
        select(
            func.min(CalculationScanPoint.electronic_energy_hartree),
            func.max(CalculationScanPoint.electronic_energy_hartree),
            func.min(CalculationScanPoint.relative_energy_kj_mol),
            func.max(CalculationScanPoint.relative_energy_kj_mol),
        ).where(CalculationScanPoint.calculation_id == calculation_id)
    ).one()
    min_e_h, max_e_h, min_rel, max_rel = energy_aggregates

    coordinates = [
        ScanCoordinateSummary(
            coordinate_index=row.coordinate_index,
            coordinate_kind=row.coordinate_kind,
            atom1_index=row.atom1_index,
            atom2_index=row.atom2_index,
            atom3_index=row.atom3_index,
            atom4_index=row.atom4_index,
            atom_indices=[
                idx
                for idx in (
                    row.atom1_index,
                    row.atom2_index,
                    row.atom3_index,
                    row.atom4_index,
                )
                if idx is not None
            ],
            step_count=row.step_count,
            step_size=row.step_size,
            start_value=row.start_value,
            end_value=row.end_value,
            value_unit=row.value_unit,
            resolution_degrees=row.resolution_degrees,
            symmetry_number=row.symmetry_number,
        )
        for row in coord_rows
    ]

    return CalculationScanSummary(
        dimension=result_row.dimension,
        is_relaxed=result_row.is_relaxed,
        zero_energy_reference_hartree=result_row.zero_energy_reference_hartree,
        note=result_row.note,
        coordinate_count=coordinate_count,
        point_count=point_count,
        coordinates=coordinates,
        min_electronic_energy_hartree=min_e_h,
        max_electronic_energy_hartree=max_e_h,
        min_relative_energy_kj_mol=min_rel,
        max_relative_energy_kj_mol=max_rel,
    )


# ---------------------------------------------------------------------------
# include=path_search summary loader
# ---------------------------------------------------------------------------


def _build_path_search_include_summary(
    session: Session, calculation_id: int
) -> CalculationPathSearchSummary | None:
    """Return the bounded ``include=path_search`` summary for *calculation_id*.

    Returns ``None`` (singleton) when no ``calc_path_search_result``
    row exists.

    **No per-point arrays, no per-point geometry refs, no
    path-coordinate arrays.** Aggregates come from cheap SQL queries
    on ``calc_path_search_point``.

    The schema carries two independent point-marker booleans
    (``is_ts_guess`` and ``is_climbing_image``) that can be set
    independently. NEB normally sets both on the climbing image; GSM
    and string methods only have ``is_ts_guess``. The summary surfaces
    both counts separately so callers don't have to guess which marker
    a given algorithm uses.
    """
    result_row = session.scalar(
        select(CalculationPathSearchResult).where(
            CalculationPathSearchResult.calculation_id == calculation_id
        )
    )
    if result_row is None:
        return None

    stored_point_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationPathSearchPoint)
            .where(
                CalculationPathSearchPoint.calculation_id == calculation_id
            )
        )
        or 0
    )
    ts_guess_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationPathSearchPoint)
            .where(
                CalculationPathSearchPoint.calculation_id == calculation_id,
                CalculationPathSearchPoint.is_ts_guess.is_(True),
            )
        )
        or 0
    )
    climbing_image_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationPathSearchPoint)
            .where(
                CalculationPathSearchPoint.calculation_id == calculation_id,
                CalculationPathSearchPoint.is_climbing_image.is_(True),
            )
        )
        or 0
    )

    energy_aggregates = session.execute(
        select(
            func.min(CalculationPathSearchPoint.electronic_energy_hartree),
            func.max(CalculationPathSearchPoint.electronic_energy_hartree),
            func.min(CalculationPathSearchPoint.relative_energy_kj_mol),
            func.max(CalculationPathSearchPoint.relative_energy_kj_mol),
            func.min(CalculationPathSearchPoint.path_coordinate),
            func.max(CalculationPathSearchPoint.path_coordinate),
        ).where(CalculationPathSearchPoint.calculation_id == calculation_id)
    ).one()
    (
        min_e_h,
        max_e_h,
        min_rel,
        max_rel,
        min_pc,
        max_pc,
    ) = energy_aggregates

    return CalculationPathSearchSummary(
        method=result_row.method,
        is_double_ended=result_row.is_double_ended,
        converged=result_row.converged,
        n_points=result_row.n_points,
        selected_ts_point_index=result_row.selected_ts_point_index,
        climbing_image_index=result_row.climbing_image_index,
        source_endpoint_count=result_row.source_endpoint_count,
        zero_energy_reference_hartree=result_row.zero_energy_reference_hartree,
        note=result_row.note,
        stored_point_count=stored_point_count,
        ts_guess_count=ts_guess_count,
        climbing_image_count=climbing_image_count,
        min_electronic_energy_hartree=min_e_h,
        max_electronic_energy_hartree=max_e_h,
        min_relative_energy_kj_mol=min_rel,
        max_relative_energy_kj_mol=max_rel,
        min_path_coordinate=min_pc,
        max_path_coordinate=max_pc,
    )


# ---------------------------------------------------------------------------
# include=irc summary loader
# ---------------------------------------------------------------------------


def _build_irc_include_summary(
    session: Session, calculation_id: int
) -> CalculationIRCSummary | None:
    """Return the bounded ``include=irc`` summary for *calculation_id*.

    Returns ``None`` (singleton, not ``[]``) when no
    ``calc_irc_result`` row exists for the calc — the schema PK is
    ``calculation_id`` so there is at most one.

    **No per-point arrays, no per-point geometry refs, no
    reaction-coordinate arrays.** Aggregates come from cheap SQL
    queries on ``calc_irc_point``. Direction-counting policy:
    ``forward`` and ``reverse`` rows count toward their direction;
    rows with ``direction = both`` or ``direction IS NULL`` (e.g. the
    ORCA TS marker) are not double-counted. ``ts_point_count`` is
    independent and counts every row with ``is_ts = True``.
    """
    result_row = session.scalar(
        select(CalculationIRCResult).where(
            CalculationIRCResult.calculation_id == calculation_id
        )
    )
    if result_row is None:
        return None

    forward_point_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationIRCPoint)
            .where(
                CalculationIRCPoint.calculation_id == calculation_id,
                CalculationIRCPoint.direction == IRCDirection.forward,
            )
        )
        or 0
    )
    reverse_point_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationIRCPoint)
            .where(
                CalculationIRCPoint.calculation_id == calculation_id,
                CalculationIRCPoint.direction == IRCDirection.reverse,
            )
        )
        or 0
    )
    ts_point_count = int(
        session.scalar(
            select(func.count())
            .select_from(CalculationIRCPoint)
            .where(
                CalculationIRCPoint.calculation_id == calculation_id,
                CalculationIRCPoint.is_ts.is_(True),
            )
        )
        or 0
    )

    energy_aggregates = session.execute(
        select(
            func.min(CalculationIRCPoint.electronic_energy_hartree),
            func.max(CalculationIRCPoint.electronic_energy_hartree),
            func.min(CalculationIRCPoint.relative_energy_kj_mol),
            func.max(CalculationIRCPoint.relative_energy_kj_mol),
            func.min(CalculationIRCPoint.reaction_coordinate),
            func.max(CalculationIRCPoint.reaction_coordinate),
        ).where(CalculationIRCPoint.calculation_id == calculation_id)
    ).one()
    (
        min_e_h,
        max_e_h,
        min_rel,
        max_rel,
        min_rc,
        max_rc,
    ) = energy_aggregates

    return CalculationIRCSummary(
        direction=result_row.direction,
        has_forward=result_row.has_forward,
        has_reverse=result_row.has_reverse,
        ts_point_index=result_row.ts_point_index,
        point_count=result_row.point_count,
        zero_energy_reference_hartree=result_row.zero_energy_reference_hartree,
        note=result_row.note,
        forward_point_count=forward_point_count,
        reverse_point_count=reverse_point_count,
        ts_point_count=ts_point_count,
        min_electronic_energy_hartree=min_e_h,
        max_electronic_energy_hartree=max_e_h,
        min_relative_energy_kj_mol=min_rel,
        max_relative_energy_kj_mol=max_rel,
        min_reaction_coordinate=min_rc,
        max_reaction_coordinate=max_rc,
    )


# ---------------------------------------------------------------------------
# include=review loader
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session, calculation_id: int
) -> list[CalculationReviewEntry]:
    """Return ``record_review`` rows for *calculation_id*.

    The schema enforces ``UNIQUE (record_type, record_id)``, so this
    function returns zero or one entry. The list shape matches other
    singleton-list includes (``geometry_validation``, ``scf_stability``).

    The compact :class:`RecordReviewBadge` on
    ``CalculationCoreBlock.review`` is unaffected — it is the
    always-present trust signal; this include adds the curator-context
    payload (note, submission link, reviewer ids when policy permits).

    Ordering: ``created_at ASC NULLS LAST, id ASC`` — defined for
    forward-compatibility if a future schema relaxation allows
    multiple review rows per record.
    """
    rows = session.execute(
        select(RecordReview)
        .where(
            RecordReview.record_type == SubmissionRecordType.calculation,
            RecordReview.record_id == calculation_id,
        )
        .order_by(
            RecordReview.created_at.asc().nullslast(),
            RecordReview.id.asc(),
        )
    ).scalars().all()

    if not rows:
        return []

    submission_ids = {r.submission_id for r in rows if r.submission_id is not None}
    submission_refs: dict[int, str] = {}
    if submission_ids:
        submission_refs = dict(
            session.execute(
                select(Submission.id, Submission.public_ref).where(
                    Submission.id.in_(submission_ids)
                )
            ).all()
        )

    return [
        CalculationReviewEntry(
            status=row.status,
            note=row.note,
            reviewed_at=row.reviewed_at,
            submission_ref=(
                submission_refs.get(row.submission_id)
                if row.submission_id is not None
                else None
            ),
            review_id=row.id,
            reviewer_id=row.reviewed_by,
            submission_id=row.submission_id,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# include=parameters loader
# ---------------------------------------------------------------------------


def _build_parameters(
    session: Session, calculation_id: int
) -> list[CalculationParameterSummary]:
    """Return parsed ``calculation_parameter`` rows for *calculation_id*.

    Returns ``[]`` when the calc has no parameters (the include is
    populated as an empty list, not omitted, so callers can tell
    "asked but none" from "did not ask").

    Ordering (deterministic, doc'd in tests):

        ``section ASC NULLS LAST``,
        ``parameter_index ASC NULLS LAST``,
        ``raw_key ASC``,
        ``id ASC``.
    """
    rows = session.execute(
        select(CalculationParameter)
        .where(CalculationParameter.calculation_id == calculation_id)
        .order_by(
            CalculationParameter.section.asc().nullslast(),
            CalculationParameter.parameter_index.asc().nullslast(),
            CalculationParameter.raw_key.asc(),
            CalculationParameter.id.asc(),
        )
    ).scalars().all()

    return [
        CalculationParameterSummary(
            parameter_id=row.id,
            raw_key=row.raw_key,
            raw_value=row.raw_value,
            canonical_key=row.canonical_key,
            canonical_value=row.canonical_value,
            section=row.section,
            value_type=row.value_type,
            unit=row.unit,
            parameter_index=row.parameter_index,
            created_at=row.created_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# include=geometry_validation / include=scf_stability loaders
# ---------------------------------------------------------------------------


def _build_geometry_validation(
    session: Session, calculation_id: int
) -> list[CalculationGeometryValidationSummary]:
    """Return the geometry-validation row for *calculation_id* as a list.

    The schema constrains at most one row per calculation, so the
    returned list contains zero or one entry. The full ``atom_mapping``
    JSONB is **not** exposed in this MVP.

    ``input_geometry_ref`` / ``output_geometry_ref`` are populated when
    the corresponding geometry exists. Refs are loaded in a single
    additional SELECT to keep the include cheap regardless of how many
    geometries the calc references.
    """
    row = session.scalar(
        select(CalculationGeometryValidation).where(
            CalculationGeometryValidation.calculation_id == calculation_id
        )
    )
    if row is None:
        return []

    geom_ids = [
        gid
        for gid in (row.input_geometry_id, row.output_geometry_id)
        if gid is not None
    ]
    refs_by_id: dict[int, str] = {}
    if geom_ids:
        refs_by_id = dict(
            session.execute(
                select(Geometry.id, Geometry.public_ref).where(
                    Geometry.id.in_(geom_ids)
                )
            ).all()
        )

    return [
        CalculationGeometryValidationSummary(
            input_geometry_id=row.input_geometry_id,
            input_geometry_ref=refs_by_id.get(row.input_geometry_id)
            if row.input_geometry_id is not None
            else None,
            output_geometry_id=row.output_geometry_id,
            output_geometry_ref=refs_by_id.get(row.output_geometry_id)
            if row.output_geometry_id is not None
            else None,
            species_smiles=row.species_smiles,
            is_isomorphic=row.is_isomorphic,
            rmsd=row.rmsd,
            n_mappings=row.n_mappings,
            validation_status=row.validation_status,
            validation_reason=row.validation_reason,
            rmsd_warning_threshold=row.rmsd_warning_threshold,
            created_at=row.created_at,
        )
    ]


def _build_scf_stability(
    session: Session, calculation_id: int
) -> list[CalculationSCFStabilitySummary]:
    """Return the SCF-stability row for *calculation_id* as a list.

    The schema constrains at most one row per calculation. Absence
    means "not checked" and is reflected by an empty list (the cheap
    provenance summary on the default record uses the same encoding).

    ``source_calculation_ref`` is loaded when the FK is set;
    ``source_artifact_ref`` is always ``None`` because
    ``calculation_artifact`` has no ``public_ref`` column today.
    """
    row = session.scalar(
        select(CalculationSCFStability).where(
            CalculationSCFStability.calculation_id == calculation_id
        )
    )
    if row is None:
        return []

    source_calc_ref: str | None = None
    if row.source_calculation_id is not None:
        source_calc_ref = session.scalar(
            select(Calculation.public_ref).where(
                Calculation.id == row.source_calculation_id
            )
        )

    return [
        CalculationSCFStabilitySummary(
            status=row.status,
            lowest_eigenvalue=row.lowest_eigenvalue,
            instability_count=row.instability_count,
            instability_type=row.instability_type,
            reoptimized_wavefunction=row.reoptimized_wavefunction,
            note=row.note,
            created_at=row.created_at,
            source_calculation_id=row.source_calculation_id,
            source_calculation_ref=source_calc_ref,
            source_artifact_id=row.source_artifact_id,
            source_artifact_ref=None,
        )
    ]


def _build_wavefunction_diagnostic(
    session: Session, calculation_id: int
) -> list[CalculationWavefunctionDiagnosticSummary]:
    """Return the wavefunction-diagnostic row for *calculation_id* as a list.

    The schema constrains at most one row per calculation. Absence
    reads as "not parsed / not applicable / not reported" via an empty
    list (mirrors the geometry-validation / scf-stability include
    pattern). Thresholds for interpreting T1/D1 are deliberately not
    applied here.
    """
    row = session.scalar(
        select(CalculationWavefunctionDiagnostic).where(
            CalculationWavefunctionDiagnostic.calculation_id == calculation_id
        )
    )
    if row is None:
        return []
    return [
        CalculationWavefunctionDiagnosticSummary(
            t1_diagnostic=row.t1_diagnostic,
            d1_diagnostic=row.d1_diagnostic,
            t1_norm=row.t1_norm,
            largest_t2_amplitude=row.largest_t2_amplitude,
            note=row.note,
            created_at=row.created_at,
        )
    ]


_RESULT_BUILDERS: dict[CalculationType, callable] = {
    CalculationType.sp: _build_sp_summary,
    CalculationType.opt: _build_opt_summary,
    CalculationType.freq: _build_freq_summary,
    CalculationType.scan: _build_scan_summary,
    CalculationType.irc: _build_irc_summary,
    CalculationType.path_search: _build_path_search_summary,
}


__all__ = [
    "build_record",
    "get_calculation",
    "_HEAVY_INCLUDE_TOKENS",
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "_NOT_IMPLEMENTED_INCLUDE_TOKENS",
]
