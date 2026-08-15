"""Reconcile a declared charge / spin multiplicity against the ESS output log.

The uploader declares the charge and spin multiplicity of the species (or
transition state) a calculation belongs to. Where the output log is *also*
uploaded, TCKDB independently re-reads what the electronic-structure run
actually did and reconciles the two:

======================  ==============================================
Situation               Outcome
======================  ==============================================
declared, log agree     ``confirmed``    — no action
declared, log differs   ``mismatch``     — non-blocking warning, flag for review
log un-parseable        ``unverifiable`` — the declaration stands, unchecked
nothing declared        ``absent``       — nothing to do
======================  ==============================================

This is the check that makes ``W_CHARGE_MISMATCH`` / ``W_MULTIPLICITY_MISMATCH``
able to fire at all. The Layer-2 deduction pass in
:mod:`app.services.upload_reconciliation` builds its ``ESSJobMeta`` from the
very payload it then compares against, so its charge/multiplicity comparison
is payload-versus-payload and cannot detect a contradiction by construction.
Here the second opinion comes from the log bytes, which is a genuinely
independent source.

**Absence is not a contradiction.** If the producing program is not one of
the wired parsers, the artifact is missing, the log is truncated, or the
declarations inside a single log disagree with each other, the value is left
*unknown* and no warning is emitted. Emitting a mismatch because parsing
failed would fabricate a contradiction out of ignorance, which
``docs/adr/0008-validation-tiers-definitions-block-expectations-warn.md``
forbids. Only a value genuinely read from the log may contradict a
declaration.

Both findings stay at the :class:`UploadWarning` tier. ADR 0008 argues they
are definitional and ultimately belong at the blocking tier, but explicitly
defers that promotion: these checks have never fired on real data, so their
false-positive rate is unknown and promoting them first would be unsafe.

Pure functions over text and ints; no database dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tckdb_schemas.upload_warning import UploadWarning

from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)
from app.services.ess_software_detection import SoftwareName, detect_software_from_text

#: Emitted when the declared charge and the log's charge disagree.
W_CHARGE_MISMATCH = "charge_mismatch"
#: Emitted when the declared multiplicity and the log's multiplicity disagree.
W_MULTIPLICITY_MISMATCH = "multiplicity_mismatch"


class ChargeMultiplicityAction(str, Enum):
    """What the reconciliation concluded about charge / multiplicity."""

    confirmed = "confirmed"
    mismatch = "mismatch"
    unverifiable = "unverifiable"
    absent = "absent"


@dataclass(frozen=True)
class ParsedChargeMultiplicity:
    """Charge / multiplicity as actually stated by an output log.

    Either field may be ``None`` independently: a Molpro deck states the
    spin outright but never declares the charge, so its multiplicity is
    usable while its charge is not.
    """

    charge: int | None
    multiplicity: int | None
    software: SoftwareName


@dataclass(frozen=True)
class ChargeMultiplicityReconciliation:
    """Outcome of comparing declared values against the output log."""

    action: ChargeMultiplicityAction
    declared_charge: int | None
    declared_multiplicity: int | None
    log_charge: int | None
    log_multiplicity: int | None
    software: SoftwareName | None
    warnings: list[UploadWarning]


def _unanimous(values: list[int | None]) -> int | None:
    """Return the single agreed value, or ``None`` if there isn't one.

    A log that declares a quantity more than once with conflicting values
    (Gaussian counterpoise fragments, ORCA compound jobs, multi-state MRCI
    decks) has not told us what the *system's* value is. Reject rather than
    pick one and risk a fabricated mismatch.
    """
    known = [v for v in values if v is not None]
    if not known:
        return None
    first = known[0]
    return first if all(v == first for v in known) else None


def _parse_gaussian(text: str) -> tuple[int | None, int | None]:
    from app.services.gaussian_parameter_parser import parse_all_charge_multiplicity

    found = parse_all_charge_multiplicity(text)
    charges: list[int | None] = [c for c, _ in found]
    mults: list[int | None] = [m for _, m in found]
    return _unanimous(charges), _unanimous(mults)


def _parse_orca(text: str) -> tuple[int | None, int | None]:
    from app.services.orca_parameter_parser import parse_all_charge_multiplicity

    found = parse_all_charge_multiplicity(text)
    charges: list[int | None] = [c for c, _ in found]
    mults: list[int | None] = [m for _, m in found]
    return _unanimous(charges), _unanimous(mults)


def _parse_psi4(text: str) -> tuple[int | None, int | None]:
    """Psi4 declares the pair twice per geometry, in two different shapes.

    Both are collected by the parser, and both must agree. A counterpoise
    or SAPT job activates each fragment as its own molecule and prints a
    header per fragment, so the first pair need not describe the whole
    system — the unanimity check is what stops a fragment's values being
    compared against the entry's.
    """
    from app.services.psi4_parameter_parser import parse_all_charge_multiplicity

    found = parse_all_charge_multiplicity(text)
    charges: list[int | None] = [c for c, _ in found]
    mults: list[int | None] = [m for _, m in found]
    return _unanimous(charges), _unanimous(mults)


def _parse_molpro(text: str) -> tuple[int | None, int | None]:
    """Molpro states the spin, but never declares the charge outright.

    In the keyword form (``wf,spin=..,charge=..``) an omitted ``charge=``
    falls back to Molpro's neutral default, and in the positional MRCI form
    (``wf,<nelec>,<sym>,<2S>``) the charge is *derived* by subtracting the
    electron count from a sum of atomic numbers read out of the echoed
    geometry. A defaulted or derived value is an inference, not something
    the log declared, so a disagreement with it is not evidence of a
    contradiction. Only an explicitly written ``charge=`` is trusted here.
    """
    from app.services.molpro_parameter_parser import parse_all_charge_multiplicity

    found = parse_all_charge_multiplicity(text)
    charges: list[int | None] = [
        entry["charge"] if entry.get("charge_is_declared") else None
        for entry in found
    ]
    mults: list[int | None] = [entry["multiplicity"] for entry in found]
    return _unanimous(charges), _unanimous(mults)


def parse_charge_multiplicity_from_log(
    text: str | None,
) -> ParsedChargeMultiplicity | None:
    """Re-read the charge and spin multiplicity stated by an output log.

    Picks the parser by sniffing the program banner in the log's *content*
    (not its filename), the same dispatch the single-point-energy path uses,
    so the two can never disagree about which program produced a given file.
    Gaussian, ORCA, Molpro and Psi4 are wired.

    Returns ``None`` when the program is unrecognised or neither quantity
    could be read, so the caller treats the log as *unverifiable* rather
    than guessing. A returned object may still carry ``None`` in one field.
    """
    if not text:
        return None
    software = detect_software_from_text(text)
    if software is None:
        return None

    # Exhaustive on purpose. A program without a wired parser must not fall
    # through to another program's: Gaussian's charge/multiplicity pattern
    # spans newlines and so coincidentally matches Psi4's SCF block, which
    # would look like it worked while missing Psi4's geometry-header form
    # entirely — and would report one confident pair for a log whose two
    # forms disagree, the exact fabricated mismatch this module exists to
    # avoid.
    if software == "molpro":
        charge, multiplicity = _parse_molpro(text)
    elif software == "orca":
        charge, multiplicity = _parse_orca(text)
    elif software == "psi4":
        charge, multiplicity = _parse_psi4(text)
    else:  # gaussian
        charge, multiplicity = _parse_gaussian(text)

    # A multiplicity below 1 is unphysical (2S+1 >= 1, mirrored by the
    # ``multiplicity_ge_1`` check constraint on the entry tables): a parse
    # that produced one has misread the log, so discard it rather than
    # compare against it.
    if multiplicity is not None and multiplicity < 1:
        multiplicity = None

    if charge is None and multiplicity is None:
        return None

    return ParsedChargeMultiplicity(
        charge=charge, multiplicity=multiplicity, software=software
    )


def reconcile_charge_multiplicity(
    *,
    declared_charge: int | None,
    declared_multiplicity: int | None,
    log_text: str | None,
    field_prefix: str = "species_entry",
) -> ChargeMultiplicityReconciliation:
    """Reconcile declared charge/multiplicity against the uploaded log.

    :param declared_charge: Charge recorded on the owning entry.
    :param declared_multiplicity: Multiplicity recorded on the owning entry.
    :param log_text: Decoded output-log text, or ``None`` when no output
        artifact accompanies the calculation.
    :param field_prefix: Dot-path prefix for any emitted
        :class:`UploadWarning` (``"species_entry"`` or
        ``"transition_state_entry"``).
    """
    parsed = parse_charge_multiplicity_from_log(log_text)

    if declared_charge is None and declared_multiplicity is None:
        return ChargeMultiplicityReconciliation(
            action=ChargeMultiplicityAction.absent,
            declared_charge=None,
            declared_multiplicity=None,
            log_charge=parsed.charge if parsed else None,
            log_multiplicity=parsed.multiplicity if parsed else None,
            software=parsed.software if parsed else None,
            warnings=[],
        )

    if parsed is None:
        # Unsupported program, absent artifact, truncated or malformed log,
        # or self-contradictory declarations. Nothing was learned, so
        # nothing is contradicted.
        return ChargeMultiplicityReconciliation(
            action=ChargeMultiplicityAction.unverifiable,
            declared_charge=declared_charge,
            declared_multiplicity=declared_multiplicity,
            log_charge=None,
            log_multiplicity=None,
            software=None,
            warnings=[],
        )

    warnings: list[UploadWarning] = []
    compared = False

    if declared_charge is not None and parsed.charge is not None:
        compared = True
        if declared_charge != parsed.charge:
            warnings.append(
                UploadWarning(
                    field=f"{field_prefix}.charge",
                    code=W_CHARGE_MISMATCH,
                    message=(
                        f"Declared charge {declared_charge:+d} disagrees with "
                        f"the charge stated by the uploaded {parsed.software} "
                        f"output log ({parsed.charge:+d}). The declared value "
                        "is kept unchanged and flagged for reviewer attention."
                    ),
                )
            )

    if declared_multiplicity is not None and parsed.multiplicity is not None:
        compared = True
        if declared_multiplicity != parsed.multiplicity:
            warnings.append(
                UploadWarning(
                    field=f"{field_prefix}.multiplicity",
                    code=W_MULTIPLICITY_MISMATCH,
                    message=(
                        f"Declared spin multiplicity {declared_multiplicity} "
                        f"disagrees with the multiplicity stated by the "
                        f"uploaded {parsed.software} output log "
                        f"({parsed.multiplicity}). The declared value is kept "
                        "unchanged and flagged for reviewer attention."
                    ),
                )
            )

    if not compared:
        # The log yielded only a quantity the caller did not declare.
        action = ChargeMultiplicityAction.unverifiable
    elif warnings:
        action = ChargeMultiplicityAction.mismatch
    else:
        action = ChargeMultiplicityAction.confirmed

    return ChargeMultiplicityReconciliation(
        action=action,
        declared_charge=declared_charge,
        declared_multiplicity=declared_multiplicity,
        log_charge=parsed.charge,
        log_multiplicity=parsed.multiplicity,
        software=parsed.software,
        warnings=warnings,
    )


CHECK_CHARGE_MULTIPLICITY_VS_LOG = ScientificCheck(
    group="A structure against its own label",
    sort_key=5,  # Shifted from 4 by #143; see CHECK_SMILES_CHARGE_MATCHES_DECLARED.
    code=(W_CHARGE_MISMATCH, W_MULTIPLICITY_MISMATCH),
    asserts=(
        "The charge and spin multiplicity a depositor declares match the ones "
        "the electronic-structure log says the calculation was actually run "
        "at."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "**Placed against the ADR's own reasoning, deliberately.** ADR 0008 "
        "names both findings as direct contradictions between a declaration "
        "and the parsed evidence, therefore definitional, therefore belonging "
        "at the blocking tier — and then defers the promotion, because "
        "promoting a warning to a blocker rejects payloads that are accepted "
        "today. These checks have never fired on real data, so their "
        "false-positive rate is unknown and promoting them first would be "
        "unsafe. The register records the gap rather than hiding it: this is "
        "the clearest case in TCKDB of a check sitting one tier below where "
        "its own governing decision puts it."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            reconcile_charge_multiplicity,
            note=(
                "Re-reads charge and multiplicity from the uploaded artifact "
                "using the wired Gaussian, ORCA, Psi4 and Molpro parsers."
            ),
        ),
    ),
    escape_hatch=(
        "Absence is not contradiction: if the producing program is not one of "
        "the wired parsers, the artifact is missing, the log is truncated, or "
        "the declarations inside a single log disagree with each other, the "
        "value is left unknown and **no** warning is emitted. Only a value "
        "genuinely read from the log may contradict a declaration — emitting a "
        "mismatch because parsing failed would fabricate a contradiction out "
        "of ignorance."
    ),
)
