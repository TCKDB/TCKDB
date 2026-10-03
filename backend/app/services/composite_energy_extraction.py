"""Upload-side hook: reconcile a composite calculation's energy with its output log.

Sibling of :mod:`app.services.sp_energy_extraction`, for calculations of type
``composite`` (ADR 0021, P3b). It reads the Gaussian composite summary block of
an attached output log and compares it with the deposited ``composite_result``
(:func:`app.services.composite_energy_reconciliation.reconcile_composite_energy`).

It is reached through ``try_reconcile_sp_energy_from_output_upload``, which every
path that persists an output-log artifact already calls (the artifacts route and
the computed-species / computed-reaction bundles), so coverage matches the
single-point hook without touching those call sites.

Best-effort and never raises, like its sibling: artifact upload is canonical.
It never writes an energy: a mismatch is a warning and nothing is filled from the
log (see the reconciliation module for why). It does record the *conclusion*, one
``calc_composite_log_check`` row per ``(calculation, log digest)``, so a read can
say whether a program run's number was confirmed (``composite_energy_verification``)
without parsing a log again.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import (
    Calculation,
    CalculationCompositeLogCheck,
    CalculationCompositeResult,
)
from app.db.models.common import CompositeLogOutcome
from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.fragments.artifact import ArtifactIn
from app.services.best_effort import isolated_best_effort
from app.services.composite_energy_reconciliation import (
    COMPOSITE_LOG_PARSER_VERSION,
    CompositeEnergyAction,
    reconcile_composite_energy,
)

logger = logging.getLogger(__name__)


def try_reconcile_composite_energy_from_output_log(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> UploadWarning | None:
    """Compare a composite calculation's deposited energy with an output-log artifact.

    The caller has already checked that the artifact is an output log and the
    calculation is of type ``composite``.

    :returns: The warning for a mismatch or a method mismatch, else ``None``
        (including for an unreadable log, whose reason is logged at INFO).
    """
    return isolated_best_effort(
        session,
        lambda: _reconcile(session, calculation, artifact_in),
        what=f"composite energy reconciliation for artifact '{artifact_in.filename}'",
    )


def _reconcile(
    session: Session,
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> UploadWarning | None:
    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        logger.warning(
            "composite energy reconciliation skipped: artifact '%s' could not be base64-decoded",
            artifact_in.filename,
        )
        return None
    text = content.decode("utf-8", errors="replace")

    result = session.get(CalculationCompositeResult, calculation.id)
    if result is None:
        return None
    level = session.get(LevelOfTheory, calculation.lot_id) if calculation.lot_id is not None else None

    outcome = reconcile_composite_energy(
        level_method=level.method if level is not None else None,
        e0_hartree=result.e0_hartree,
        electronic_energy_hartree=result.electronic_energy_hartree,
        recipe_zpe_hartree=result.recipe_zpe_hartree,
        log_text=text,
    )
    # Record the conclusion (never a number). The same bytes uploaded twice under one
    # parser version conclude the same thing about an immutable result, so the second observation is dropped.
    session.execute(
        pg_insert(CalculationCompositeLogCheck)
        .values(
            calculation_id=calculation.id,
            artifact_sha256=hashlib.sha256(content).hexdigest(),
            parser_version=COMPOSITE_LOG_PARSER_VERSION,
            outcome=CompositeLogOutcome(outcome.action.value),
        )
        .on_conflict_do_nothing()
    )
    if outcome.action is CompositeEnergyAction.unverifiable:
        logger.info(
            "composite energy for calculation id=%s left unverified by artifact '%s': %s",
            calculation.id,
            artifact_in.filename,
            outcome.unverifiable_reason,
        )
    return outcome.warning
