"""Electronic-structure completeness of single-atom uploads (#609).

An isolated atom's entropy and free energy depend on its electronic partition
function, ``q_el = sum g_i exp(-E_i / kT)``. For an S-term atom (H, N, P, ...)
the spin multiplicity is the whole answer. For any other ground term (B, C, O,
F, Al, Si, S, Cl, Br, I) it is not: the fine-structure J-levels are populated
at 298 K. The real ARC oxygen atom, deposited with spin multiplicity alone, has
S(298 K) = 152.44 J/mol/K against the NIST-JANAF 161.06 (about 8.5 low, or about
2.5 kJ/mol in G).

Tier (ADR 0008)
---------------
Both absences are **warnings**. Each is an absence, and "an incomplete record
is still a true record". Neither could be a refusal: a depositor may
legitimately have folded the degeneracy elsewhere, or be depositing an
experimental value with no partition function of its own.

What counts as "the information is present"
-------------------------------------------
Sign convention: the spin-only partition function ``g_spin`` under-counts
``q_el``, so spin-only S is low and spin-only G is *high*:
``G(true) - G(spin-only) = -RT ln(q_el / g_spin)`` (O -2.00, Cl -1.74 kJ/mol
at 298.15 K).

* Electronic levels: any ``electronic_levels`` on the statmech. There is no
  other place a statmech row records electronic degeneracy (there is no
  ``electronic_degeneracy`` column; ``spin_multiplicity`` lives on the species
  entry and is exactly the spin-only case this check exists to flag).
* Spin-orbit energy: an applied correction with role ``soc_total``, or one
  whose scheme kind is ``soc``, or any component of kind ``soc``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.atomic_ground_terms import (
    ground_term_for,
)
from app.db.models.common import (
    AppliedCorrectionComponentKind,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    ScientificOriginKind,
    SpeciesEntryStateKind,
)
from app.services.monatomic import single_atom_element

W_MISSING_ATOMIC_ELECTRONIC_LEVELS = "missing_atomic_electronic_levels"
W_MISSING_ATOMIC_SPIN_ORBIT_CORRECTION = "missing_atomic_spin_orbit_correction"
W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM = "atomic_electronic_degeneracy_contradicts_term"

__all__ = [
    "W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM",
    "W_MISSING_ATOMIC_ELECTRONIC_LEVELS",
    "W_MISSING_ATOMIC_SPIN_ORBIT_CORRECTION",
    "collect_atomic_electronic_warnings",
    "collect_bundle_atomic_warnings",
    "corrections_include_spin_orbit",
]


def corrections_include_spin_orbit(corrections: Iterable[object]) -> bool:
    """True when any applied correction carries a spin-orbit contribution."""
    for ac in corrections:
        role = getattr(ac, "application_role", None)
        if role is not None and EnergyCorrectionApplicationRole(role) == (EnergyCorrectionApplicationRole.soc_total):
            return True
        scheme = getattr(ac, "scheme", None)
        kind = getattr(scheme, "kind", None)
        if kind is not None and EnergyCorrectionSchemeKind(kind) == (EnergyCorrectionSchemeKind.soc):
            return True
        for comp in getattr(ac, "components", ()) or ():
            ck = getattr(comp, "component_kind", None)
            if ck is not None and AppliedCorrectionComponentKind(ck) == (AppliedCorrectionComponentKind.soc):
                return True
    return False


def collect_atomic_electronic_warnings(
    *,
    element: str | None,
    charge: int,
    multiplicity: int,
    electronic_state_kind: SpeciesEntryStateKind | str,
    term_symbol: str | None,
    electronic_levels: Sequence[object] | None,
    statmech_computed: bool,
    energy_is_computed: bool | None,
    has_soc_total: bool,
    statmech_field: str = "statmech",
    energy_field: str = "applied_energy_corrections",
) -> list[UploadWarning]:
    """Warnings for one single-atom species.

    :param element: Element of the one atom, or ``None`` (not an atom: no
        warnings).
    :param electronic_levels: The statmech's levels, or ``None`` when the
        upload carries no statmech at all (then nothing is said about levels).
    :param statmech_computed: The statmech's origin is ``computed``.
    :param energy_is_computed: ``True`` when the upload carries computed
        energy content (computed thermo or applied energy corrections),
        ``None``/``False`` when it does not.
    :param has_soc_total: :func:`corrections_include_spin_orbit` over the
        species' corrections.

    Only a neutral, ground-state atom whose declared multiplicity is the
    table's is judged: an ion, an excited state or a different multiplicity
    is a different subject and the neutral ground term says nothing about it.
    """
    if element is None or charge != 0:
        return []
    if SpeciesEntryStateKind(electronic_state_kind) != SpeciesEntryStateKind.ground:
        return []
    ground = ground_term_for(element)
    if ground is None or multiplicity != ground.multiplicity:
        return []

    warnings: list[UploadWarning] = []

    if electronic_levels is not None and len(electronic_levels) > 0:
        warnings.extend(
            _degeneracy_warnings(
                element=element,
                ground=ground,
                levels=electronic_levels,
                field=f"{statmech_field}.electronic_levels",
            )
        )

    if ground.is_s_term:
        return warnings

    j_desc = ", ".join(f"J={two_j / 2:g} (g={two_j + 1}) at {e:g} cm^-1" for two_j, e in ground.levels)
    if electronic_levels is not None and len(electronic_levels) == 0 and statmech_computed:
        warnings.append(
            UploadWarning(
                field=f"{statmech_field}.electronic_levels",
                code=W_MISSING_ATOMIC_ELECTRONIC_LEVELS,
                message=(
                    f"{element} atom has a {ground.term_symbol} ground term with "
                    f"fine structure ({j_desc}) but this statmech carries no "
                    f"electronic_levels, so its electronic partition function is "
                    f"only the spin degeneracy {multiplicity}. Its entropy is low and "
                    f"its free energy too high by about "
                    f"{-ground.spin_only_free_energy_error_kj_mol():.2f} kJ/mol at "
                    f"298.15 K (q_el = {ground.electronic_partition_function():.2f} "
                    f"against {multiplicity})."
                ),
            )
        )
    if energy_is_computed and not has_soc_total:
        warnings.append(
            UploadWarning(
                field=energy_field,
                code=W_MISSING_ATOMIC_SPIN_ORBIT_CORRECTION,
                message=(
                    f"{element} atom has a {ground.term_symbol} ground term whose "
                    f"spin-orbit splitting lowers the ground level, but no "
                    f"soc_total energy correction was supplied, so its energy is "
                    f"the spin-orbit-free value ("
                    f"{ground.spin_orbit_shift_kj_mol:.2f} kJ/mol)."
                ),
            )
        )
    return warnings


def _degeneracy_warnings(*, element, ground, levels, field):
    """Judge the lowest deposited level against the NIST levels in energy order.

    Allowed ground degeneracies are the running sums of ``2J+1`` down the
    ground term's levels (:attr:`AtomicGroundTerm.cumulative_degeneracies`):
    the J-resolved ground state, or the lowest levels lumped, up to the
    unsplit term. The declared term's J, when it has one, is compared against
    the NIST ground J by ``term_symbol_warnings``, not here.
    """
    allowed = ground.cumulative_degeneracies
    lowest = min(levels, key=lambda lv: (lv.energy_cm1, lv.level_index))
    if lowest.degeneracy in allowed:
        return []
    return [
        UploadWarning(
            field=field,
            code=W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM,
            message=(
                f"The lowest electronic level of this {element} atom has "
                f"degeneracy {lowest.degeneracy}, but the {ground.term_symbol} "
                f"ground term has levels of degeneracy "
                f"{', '.join(str(g) for g in ground.level_degeneracies)} in "
                f"energy order, so the lowest level can only be "
                f"{', '.join(str(a) for a in allowed)}."
            ),
        )
    ]


def collect_bundle_atomic_warnings(
    *,
    species_entry,
    xyz_texts: Iterable[str],
    statmech,
    thermo,
    corrections: Sequence[object],
    statmech_field: str,
    energy_field: str,
) -> list[UploadWarning]:
    """Adapter for a bundle species block (species or reaction bundle).

    Decides whether the species is one atom from the deposited geometry, else
    the identity SMILES, and reads levels, origin and corrections off the
    blocks the bundle actually carries.
    """
    element = single_atom_element(xyz_texts=list(xyz_texts), smiles=species_entry.smiles)
    if element is None:
        return []
    thermo_computed = thermo is not None and thermo.scientific_origin == ScientificOriginKind.computed
    return collect_atomic_electronic_warnings(
        element=element,
        charge=species_entry.charge,
        multiplicity=species_entry.multiplicity,
        electronic_state_kind=species_entry.electronic_state_kind,
        term_symbol=species_entry.term_symbol,
        electronic_levels=(list(statmech.electronic_levels) if statmech is not None else None),
        statmech_computed=(statmech is not None and statmech.scientific_origin == ScientificOriginKind.computed),
        energy_is_computed=bool(corrections) or thermo_computed,
        has_soc_total=corrections_include_spin_orbit(corrections),
        statmech_field=statmech_field,
        energy_field=energy_field,
    )
