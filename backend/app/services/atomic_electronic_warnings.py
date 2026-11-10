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
    "collect_conformer_atomic_warnings",
    "collect_pdep_atomic_warnings",
    "collect_thermo_atomic_warnings",
    "corrections_include_spin_orbit",
    "thermo_carries_enthalpy",
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


def thermo_carries_enthalpy(thermo: object) -> bool:
    """True when a thermo payload states an energy-bearing value.

    A spin-orbit shift moves an atom's enthalpy and free energy, not its entropy or heat
    capacity, so a thermo that states only ``s298_j_mol_k`` or ``cp`` points has no energy
    for the shift to be missing from. Enthalpy-bearing: ``h298_kj_mol``,
    ``enthalpy_formation_0k_kj_mol``, a NASA-7 or NASA-9 polynomial (their integration
    constants are the enthalpy), a Wilhoit block with ``h0_kj_mol``, or a tabulated point
    with ``h_kj_mol`` or ``g_kj_mol``.
    """
    if getattr(thermo, "h298_kj_mol", None) is not None:
        return True
    if getattr(thermo, "enthalpy_formation_0k_kj_mol", None) is not None:
        return True
    if getattr(thermo, "nasa", None) is not None or getattr(thermo, "nasa9_intervals", None):
        return True
    wilhoit = getattr(thermo, "wilhoit", None)
    if wilhoit is not None and getattr(wilhoit, "h0_kj_mol", None) is not None:
        return True
    return any(
        getattr(pt, "h_kj_mol", None) is not None or getattr(pt, "g_kj_mol", None) is not None
        for pt in (getattr(thermo, "points", None) or ())
    )


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

    if electronic_levels is not None and len(electronic_levels) == 0 and statmech_computed:
        warnings.append(
            UploadWarning.from_code(
                field=f"{statmech_field}.electronic_levels",
                code=W_MISSING_ATOMIC_ELECTRONIC_LEVELS,
                context={
                    "element": element,
                    "ground_term": ground.term_symbol,
                    "multiplicity": multiplicity,
                    "electronic_partition_function": ground.electronic_partition_function(),
                    "free_energy_error_kj_mol": -ground.spin_only_free_energy_error_kj_mol(),
                    "ground_levels": [
                        {"j": two_j / 2, "degeneracy": two_j + 1, "energy_cm1": energy} for two_j, energy in ground.levels
                    ],
                },
            )
        )
    if energy_is_computed and not has_soc_total:
        warnings.append(
            UploadWarning.from_code(
                field=energy_field,
                code=W_MISSING_ATOMIC_SPIN_ORBIT_CORRECTION,
                context={
                    "element": element,
                    "ground_term": ground.term_symbol,
                    "spin_orbit_shift_kj_mol": abs(ground.spin_orbit_shift_kj_mol),
                },
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
        UploadWarning.from_code(
            field=field,
            code=W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM,
            context={
                "element": element,
                "ground_term": ground.term_symbol,
                "lowest_degeneracy": lowest.degeneracy,
                "allowed_degeneracies": list(allowed),
                "level_degeneracies": list(ground.level_degeneracies),
            },
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
    thermo_computed = (
        thermo is not None
        and thermo.scientific_origin == ScientificOriginKind.computed
        and thermo_carries_enthalpy(thermo)
    )
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


def collect_thermo_atomic_warnings(request) -> list[UploadWarning]:
    """Adapter for ``POST /uploads/thermo`` (also a bundle's thermo records and the thermo job).

    A thermo request carries the species identity and its own applied
    corrections; it carries no levels, so nothing is said about them. The
    spin-orbit judgement is made only on corrections this request lists. A
    thermo that cites a record the request cannot see into (``existing_statmech_id``,
    a source calculation by ``existing_calculation_id``, or a protocol supporting calculation by
    ``calculation_ref``) may have its spin-orbit
    energy recorded there, so the absence is not judged.
    """
    corrections = list(request.applied_energy_corrections)
    has_soc = corrections_include_spin_orbit(corrections)
    protocol = request.protocol
    cites_unseen_record = (
        request.existing_statmech_id is not None
        or any(sc.existing_calculation_id is not None for sc in request.source_calculations)
        or (
            protocol is not None
            and any(c.calculation_ref is not None for c in protocol.supporting_calculations)
        )
    )
    element = single_atom_element(smiles=request.species_entry.smiles)
    if element is None:
        return []
    return collect_atomic_electronic_warnings(
        element=element,
        charge=request.species_entry.charge,
        multiplicity=request.species_entry.multiplicity,
        electronic_state_kind=request.species_entry.electronic_state_kind,
        term_symbol=request.species_entry.term_symbol,
        electronic_levels=None,
        statmech_computed=False,
        energy_is_computed=(
            bool(corrections)
            or (request.scientific_origin == ScientificOriginKind.computed and thermo_carries_enthalpy(request))
        )
        and not cites_unseen_record,
        has_soc_total=has_soc,
        energy_field="applied_energy_corrections",
    )


def collect_conformer_atomic_warnings(request) -> list[UploadWarning]:
    """Adapter for ``POST /uploads/conformers`` (and the conformer job).

    The request carries the geometry, the statmech (its levels, and its own
    ``scientific_origin``, which is what is stored on the statmech row) and the applied corrections, so all three judgements are made on
    what it holds.
    """
    element = single_atom_element(xyz_texts=[request.geometry.xyz_text], smiles=request.species_entry.smiles)
    if element is None:
        return []
    corrections = list(request.applied_energy_corrections)
    statmech = request.statmech
    return collect_atomic_electronic_warnings(
        element=element,
        charge=request.species_entry.charge,
        multiplicity=request.species_entry.multiplicity,
        electronic_state_kind=request.species_entry.electronic_state_kind,
        term_symbol=request.species_entry.term_symbol,
        electronic_levels=(list(statmech.electronic_levels) if statmech is not None else None),
        statmech_computed=statmech is not None and statmech.scientific_origin == ScientificOriginKind.computed,
        energy_is_computed=bool(corrections),
        has_soc_total=corrections_include_spin_orbit(corrections),
        statmech_field="statmech",
        energy_field="applied_energy_corrections",
    )


def collect_pdep_atomic_warnings(request) -> list[UploadWarning]:
    """Adapter for ``POST /uploads/networks/pdep`` (and the network job).

    A network species with a statmech block is judged on its levels. The route has
    no corrections field, so it cannot see whether spin-orbit energy was applied:
    that warning is not emitted here.
    """
    warnings: list[UploadWarning] = []
    for sp in request.species:
        if sp.statmech is None:
            continue
        element = single_atom_element(
            xyz_texts=[c.geometry.xyz_text for c in sp.conformers], smiles=sp.species_entry.smiles
        )
        if element is None:
            continue
        warnings.extend(
            collect_atomic_electronic_warnings(
                element=element,
                charge=sp.species_entry.charge,
                multiplicity=sp.species_entry.multiplicity,
                electronic_state_kind=sp.species_entry.electronic_state_kind,
                term_symbol=sp.species_entry.term_symbol,
                electronic_levels=list(sp.statmech.electronic_levels),
                statmech_computed=sp.statmech.scientific_origin == ScientificOriginKind.computed,
                energy_is_computed=None,
                has_soc_total=False,
                statmech_field=f"species[{sp.key!r}].statmech",
            )
        )
    return warnings
