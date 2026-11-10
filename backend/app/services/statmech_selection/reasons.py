"""The closed registry of reasons an assessment may give, and what each one means for the outcome.

A reason is a short code, optionally followed by ``:detail`` naming the fact or factor it is about
(``required_recipe_unknown:scale_application``). Every code is registered here once with the applicability it implies
and the outcome *category* it belongs to; an unregistered code is a programming error (``KeyError``), so the vocabulary a
client sees cannot grow by accident.

Categories are what a population summary reads (see :func:`~app.services.statmech_selection.assessment.summarize`):

``evidence_conflict``       trustworthy evidence disagrees with itself or with the claim's own support
``unsupported_model``       the representation is recognised and not evaluated
``missing_required_data``   a fact the claim needs was never stated, or its source is not available to this request
``unresolved_applicability`` applicability cannot be decided from what is stated (ambiguous alternatives, a cycle)
``known_incompatible``      something is known to be wrong for this request

Applicability precedence when several reasons apply to one candidate: incompatible, unsupported, unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.selection_kernel import Applicability


class Category(str, Enum):
    evidence_conflict = "evidence_conflict"
    unsupported_model = "unsupported_model"
    missing_required_data = "missing_required_data"
    unresolved_applicability = "unresolved_applicability"
    known_incompatible = "known_incompatible"


@dataclass(frozen=True)
class ReasonSpec:
    applicability: Applicability
    category: Category


_I, _S, _U = Applicability.incompatible, Applicability.unsupported, Applicability.unresolved
_C = Category

REASONS: dict[str, ReasonSpec] = {
    # --- representation ---------------------------------------------------------------------------------------
    "declaration_version_unsupported": ReasonSpec(_S, _C.unsupported_model),
    "treatment_not_supported": ReasonSpec(_S, _C.unsupported_model),
    "dividing_surface_not_supported": ReasonSpec(_S, _C.unsupported_model),
    "higher_order_saddle_flagged": ReasonSpec(_S, _C.unsupported_model),
    # --- scope and subject ------------------------------------------------------------------------------------
    "scope_incompatible": ReasonSpec(_I, _C.known_incompatible),
    "scope_unestablished": ReasonSpec(_U, _C.unresolved_applicability),
    "isotope_mismatch": ReasonSpec(_I, _C.known_incompatible),
    # --- availability of what the claim rests on --------------------------------------------------------------
    "required_recipe_unknown": ReasonSpec(_U, _C.missing_required_data),
    "required_evidence_unavailable": ReasonSpec(_U, _C.missing_required_data),
    "required_source_unusable": ReasonSpec(_U, _C.missing_required_data),
    "required_member_unavailable": ReasonSpec(_U, _C.missing_required_data),
    "relative_offsets_missing": ReasonSpec(_U, _C.missing_required_data),
    "scale_application_unknown": ReasonSpec(_U, _C.missing_required_data),
    # --- coherence --------------------------------------------------------------------------------------------
    "multiple_geometry_alternatives": ReasonSpec(_U, _C.unresolved_applicability),
    "witness_ambiguous": ReasonSpec(_U, _C.unresolved_applicability),
    "rotor_replacement_unstated": ReasonSpec(_U, _C.unresolved_applicability),
    "ambiguous_fact": ReasonSpec(_U, _C.unresolved_applicability),
    "curvature_witnesses_disagree": ReasonSpec(_U, _C.evidence_conflict),
    "finding_unresolved": ReasonSpec(_U, _C.evidence_conflict),
    # --- known failures ---------------------------------------------------------------------------------------
    "curvature_contradicts_claim": ReasonSpec(_I, _C.known_incompatible),
    "finding_invalidates": ReasonSpec(_I, _C.known_incompatible),
    "role_invalidated": ReasonSpec(_I, _C.known_incompatible),
    "required_rotor_invalidated": ReasonSpec(_I, _C.known_incompatible),
    "reaction_coordinate_mismatch": ReasonSpec(_I, _C.known_incompatible),
    "reaction_coordinate_not_excluded": ReasonSpec(_I, _C.known_incompatible),
    # --- the statistical accounting and conventions -----------------------------------------------------------
    "counting_conflict": ReasonSpec(_I, _C.known_incompatible),
    "required_factor_not_counted": ReasonSpec(_I, _C.known_incompatible),
    "scale_purpose_mismatch": ReasonSpec(_I, _C.known_incompatible),
    "scale_declaration_conflict": ReasonSpec(_I, _C.known_incompatible),
    "energy_reference_mismatch": ReasonSpec(_I, _C.known_incompatible),
    "energy_reference_conflict": ReasonSpec(_I, _C.known_incompatible),
    "normalization_mismatch": ReasonSpec(_I, _C.known_incompatible),
    "model_includes_translation": ReasonSpec(_I, _C.known_incompatible),
    "model_is_internal_only": ReasonSpec(_I, _C.known_incompatible),
    "domain_not_covered": ReasonSpec(_I, _C.known_incompatible),
}

#: Request-level reasons: the request asks for something this release does not evaluate (outcome ``unsupported_task``).
REQUEST_REASONS: tuple[str, ...] = (
    "task_deferred",
    "environment_not_supported",
    "isotope_convention_unsupported",
    "stereochemical_scope_unsupported",
    "dividing_surface_not_supported",
)

#: Advisory notes: disclosed, never excluding. Free-form detail after ``:`` is allowed.
ADVISORY_PREFIXES: tuple[str, ...] = (
    "geometry_validation_heuristic",
    "validity_domain_not_stated",
    "curvature_on_other_geometry",
    "finding_version_unsupported",
    "finding_kind_unsupported",
    "finding_on_unneeded_subject",
    "stationary_point_finding",
    "imaginary_modes_below_tau",
    "extra_imaginary_modes_below_tau",
    "optimization_not_converged",
    "optimization_convergence_unknown",
    "rotor_without_torsion_row",
    "member_energy_not_compared",
    "legacy_record_read_as_stored",
    "imported_parameters_not_revalidated",
    "source_literature_unidentified",
    "superseded_record",
    "source_not_linked_to_geometry_optimization",
    # A confirmed finding that would have excluded the candidate, kept off the competition because the request lifted
    # the ``confirmed_blocking_findings`` default. The finding is disclosed here, never hidden.
    "finding_overridden",
)


def base(code: str) -> str:
    return code.split(":", 1)[0]


def spec_of(code: str) -> ReasonSpec:
    """The registered meaning of a reason code (``KeyError`` for one nobody registered)."""
    return REASONS[base(code)]


def is_registered_advisory(code: str) -> bool:
    return any(code == p or code.startswith(f"{p}:") for p in ADVISORY_PREFIXES)
