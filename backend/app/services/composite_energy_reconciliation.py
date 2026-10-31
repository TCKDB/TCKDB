"""Reconcile a deposited composite energy against the program's own log (ADR 0021, P3b).

A ``composite`` calculation carries the numbers the producer read (or the
program printed): ``e0_hartree``, ``electronic_energy_hartree`` and
``recipe_zpe_hartree``. Where the program's output log is also attached, the
summary block at its end states the same quantities, and this module compares:

======================  =====================================================
Situation               Outcome
======================  =====================================================
every stated number     ``confirmed``      -- nothing to say
agrees with the log
a number differs        ``mismatch``       -- warn ``composite_energy_log_mismatch``
the log is a different  ``method_mismatch`` -- warn ``composite_log_method_mismatch``;
method from the level   the energies are then not compared (they answer
of theory               different questions)
log not readable        ``unverifiable``   -- the deposit stands, with a recorded reason
nothing deposited,      ``available``      -- inform ``composite_energy_log_available``; the
log readable            numbers are stated, nothing is filled
nothing deposited,      ``absent``
log not readable
======================  =====================================================

Always **warn**, never refuse (ADR 0008 tier for ``composite_energy_log_mismatch``):
the log is evidence, and the producer's number is kept exactly as sent.

What is compared, and with which tolerance
------------------------------------------
Each log number was printed rounded to six decimals, and so was a deposited one
copied from it, so two such numbers can differ by 1e-6 without either being
wrong. The tolerance is :func:`~tckdb_schemas.fragments.calculation.composite_arithmetic_tolerance_hartree`
(``max(1e-6, 5e-7 * n)``) with ``n`` the rounded quantities in the comparison:

* ``e0_hartree`` against ``<METHOD> (0 K)``: ``n = 2``;
* ``recipe_zpe_hartree`` against ``E(ZPE)``: ``n = 2``;
* ``electronic_energy_hartree`` against ``E0 - E(ZPE)``: ``n = 3`` (the deposited
  value, and the two printed numbers it is derived from). No block prints a
  ZPE-free energy, so this one comparison uses a number derived from two
  printed ones. That is a *comparison*, never a stored value.

Individual terms are not compared position by position: for a named method the
position of a term is the producer's own ordering, not Gaussian's print order, so
there is no sound mapping. Their sum is tied to ``electronic_energy_hartree`` by
the wire arithmetic check, which the electronic comparison above then covers.

Why nothing is filled from the log
----------------------------------
The single-point path fills a missing energy from the log. This path does not,
on purpose:

* The one number a correction layer uses, ``electronic_energy_hartree``, is not
  printed by any block, so it could only be *computed* (``E0 - E(ZPE)``), and
  TCKDB never stores a value it computed.
* Filling ``e0`` or the ZPE alone would leave a result whose electronic energy is
  still empty, a half-filled row that reads as complete.
* ``composite_e0_inconsistent`` and ``composite_terms_do_not_sum`` run on the
  payload, before any fill. A filled ``e0`` next to a deposited
  ``electronic_energy_hartree`` that disagrees with the log would break
  ``e0 = electronic + zpe`` *after* the checks had passed; that is the hole a
  sibling change had. Declining to fill leaves no such path.

Pure functions over text and floats; no database dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tckdb_schemas.fragments.calculation import composite_arithmetic_tolerance_hartree
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.method_names import method_identity_key
from app.parser_registry import PARSERS
from app.services.ess_software_detection import detect_software_from_text
from app.services.gaussian_composite_parser import (
    GaussianCompositeSummary,
    implied_electronic_energy_hartree,
    parse_gaussian_composite_summary,
)

#: The version of the composite-log parsing and comparison that draws a conclusion. Bump it when a fix to
#: :mod:`app.services.gaussian_composite_parser` or to this comparison could change what a log concludes: the
#: next upload of the same log then records a fresh conclusion (``calc_composite_log_check``), and reads prefer it.
COMPOSITE_LOG_PARSER_VERSION = PARSERS.get("composite_log").version

#: A deposited composite number disagrees with the output log's summary block.
W_COMPOSITE_ENERGY_LOG_MISMATCH = "composite_energy_log_mismatch"
#: The log is a different composite method from the calculation's level of theory.
W_COMPOSITE_LOG_METHOD_MISMATCH = "composite_log_method_mismatch"
#: Informational: the deposit stated no energy and the log states them. Nothing is
#: filled; this tells the depositor the numbers are there to send.
W_COMPOSITE_ENERGY_LOG_AVAILABLE = "composite_energy_log_available"

_FLOAT_NOISE = 1e-12


class CompositeEnergyAction(str, Enum):
    """What the reconciliation concluded."""

    confirmed = "confirmed"
    mismatch = "mismatch"
    available = "available"
    method_mismatch = "method_mismatch"
    unverifiable = "unverifiable"
    absent = "absent"


#: Why a log could not be read, for the recorded ``unverifiable`` outcome.
UNVERIFIABLE_NOT_GAUSSIAN = "not_a_gaussian_log"
UNVERIFIABLE_NO_SUPPORTED_BLOCK = "no_supported_composite_summary_block"


@dataclass(frozen=True)
class CompositeEnergyReconciliation:
    """Outcome of comparing a deposited composite result with its log.

    :param action: The conclusion.
    :param log_summary: What the log stated, when it could be read.
    :param warning: The warning to return, for ``mismatch`` and ``method_mismatch``.
    :param unverifiable_reason: Set when ``action`` is ``unverifiable``.
    """

    action: CompositeEnergyAction
    log_summary: GaussianCompositeSummary | None = None
    warning: UploadWarning | None = None
    unverifiable_reason: str | None = None


def _differs(deposited: float, logged: float, rounded_quantities: int) -> bool:
    tolerance = composite_arithmetic_tolerance_hartree(rounded_quantities)
    return abs(deposited - logged) > tolerance + _FLOAT_NOISE


def reconcile_composite_energy(
    *,
    level_method: str | None,
    e0_hartree: float | None,
    electronic_energy_hartree: float | None,
    recipe_zpe_hartree: float | None,
    log_text: str | None,
    field: str = "composite_result",
) -> CompositeEnergyReconciliation:
    """Compare a deposited composite result with the output log's summary block.

    :param level_method: The calculation's level-of-theory ``method`` as stored
        (``None`` when it has none); compared by identity key, never re-keyed.
    :param e0_hartree: Deposited ``e0_hartree``.
    :param electronic_energy_hartree: Deposited ``electronic_energy_hartree``.
    :param recipe_zpe_hartree: Deposited ``recipe_zpe_hartree``.
    :param log_text: Decoded output-log text.
    :param field: Dot-path used in any emitted warning.
    """
    nothing_deposited = all(
        value is None for value in (e0_hartree, electronic_energy_hartree, recipe_zpe_hartree)
    )
    if not log_text or detect_software_from_text(log_text) != "gaussian":
        if nothing_deposited:
            return CompositeEnergyReconciliation(action=CompositeEnergyAction.absent)
        return CompositeEnergyReconciliation(
            action=CompositeEnergyAction.unverifiable,
            unverifiable_reason=UNVERIFIABLE_NOT_GAUSSIAN,
        )
    summary = parse_gaussian_composite_summary(log_text)
    if summary is None:
        if nothing_deposited:
            return CompositeEnergyReconciliation(action=CompositeEnergyAction.absent)
        return CompositeEnergyReconciliation(
            action=CompositeEnergyAction.unverifiable,
            unverifiable_reason=UNVERIFIABLE_NO_SUPPORTED_BLOCK,
        )

    if level_method is not None and method_identity_key(level_method) != summary.method_key:
        return CompositeEnergyReconciliation(
            action=CompositeEnergyAction.method_mismatch,
            log_summary=summary,
            warning=UploadWarning.from_code(
                field=field,
                code=W_COMPOSITE_LOG_METHOD_MISMATCH,
                context={
                    "log_method": summary.method_key,
                    "depositor_supplied": {"level_of_theory_method": str(level_method)},
                },
            ),
        )

    if nothing_deposited:
        return CompositeEnergyReconciliation(
            action=CompositeEnergyAction.available,
            log_summary=summary,
            warning=UploadWarning.from_code(
                field=field,
                code=W_COMPOSITE_ENERGY_LOG_AVAILABLE,
                context={
                    "log_method": summary.method_key,
                    "e0_hartree": summary.e0_hartree,
                    "recipe_zpe_hartree": summary.recipe_zpe_hartree,
                },
            ),
        )

    disagreements: list[dict[str, object]] = []
    if e0_hartree is not None and _differs(e0_hartree, summary.e0_hartree, 2):
        disagreements.append(
            {"quantity": "e0_hartree", "deposited_hartree": e0_hartree, "log_hartree": summary.e0_hartree}
        )
    if recipe_zpe_hartree is not None and _differs(recipe_zpe_hartree, summary.recipe_zpe_hartree, 2):
        disagreements.append(
            {
                "quantity": "recipe_zpe_hartree",
                "deposited_hartree": recipe_zpe_hartree,
                "log_hartree": summary.recipe_zpe_hartree,
            }
        )
    # The log-implied electronic energy is the log's 0 K energy minus its ZPE.
    implied = implied_electronic_energy_hartree(summary)
    if electronic_energy_hartree is not None and _differs(electronic_energy_hartree, implied, 3):
        disagreements.append(
            {
                "quantity": "electronic_energy_hartree",
                "deposited_hartree": electronic_energy_hartree,
                "log_hartree": implied,
            }
        )
    if disagreements:
        return CompositeEnergyReconciliation(
            action=CompositeEnergyAction.mismatch,
            log_summary=summary,
            warning=UploadWarning.from_code(
                field=field,
                code=W_COMPOSITE_ENERGY_LOG_MISMATCH,
                context={"log_method": summary.method_key, "disagreements": disagreements},
            ),
        )
    return CompositeEnergyReconciliation(action=CompositeEnergyAction.confirmed, log_summary=summary)
