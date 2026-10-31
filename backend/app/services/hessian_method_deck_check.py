"""Warn when a deck uploaded later states a Hessian method the stored tolerance does not rest on.

A deck attached with the deposit is read before the deposit is judged. One
attached afterwards through the artifact route is not, so a calculation can
already carry a tau and basis that were decided without it. Stored rows are
append-only, so nothing is relabelled: when the later deck states a method whose
kind (analytic, or numerical from gradients or energies) differs from the kind the
stored basis rests on, a warning is appended to the calculation.

Only a disagreement warns. Agreement (including an ``assumed_*`` basis the deck
confirms), a deck with no frequency directive, a calculation with no judged
frequency result, and a ``protocol_not_recorded`` basis (which rests on no method)
warn nothing.

The message is built from the code and TCKDB's own values, never from deck text.
"""

from __future__ import annotations

import logging

from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import deck_hessian_method
from tckdb_schemas.software import normalize_software_name
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.db.models.common import ArtifactKind
from app.schemas.fragments.artifact import ArtifactIn

logger = logging.getLogger(__name__)

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


def _kind(method: HessianMethod) -> str:
    return "analytic" if method is HessianMethod.analytic else "numerical"


def try_hessian_method_disagreement_from_input_upload(
    calculation: Calculation, artifact_in: ArtifactIn
) -> UploadWarning | None:
    """The warning a later input deck earns against the stored tolerance basis, or ``None``.

    Never raises: an artifact upload is canonical and is not aborted by this check.
    """
    if artifact_in.kind != ArtifactKind.input:
        return None
    try:
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
        return UploadWarning(
            field="artifacts",
            code=W_HESSIAN_METHOD_DISAGREES_WITH_STORED_TAU_BASIS,
            message=(
                f"The tolerance stored with this frequency calculation rests on a {_kind(stored)} Hessian "
                f"({freq.imaginary_mode_tau_basis}), but the input deck attached afterwards states a "
                f"{_kind(deck)} one. The stored tolerance was not changed."
            ),
            context={
                "stored_basis": freq.imaginary_mode_tau_basis,
                "stored_method_kind": _kind(stored),
                "deck_method_kind": _kind(deck),
            },
        )
    except Exception:
        logger.warning("hessian method / stored tau basis check failed for artifact '%s'", artifact_in.filename)
        return None
