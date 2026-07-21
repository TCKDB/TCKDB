"""Upload-side hook: reconcile and fill a calculation's single-point energy.

Sibling to :mod:`app.services.calculation_parameter_extraction`. That hook
parses *input* artifacts into parameter rows; this one inspects *output-log*
artifacts and reconciles the single-point electronic energy the submitting
tool reported against the value re-derived from the log by
:func:`app.services.sp_energy_reconciliation.reconcile_sp_energy`:

* the tool omitted the energy -> fill ``calc_sp_result`` from the log
* the two disagree            -> return a warning to flag for review
  (the tool's reported value is kept, never overwritten)
* they agree, or the log cannot be re-parsed -> no action

**Scope (v1):** reconciliation runs on the dedicated artifacts route
(``POST /calculations/{id}/artifacts``), the canonical second-phase path for
uploading logs. Output logs attached *inline* through the contribution-bundle
workflows (``computed_species`` / ``computed_reaction``) are not yet
reconciled — those call sites have no channel to surface a warning, so wiring
fill-without-warning there would diverge from the route's behaviour. Extending
reconciliation to inline-bundle logs is tracked follow-up.

Best-effort and never raises: artifact upload is canonical and must not be
aborted by a reconciliation failure. Scoped to single-point calculations —
the single-point electronic energy is the relevant result only there.
"""

from __future__ import annotations

import base64
import binascii
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation, CalculationSPResult
from app.db.models.common import ArtifactKind, CalculationType
from app.schemas.fragments.artifact import ArtifactIn
from app.services.sp_energy_reconciliation import (
    SpEnergyAction,
    SpEnergyReconciliation,
    reconcile_sp_energy,
)

logger = logging.getLogger(__name__)


def try_reconcile_sp_energy_from_output_upload(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> UploadWarning | None:
    """Reconcile/fill the SP energy from an uploaded output-log artifact.

    Runs immediately after the matching ``CalculationArtifact`` row has
    been persisted, decoding bytes from the in-memory base64 payload so no
    object-storage round-trip is needed.

    Returns an :class:`UploadWarning` when the reconciliation produced one
    (a mismatch flagged for review, or an informational note that the
    energy was filled from the log), otherwise ``None``. Returns ``None``
    for non-output-log artifacts, non-single-point calculations, and any
    failure — a broad safety net guarantees the canonical artifact upload
    is never aborted by a reconciliation error, matching the sibling
    parameter-extraction hook.
    """
    # ``artifact_in.kind`` is typed against ``tckdb_schemas.enums.ArtifactKind``
    # while ``ArtifactKind`` here is the parallel ORM enum; both are
    # ``(str, Enum)`` with identical members, so a value comparison works
    # across the boundary (an identity check would always fail).
    if artifact_in.kind != ArtifactKind.output_log:
        return None
    if calculation.type != CalculationType.sp:
        return None

    try:
        return _reconcile_and_fill(session, calculation, artifact_in)
    except Exception:
        # Artifact upload is canonical and must never be aborted by a
        # reconciliation failure — swallow anything and log it.
        logger.warning(
            "sp_energy reconciliation failed for artifact '%s'",
            artifact_in.filename,
            exc_info=True,
        )
        return None


def _reconcile_and_fill(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> UploadWarning | None:
    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "sp_energy reconciliation skipped: artifact '%s' could not be "
            "base64-decoded",
            artifact_in.filename,
        )
        return None

    text = content.decode("utf-8", errors="replace")

    existing = calculation.sp_result
    payload_energy = (
        existing.electronic_energy_hartree if existing is not None else None
    )

    outcome = reconcile_sp_energy(
        payload_energy_hartree=payload_energy,
        log_text=text,
    )

    if outcome.action is SpEnergyAction.filled:
        return _fill(session, calculation, existing, outcome)
    if outcome.action is SpEnergyAction.mismatch:
        return outcome.warning
    return None


def _fill(
    session: Session,
    calculation: Calculation,
    existing: CalculationSPResult | None,
    outcome: SpEnergyReconciliation,
) -> UploadWarning | None:
    """Persist a log-derived energy where the tool supplied none."""
    energy = outcome.resolved_energy_hartree

    if existing is not None:
        # A ``calc_sp_result`` row already exists but carries a NULL energy
        # (the tool sent ``sp_result`` with no value). Fill it in place —
        # this supplies the missing value, it does not overwrite a reported
        # one, so the "filled" warning is accurate.
        existing.electronic_energy_hartree = energy
        return outcome.warning

    # No row yet — insert one inside a SAVEPOINT so a concurrent uploader
    # racing to fill the same energy-less calculation (PK is
    # ``calculation_id``) cannot poison the outer transaction with a
    # duplicate-key violation; the loser simply skips the fill.
    savepoint = session.begin_nested()
    try:
        session.add(
            CalculationSPResult(
                calculation=calculation,
                electronic_energy_hartree=energy,
            )
        )
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        session.expire(calculation, ["sp_result"])
        return None
    return outcome.warning
