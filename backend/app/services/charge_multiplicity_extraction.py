"""Upload-side hook: cross-check declared charge / multiplicity against the log.

Sibling to :mod:`app.services.sp_energy_extraction`. That hook reconciles the
single-point energy an uploading tool reported against the value re-derived
from the output log; this one does the same for the charge and spin
multiplicity declared on the species entry (or transition-state entry) the
calculation belongs to:

* the log states a different charge or multiplicity -> return a warning
* they agree, or the log cannot be re-read           -> no action

Nothing is ever written. The declared charge and multiplicity are part of
the entry's *identity* (``uq_species_identity`` is SMILES + charge +
multiplicity), so silently correcting them would silently repoint the upload
at a different species. TCKDB flags the contradiction and leaves the record
exactly as submitted, the same policy the single-point-energy hook applies
on a mismatch.

**Where it runs:** every path that persists an output-log artifact — the
dedicated artifacts route (``POST /calculations/{id}/artifacts``) and output
logs attached *inline* through the contribution-bundle workflows
(``computed_species`` / ``computed_reaction``, which ARC uses in bundle
mode), matching the coverage of the sibling hooks.

Best-effort and never raises: artifact upload is canonical and must not be
aborted by a reconciliation failure.
"""

from __future__ import annotations

import base64
import binascii
import logging

from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.db.models.common import ArtifactKind
from app.schemas.fragments.artifact import ArtifactIn
from app.services.charge_multiplicity_reconciliation import (
    reconcile_charge_multiplicity,
)

logger = logging.getLogger(__name__)


def try_reconcile_charge_multiplicity_from_output_upload(
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> list[UploadWarning]:
    """Cross-check the owning entry's charge/multiplicity against a log.

    Runs immediately after the matching ``CalculationArtifact`` row has been
    persisted, decoding bytes from the in-memory base64 payload so no
    object-storage round-trip is needed.

    Returns the warnings the reconciliation produced (at most one for
    charge and one for multiplicity), otherwise an empty list. Returns an
    empty list for non-output-log artifacts, calculations with no owning
    entry, and any failure — a broad safety net guarantees the canonical
    artifact upload is never aborted by a reconciliation error, matching
    the sibling extraction hooks.
    """
    # ``artifact_in.kind`` is typed against ``tckdb_schemas.enums.ArtifactKind``
    # while ``ArtifactKind`` here is the parallel ORM enum; both are
    # ``(str, Enum)`` with identical members, so a value comparison works
    # across the boundary (an identity check would always fail).
    if artifact_in.kind != ArtifactKind.output_log:
        return []

    try:
        return _reconcile(calculation, artifact_in)
    except Exception:
        # Artifact upload is canonical and must never be aborted by a
        # reconciliation failure — swallow anything and log it.
        logger.warning(
            "charge/multiplicity reconciliation failed for artifact '%s'",
            artifact_in.filename,
            exc_info=True,
        )
        return []


def _reconcile(
    calculation: Calculation,
    artifact_in: ArtifactIn,
) -> list[UploadWarning]:
    # A calculation is owned by exactly one of the two entry kinds (enforced
    # by a table check constraint). Where the declared charge/multiplicity
    # lives differs between them: for a species they are identity columns on
    # ``species`` (``uq_species_identity`` is SMILES + charge + multiplicity,
    # DR-0031) reached through the entry, whereas a transition-state entry
    # carries them directly. Both are non-null.
    if calculation.species_entry is not None:
        owner = calculation.species_entry.species
        field_prefix = "species_entry"
    elif calculation.transition_state_entry is not None:
        owner = calculation.transition_state_entry
        field_prefix = "transition_state_entry"
    else:
        return []

    try:
        content = base64.b64decode(artifact_in.content_base64, validate=True)
    except (binascii.Error, ValueError):
        # Should not happen — pass-1 validation already decoded successfully.
        logger.warning(
            "charge/multiplicity reconciliation skipped: artifact '%s' could "
            "not be base64-decoded",
            artifact_in.filename,
        )
        return []

    text = content.decode("utf-8", errors="replace")

    outcome = reconcile_charge_multiplicity(
        declared_charge=owner.charge,
        declared_multiplicity=owner.multiplicity,
        log_text=text,
        field_prefix=field_prefix,
    )
    return outcome.warnings
