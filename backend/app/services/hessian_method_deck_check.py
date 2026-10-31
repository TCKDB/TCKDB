"""Warn when a deck uploaded later states a Hessian method the stored tolerance does not rest on.

A deck attached with the deposit is read before the deposit is judged. One
attached afterwards through the artifact route is not, so a calculation can
already carry a tau and basis that were decided without it. Stored rows are
append-only, so nothing is relabelled: when the later deck states a method whose
method (analytic, or finite differences of gradients or of energies) differs from the method the
stored basis rests on, a warning is appended to the calculation.

Only a disagreement warns. Agreement (including an ``assumed_*`` basis the deck
confirms), a deck with no frequency directive, a calculation with no judged
frequency result, and a ``protocol_not_recorded`` basis (which rests on no method)
warn nothing.

The message is built from the code and TCKDB's own values, never from deck text.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import deck_hessian_method
from tckdb_schemas.software import normalize_software_name
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.db.models.common import ArtifactKind
from app.schemas.fragments.artifact import ArtifactIn
from app.services.best_effort import isolated_best_effort

W_HESSIAN_METHOD_DISAGREES_WITH_STORED_TAU_BASIS = "hessian_method_disagrees_with_stored_tau_basis"

#: The method each stored basis rests on. ``protocol_not_recorded`` rests on none.
_BASIS_METHOD: dict[str, HessianMethod] = {
    "analytic_tight": HessianMethod.analytic,
    "analytic_default": HessianMethod.analytic,
    "assumed_analytic_default": HessianMethod.analytic,
    "finite_difference_gradient": HessianMethod.finite_difference_gradient,
    "assumed_finite_difference_gradient": HessianMethod.finite_difference_gradient,
    "finite_difference_energy": HessianMethod.finite_difference_energy,
    "assumed_finite_difference_energy": HessianMethod.finite_difference_energy,
}


def try_hessian_method_disagreement_from_input_upload(
    session: Session, calculation: Calculation, artifact_in: ArtifactIn
) -> UploadWarning | None:
    """The warning a later input deck earns against the stored tolerance basis, or ``None``.

    Best-effort, like its sibling hooks: the lazy loads run inside a savepoint
    (:func:`~app.services.best_effort.isolated_best_effort`), so a database error
    here cannot leave the upload's transaction aborted. Never raises.
    """
    if artifact_in.kind != ArtifactKind.input:
        return None
    return isolated_best_effort(
        session,
        lambda: _disagreement(calculation, artifact_in),
        what=f"hessian method / stored tau basis check for '{artifact_in.filename}'",
    )


def _disagreement(calculation: Calculation, artifact_in: ArtifactIn) -> UploadWarning | None:
    freq = calculation.freq_result
    if freq is None or freq.imaginary_mode_tau_basis is None:
        return None
    stored = _BASIS_METHOD.get(freq.imaginary_mode_tau_basis)
    if stored is None:
        return None
    release = calculation.software_release
    declared = normalize_software_name(release.software.name) if release is not None else None
    deck = deck_hessian_method(declared, [artifact_in])
    if deck is None or deck is stored:
        return None
    # TCKDB's own method tokens, so a gradient-versus-energy disagreement reads as one.
    return UploadWarning.from_code(
        field="artifacts",
        code=W_HESSIAN_METHOD_DISAGREES_WITH_STORED_TAU_BASIS,
        context={
            "stored_basis": freq.imaginary_mode_tau_basis,
            "stored_method": stored.value,
            "deck_method": deck.value,
        },
    )
