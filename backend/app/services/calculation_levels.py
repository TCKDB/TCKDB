"""Shared level-of-theory derivation and validation for role-linked calculations.

Statmech and thermo both link supporting calculations to the record by
**role** (``opt`` / ``freq`` / ``sp`` / ``composite`` / ``imported``), and
both need to answer the same questions from those links. Owner decision,
2026-09 ("statmech-level-roles"): a depositor may legitimately run the
optimisation and the single point at two different levels of theory, and
TCKDB must both *display* that split honestly and *catch* the deposit that
forgets half of it.

Four rules, kept in one module so statmech and thermo cannot answer them
differently -- the same discipline
:func:`app.services.statmech_resolution.assert_statmech_role_compatible` /
:func:`app.workflows.thermo.assert_thermo_role_matches_calculation_type`
already follow for role/type compatibility:

* **R1 -- derive.** :func:`derive_levels` answers, at *read* time and
  from the record's role links alone: which level of theory governs the
  geometry (the ``opt`` role's), the frequencies (the ``freq`` role's, or
  the ``opt``'s own when no ``freq`` is linked but the ``opt`` calculation
  itself carries frequency results), and the energy (``sp`` when linked,
  else ``opt``, else ``composite``/``imported`` as declared). Never
  stored, never blocking -- purely a projection of whatever is linked
  right now, so it applies uniformly to every statmech/thermo record
  regardless of which upload path created it.
* **R2 -- at most one each.** :func:`assert_no_duplicate_roles` refuses a
  record that links more than one ``opt``, more than one ``freq``, or
  more than one ``sp``. Deliberately scoped to :func:`derive_levels`'s
  three role-derived levels -- ``composite``/``imported`` describe a
  scientific origin rather than a specific job and are not restricted.
* **R3 -- same geometry.** :func:`assert_sp_geometry_matches_opt` refuses
  a linked ``sp`` whose input geometry is not one of the linked ``opt``'s
  output geometries -- i.e. a single point that was not actually run on
  the geometry the record's own optimisation produced. Silent (never a
  refusal) when either side declares no geometry at all: absence of
  evidence is not evidence of a mismatch.
* **R4/R5 -- the declared energy level must be honest.** A depositor may
  declare the level of theory they intend the record's energy to stand
  at. :func:`assert_energy_level_consistent` refuses the declaration when
  it disagrees with what is actually linked -- silently if it agrees.

R2-R4 are opt-in (see ``enforce_role_consistency`` on
:func:`app.services.statmech_resolution.resolve_or_create_statmech`, and
the equivalent gate in :func:`app.workflows.thermo.persist_thermo_upload`)
because they describe *one depositor's single chain of evidence*, which is
the standalone-upload shape. The multi-conformer bundle paths
(``computed-species``, ``computed-reaction``) deliberately link several
``opt``/``freq`` calculations -- one per conformer -- to a single ensemble
thermo/statmech record, and R2 would wrongly refuse that. R1 (derivation)
still applies there: it just picks the lowest-id calculation per role,
the same deterministic-tie-break convention the rest of this archive uses
(e.g. the statmech provenance-display fallback in
``scientific_product_candidacy.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory

#: Which role supplied the energy level -- shared by :class:`DerivedLevels`
#: and the read-schema field it feeds
#: (``app.schemas.reads.scientific_common.ScientificLevelsSummary
#: .energy_source``), so the two cannot silently drift apart on which
#: strings are valid.
EnergySource = Literal["sp", "opt", "composite", "imported"]

#: A record links more than one calculation under the same 'opt'/'freq'/
#: 'sp' role. Distinct codes per product so a client branching on ``code``
#: never has to know which product it asked about.
W_STATMECH_ROLE_DUPLICATE = "statmech_role_duplicate"
W_THERMO_ROLE_DUPLICATE = "thermo_role_duplicate"

#: A linked 'sp' calculation's input geometry is not one of the linked
#: 'opt' calculation's output geometries.
W_STATMECH_SP_GEOMETRY_MISMATCH = "statmech_sp_geometry_mismatch"
W_THERMO_SP_GEOMETRY_MISMATCH = "thermo_sp_geometry_mismatch"

#: A declared ``energy_level_of_theory`` differs from the linked 'opt'
#: level and no 'sp' calculation is linked to supply the declared level.
W_STATMECH_ENERGY_LEVEL_REQUIRES_SP = "statmech_energy_level_requires_sp"
W_THERMO_ENERGY_LEVEL_REQUIRES_SP = "thermo_energy_level_requires_sp"

#: A declared ``energy_level_of_theory`` disagrees with the linked 'sp'
#: calculation's own level of theory.
W_STATMECH_ENERGY_LEVEL_CONTRADICTION = "statmech_energy_level_contradiction"
W_THERMO_ENERGY_LEVEL_CONTRADICTION = "thermo_energy_level_contradiction"

#: The three roles R2/R3 restrict to at most one link each. ``composite``
#: and ``imported`` are deliberately excluded -- see the module docstring.
_UNIQUE_ROLES = ("opt", "freq", "sp")


class RoleCalcInfo(NamedTuple):
    """The two facts :func:`derive_levels` needs about one role's calculation.

    Deliberately not the ``Calculation`` row itself: callers building this
    from bulk-loaded read-time data (thermo's per-record loop) often have
    no ORM row in hand, only a dict of scalar columns. Keeping this a
    plain tuple lets both the write-time (ORM-backed) and read-time
    (dict-backed) callers share one derivation function.
    """

    lot_id: int | None
    carries_frequencies: bool = False


class DerivedLevels(NamedTuple):
    """R1's answer: which ``level_of_theory.id`` governs each dimension."""

    geometry_lot_id: int | None
    frequency_lot_id: int | None
    energy_lot_id: int | None
    #: Which role supplied ``energy_lot_id``, or ``None`` when nothing
    #: linked can answer it.
    energy_source: EnergySource | None


def derive_levels(
    *,
    opt: RoleCalcInfo | None = None,
    freq: RoleCalcInfo | None = None,
    sp: RoleCalcInfo | None = None,
    composite: RoleCalcInfo | None = None,
    imported: RoleCalcInfo | None = None,
) -> DerivedLevels:
    """R1: derive geometry / frequency / energy levels from role links.

    Pure and DB-free by design -- every caller has already resolved
    whichever calculation it wants to represent each role, from whatever
    source (a fresh ORM row during upload, a bulk-loaded metadata dict at
    read time). This function only encodes the *priority*, so statmech and
    thermo cannot drift apart on what "the energy level" means.

    :param opt: The record's ``opt``-role calculation info, if linked.
    :param freq: The record's ``freq``-role calculation info, if linked.
    :param sp: The record's ``sp``-role calculation info, if linked.
    :param composite: The record's ``composite``-role calculation info,
        if linked.
    :param imported: The record's ``imported``-role calculation info, if
        linked.
    :returns: The derived levels. Any field may be ``None`` when nothing
        linked can answer that question.
    """
    geometry_lot_id = opt.lot_id if opt is not None else None

    if freq is not None:
        frequency_lot_id = freq.lot_id
    elif opt is not None and opt.carries_frequencies:
        frequency_lot_id = opt.lot_id
    else:
        frequency_lot_id = None

    energy_lot_id: int | None
    energy_source: EnergySource | None
    if sp is not None:
        energy_lot_id, energy_source = sp.lot_id, "sp"
    elif opt is not None:
        energy_lot_id, energy_source = opt.lot_id, "opt"
    elif composite is not None:
        energy_lot_id, energy_source = composite.lot_id, "composite"
    elif imported is not None:
        energy_lot_id, energy_source = imported.lot_id, "imported"
    else:
        energy_lot_id, energy_source = None, None

    return DerivedLevels(
        geometry_lot_id=geometry_lot_id,
        frequency_lot_id=frequency_lot_id,
        energy_lot_id=energy_lot_id,
        energy_source=energy_source,
    )


@dataclass(frozen=True)
class RoleLink:
    """One role-tagged source-calculation link, as resolved during upload.

    ``role`` is compared by its plain string value so this stays usable
    from both ``StatmechCalculationRole`` and ``ThermoCalculationRole``
    without importing either enum here.
    """

    role: str
    calculation: Calculation


def _by_role(links: list[RoleLink], role: str) -> list[Calculation]:
    """Every linked calculation for *role*, lowest ``id`` first."""
    return sorted(
        (link.calculation for link in links if link.role == role),
        key=lambda calc: calc.id,
    )


def _first(links: list[RoleLink], role: str) -> Calculation | None:
    """The lowest-id linked calculation for *role*, or ``None``."""
    matches = _by_role(links, role)
    return matches[0] if matches else None


def _lot_label(lot: LevelOfTheory | None) -> str:
    """``method/basis`` (or bare ``method``), the way a chemist writes a LoT.

    Mirrors ``LevelOfTheorySummary.display`` -- for a refusal message, not
    a stored value.
    """
    if lot is None:
        return "unknown"
    return f"{lot.method}/{lot.basis}" if lot.basis else lot.method


def assert_no_duplicate_roles(
    links: list[RoleLink],
    *,
    code: str,
    subject: str,
) -> None:
    """R2: at most one ``opt``, one ``freq``, one ``sp`` per record.

    :param links: Every role link resolved for this upload.
    :param code: The coded refusal to raise -- product-specific, see the
        module-level ``W_*_ROLE_DUPLICATE`` constants.
    :param subject: ``"statmech"`` or ``"thermo"``, for the message.
    :raises CodedValueError: if any of ``opt``/``freq``/``sp`` is linked
        more than once.
    """
    for role in _UNIQUE_ROLES:
        calcs = _by_role(links, role)
        if len(calcs) <= 1:
            continue
        refs = [calc.public_ref for calc in calcs]
        raise CodedValueError(
            code,
            f"{subject} source_calculations declares {len(calcs)} '{role}' "
            f"links ({', '.join(refs)}), but a {subject} record may have "
            f"at most one '{role}' link. Remove the extra link, or declare "
            "the role the calculation actually played.",
            context={
                "role": role,
                "count": len(calcs),
                "calculation_refs": refs,
            },
            message_prefix=False,
        )


def assert_sp_geometry_matches_opt(
    links: list[RoleLink],
    *,
    code: str,
    subject: str,
) -> None:
    """R3: a linked 'sp' must have run on the linked 'opt's own geometry.

    Silent whenever either side declares no geometry at all -- absence of
    evidence is never treated as a mismatch (the same stance
    :class:`app.services.statmech_resolution.FSFSoftwareComparisonState`
    takes for "not_comparable").

    :param links: Every role link resolved for this upload.
    :param code: The coded refusal to raise.
    :param subject: ``"statmech"`` or ``"thermo"``, for the message.
    :raises CodedValueError: if both an 'opt' and an 'sp' are linked, both
        declare at least one geometry, and the sets share no geometry row.
    """
    opt = _first(links, "opt")
    sp = _first(links, "sp")
    if opt is None or sp is None:
        return
    opt_geometry_ids = {row.geometry_id for row in opt.output_geometries}
    sp_geometry_ids = {row.geometry_id for row in sp.input_geometries}
    if not opt_geometry_ids or not sp_geometry_ids:
        return
    if opt_geometry_ids & sp_geometry_ids:
        return
    raise CodedValueError(
        code,
        f"{subject}: the linked 'sp' calculation ({sp.public_ref}) was not "
        f"run on a geometry the linked 'opt' calculation ({opt.public_ref}) "
        "produced. Link the 'sp' role to a calculation whose input "
        "geometry is one of the optimisation's output geometries.",
        context={
            "opt_calculation_ref": opt.public_ref,
            "sp_calculation_ref": sp.public_ref,
        },
        message_prefix=False,
    )


def assert_energy_level_consistent(
    links: list[RoleLink],
    declared: LevelOfTheory | None,
    *,
    requires_sp_code: str,
    contradiction_code: str,
    subject: str,
) -> None:
    """R4/R5: a declared energy level of theory must match what is linked.

    Silent when ``declared`` is ``None`` (nothing was declared -- R1
    derives the energy level from whatever is linked, with no opinion to
    contradict) and when a linked 'opt' with no 'sp' happens to already
    sit at the declared level (R5: an opt-only record is valid exactly
    when there is nothing to contradict).

    :param links: Every role link resolved for this upload.
    :param declared: The resolved ``energy_level_of_theory``, or ``None``
        when the depositor did not declare one.
    :param requires_sp_code: Coded refusal for "differs from the opt level
        and no sp is linked" (R4).
    :param contradiction_code: Coded refusal for "disagrees with the
        linked sp's own level" (R4).
    :param subject: ``"statmech"`` or ``"thermo"``, for the message.
    :raises CodedValueError: per the two cases above.
    """
    if declared is None:
        return
    opt = _first(links, "opt")
    sp = _first(links, "sp")

    if sp is not None:
        if sp.lot_id == declared.id:
            return
        raise CodedValueError(
            contradiction_code,
            f"{subject}: the declared energy level of theory "
            f"({_lot_label(declared)}) does not match the linked 'sp' "
            f"calculation's level ({_lot_label(sp.lot)}, {sp.public_ref}). "
            "Declare the level the linked single point actually ran at, "
            "or link an 'sp' calculation run at the declared level.",
            context={
                "declared_level_of_theory_ref": declared.public_ref,
                "sp_calculation_ref": sp.public_ref,
                "sp_level_of_theory_ref": (
                    sp.lot.public_ref if sp.lot is not None else None
                ),
            },
            message_prefix=False,
        )

    if opt is not None and opt.lot_id != declared.id:
        raise CodedValueError(
            requires_sp_code,
            f"{subject}: energy level of theory {_lot_label(declared)} "
            f"differs from the optimisation level {_lot_label(opt.lot)} "
            f"but no single-point calculation at {_lot_label(declared)} "
            "is linked.",
            context={
                "declared_level_of_theory_ref": declared.public_ref,
                "opt_calculation_ref": opt.public_ref,
                "opt_level_of_theory_ref": (
                    opt.lot.public_ref if opt.lot is not None else None
                ),
            },
            message_prefix=False,
        )


__all__ = [
    "W_STATMECH_ENERGY_LEVEL_CONTRADICTION",
    "W_STATMECH_ENERGY_LEVEL_REQUIRES_SP",
    "W_STATMECH_ROLE_DUPLICATE",
    "W_STATMECH_SP_GEOMETRY_MISMATCH",
    "W_THERMO_ENERGY_LEVEL_CONTRADICTION",
    "W_THERMO_ENERGY_LEVEL_REQUIRES_SP",
    "W_THERMO_ROLE_DUPLICATE",
    "W_THERMO_SP_GEOMETRY_MISMATCH",
    "DerivedLevels",
    "RoleCalcInfo",
    "RoleLink",
    "assert_energy_level_consistent",
    "assert_no_duplicate_roles",
    "assert_sp_geometry_matches_opt",
    "derive_levels",
]
