"""Reconcile a submitting tool's single-point energy against the output log.

TCKDB is software- and workflow-tool agnostic: whichever tool submits a
calculation reports the single-point electronic energy in its payload.
Where the output log is *also* uploaded, TCKDB independently re-derives the
energy from the log bytes and reconciles the two:

===================  ==============================================
Situation            Outcome
===================  ==============================================
payload E, log agree ``confirmed``    — no action
payload E, log differ ``mismatch``    — non-blocking warning, flag for review
payload missing, log ``filled``       — store the log's value
log un-parseable     ``unverifiable`` — payload stands unchanged
neither present      ``absent``       — nothing to do
===================  ==============================================

"Log un-parseable" covers programs whose SP-energy extraction is not yet
wired (ORCA and Gaussian today — their parsers surface parameters, not a
single-point energy) and unsupported Molpro methods (e.g. MRCI-F12). The
reconciliation never blocks an upload, never mutates stored bytes, and on
a mismatch it does **not** overwrite the submitter's value — it keeps what
the tool reported and surfaces the discrepancy for a human reviewer.

Pure functions over text and floats; no database dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from tckdb_schemas.upload_warning import UploadWarning

from app.services.ess_software_detection import detect_software_from_text

# Two single-point energies count as "the same number" within this absolute
# tolerance in Hartree (~6e-4 kcal/mol). A mismatch is a non-blocking
# reviewer flag, not a reject, so the bound is deliberately tight: a wrong
# energy line, a wrong method, or a unit slip diverges by far more, while a
# tool that stores the value at reduced precision stays inside it.
SP_ENERGY_ABS_TOL_HARTREE = 1e-6

#: Emitted when the tool's energy and the log's energy disagree.
W_SP_ENERGY_MISMATCH = "sp_energy_payload_log_mismatch"
#: Emitted (informational) when the tool omitted the energy and TCKDB filled
#: it from the log. Recorded so the fill is auditable in the upload response
#: and the review context rather than happening silently.
W_SP_ENERGY_FILLED_FROM_LOG = "sp_energy_filled_from_log"


class SpEnergyAction(str, Enum):
    """What the reconciliation concluded about the single-point energy."""

    confirmed = "confirmed"
    mismatch = "mismatch"
    filled = "filled"
    unverifiable = "unverifiable"
    absent = "absent"


@dataclass(frozen=True)
class SpEnergyReconciliation:
    """Outcome of comparing a payload energy against the output log.

    ``resolved_energy_hartree`` is the value the caller should persist:
    the payload's energy whenever the payload supplied one (even on a
    mismatch — TCKDB flags, it does not overwrite), the log's energy when
    filling, and ``None`` when there is nothing to store.
    """

    action: SpEnergyAction
    payload_energy_hartree: float | None
    log_energy_hartree: float | None
    resolved_energy_hartree: float | None
    warning: UploadWarning | None = None


def parse_sp_energy_from_log(text: str | None) -> float | None:
    """Re-derive the single-point electronic energy (Hartree) from log text.

    Picks the right parser for the uploaded log by sniffing the program
    banner in its *content* (not the filename): Gaussian, ORCA, and Molpro
    are wired. Any other program, or a log with no recognised single-point
    energy (e.g. an unsupported Molpro MRCI-F12 or a Gaussian composite
    method), returns ``None`` so the caller treats it as *unverifiable*
    rather than guessing.
    """
    if not text:
        return None
    software = detect_software_from_text(text)
    if software is None:
        return None

    # Local imports keep this module's own import surface to the dataclasses;
    # each parser is pure-text and free of DB dependencies.
    if software == "molpro":
        from app.services.molpro_parameter_parser import parse_sp_energy
    elif software == "orca":
        from app.services.orca_parameter_parser import parse_sp_energy
    else:  # gaussian
        from app.services.gaussian_parameter_parser import parse_sp_energy

    energy = parse_sp_energy(text)
    # A non-finite parse (NaN/inf from a garbage or overflowed energy line)
    # is not a usable value — treat it as "no energy" so it can never be
    # filled into calc_sp_result or fed to the tolerance comparison.
    if energy is None or not math.isfinite(energy):
        return None
    return energy


def reconcile_sp_energy(
    *,
    payload_energy_hartree: float | None,
    log_text: str | None,
    field: str = "sp_result.electronic_energy_hartree",
) -> SpEnergyReconciliation:
    """Reconcile the tool-reported SP energy against the uploaded log.

    :param payload_energy_hartree: The energy the submitting tool reported
        (``None`` if it omitted one).
    :param log_text: Decoded output-log text, or ``None`` when no output
        artifact accompanies the calculation.
    :param field: Dot-path used in any emitted :class:`UploadWarning`.
    """
    log_energy = parse_sp_energy_from_log(log_text)

    if payload_energy_hartree is not None and log_energy is not None:
        if math.isclose(
            payload_energy_hartree,
            log_energy,
            rel_tol=0.0,
            abs_tol=SP_ENERGY_ABS_TOL_HARTREE,
        ):
            return SpEnergyReconciliation(
                action=SpEnergyAction.confirmed,
                payload_energy_hartree=payload_energy_hartree,
                log_energy_hartree=log_energy,
                resolved_energy_hartree=payload_energy_hartree,
            )
        delta = payload_energy_hartree - log_energy
        return SpEnergyReconciliation(
            action=SpEnergyAction.mismatch,
            payload_energy_hartree=payload_energy_hartree,
            log_energy_hartree=log_energy,
            # Keep the tool's value; flag the discrepancy for a reviewer.
            resolved_energy_hartree=payload_energy_hartree,
            warning=UploadWarning(
                field=field,
                code=W_SP_ENERGY_MISMATCH,
                message=(
                    "Recorded single-point energy "
                    f"{payload_energy_hartree:.8f} Ha disagrees with the "
                    f"value re-derived from the output log "
                    f"{log_energy:.8f} Ha (delta {delta:+.2e} Ha). The "
                    "recorded value is kept unchanged and flagged for "
                    "reviewer attention."
                ),
            ),
        )

    if payload_energy_hartree is None and log_energy is not None:
        return SpEnergyReconciliation(
            action=SpEnergyAction.filled,
            payload_energy_hartree=None,
            log_energy_hartree=log_energy,
            resolved_energy_hartree=log_energy,
            warning=UploadWarning(
                field=field,
                code=W_SP_ENERGY_FILLED_FROM_LOG,
                message=(
                    "No single-point energy was provided; filled "
                    f"{log_energy:.8f} Ha re-derived from the output log."
                ),
            ),
        )

    if payload_energy_hartree is not None and log_energy is None:
        # A value was reported but the log could not be re-parsed (program
        # not yet wired, unsupported method, or no SP energy). Trust the
        # tool; we simply cannot cross-check.
        return SpEnergyReconciliation(
            action=SpEnergyAction.unverifiable,
            payload_energy_hartree=payload_energy_hartree,
            log_energy_hartree=None,
            resolved_energy_hartree=payload_energy_hartree,
        )

    return SpEnergyReconciliation(
        action=SpEnergyAction.absent,
        payload_energy_hartree=None,
        log_energy_hartree=None,
        resolved_energy_hartree=None,
    )
