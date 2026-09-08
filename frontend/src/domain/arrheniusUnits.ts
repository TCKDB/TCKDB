// Unit algebra for `ArrheniusAUnits` (`backend/app/db/models/common.py`) --
// the ONE place this PR's cm<->m<->molecule conversion factors are defined.
// `arrheniusChartLayout.ts` (series/panel conversion) and `ArrheniusChart.tsx`
// (the k(T) table's own conversion) both import the FACTOR from here rather
// than each deriving their own -- a wrong factor is a silently-wrong
// published rate coefficient, so there is exactly one place it can be wrong.
//
// k = A*T^n*exp(-Ea/RT): T and the exponential are dimensionless, so k
// carries EXACTLY A's own units, and converting A converts k -- one factor,
// used identically for the chart's plotted k(T) curve and the table's k
// column (`kineticsTable.ts`'s `convertKineticsTableRows`).
//
// Conversion is offered ONLY within one of three order families, fixed by
// dimensionality -- s⁻¹ can never become cm³ mol⁻¹ s⁻¹, and the type of
// `arrheniusUnitConversionFactor` below (`number | null`, `null` for a
// cross-family pair) is what makes that a compile-time-visible possibility
// every call site must handle, not an assumption a caller can silently get
// wrong.

/** N_A, exact per the SI 2019 redefinition. */
export const AVOGADRO_NUMBER = 6.02214076e23

export type ArrheniusUnitFamily = 1 | 2 | 3

/** Order-2 (bimolecular) and order-3 (termolecular) families each list
 *  their own molar-cm³ form FIRST -- the family's "base" unit the
 *  conversion table below is defined relative to -- then molar-m³, then
 *  per-molecule; order-1 (unimolecular, `per_s`) has no sibling unit at
 *  all. This is also the canonical ORDER a per-panel unit `<select>` lists
 *  its options in (`ArrheniusChart.tsx`), so every panel's dropdown reads
 *  the same way regardless of which unit a given record happened to be
 *  deposited in. */
const FAMILY_UNITS: Record<ArrheniusUnitFamily, readonly string[]> = {
    1: ["per_s"],
    2: ["cm3_mol_s", "m3_mol_s", "cm3_molecule_s"],
    3: ["cm6_mol2_s", "m6_mol2_s", "cm6_molecule2_s"],
}

const UNIT_FAMILY: Record<string, ArrheniusUnitFamily> = {
    per_s: 1,
    cm3_mol_s: 2,
    m3_mol_s: 2,
    cm3_molecule_s: 2,
    cm6_mol2_s: 3,
    m6_mol2_s: 3,
    cm6_molecule2_s: 3,
}

/** Short, human words for a family, used only to build a DISTINCT
 *  accessible name per panel selector when a page has more than one
 *  convertible panel at once (e.g. one bimolecular, one termolecular). */
export const FAMILY_NAME: Record<ArrheniusUnitFamily, string> = {
    1: "unimolecular",
    2: "bimolecular",
    3: "termolecular",
}

/** `FACTOR_FROM_BASE[unit]` -- multiply a value expressed in the family's
 *  OWN base unit (`cm3_mol_s` for order 2, `cm6_mol2_s` for order 3, the
 *  ONLY member for order 1) by this to get that value expressed in `unit`.
 *  1 m³ = 1e6 cm³, so 1 m⁶ = 1e12 cm⁶ -- a value in m³ units is smaller by
 *  1e6 (1e-6 here), a value in m⁶ units smaller by 1e12 (1e-12). "Per
 *  molecule" is smaller than "per mole" by N_A (order 2) or N_A² (order 3,
 *  since a termolecular rate constant carries TWO concentration factors).
 *  Sourced verbatim from this PR's own brief -- do not re-derive by hand
 *  elsewhere; import `arrheniusUnitConversionFactor` instead. */
const FACTOR_FROM_BASE: Record<string, number> = {
    per_s: 1,
    cm3_mol_s: 1,
    m3_mol_s: 1e-6,
    cm3_molecule_s: 1 / AVOGADRO_NUMBER,
    cm6_mol2_s: 1,
    m6_mol2_s: 1e-12,
    cm6_molecule2_s: 1 / (AVOGADRO_NUMBER * AVOGADRO_NUMBER),
}

/** The order family a raw `A_units` token belongs to, or `null` for an
 *  unrecorded (`null`/`undefined`) or unrecognised (not one of
 *  `ArrheniusAUnits`'s seven values -- a future addition this file hasn't
 *  learned yet) token. `null` here is the signal every caller in this PR
 *  uses to refuse conversion entirely, per the plan's own invariant: a
 *  record whose units this file cannot place in a family converts to
 *  nothing, and keeps its current "unrecorded units" treatment. */
export function arrheniusUnitFamily(units: string | null | undefined): ArrheniusUnitFamily | null {
    if (!units) return null
    return UNIT_FAMILY[units] ?? null
}

/** Every unit belonging to `family`, in the canonical (base-first) order --
 *  the full set a per-panel selector offers, regardless of which of them
 *  any actual record in that panel happens to be deposited in. */
export function familyUnits(family: ArrheniusUnitFamily): readonly string[] {
    return FAMILY_UNITS[family]
}

/**
 * Multiply a value expressed in `fromUnits` by this to get its value in
 * `toUnits`. `null` whenever the two units are NOT dimensionally
 * interconvertible: either token is unrecorded/unrecognised, or they
 * belong to different order families (s⁻¹ vs cm³ mol⁻¹ s⁻¹, or a bimolecular
 * unit vs a termolecular one) -- that `null` is the ONE mechanism this file
 * offers for refusing a cross-family conversion; every caller must treat it
 * as "do not convert", never coerce it to 1 or 0.
 *
 * The identical-token case (`fromUnits === toUnits`) always returns exactly
 * 1, checked BEFORE the family lookup -- so converting an unrecognised
 * future token to itself is still the (trivially valid) identity, even
 * though this file cannot place it in any family. This is also what makes
 * "convert to the deposited unit and back" an exact round trip rather than
 * a `1e-6 * 1e6`-style near-miss: converting FROM a unit TO ITSELF never
 * goes through the base-unit table at all.
 */
export function arrheniusUnitConversionFactor(fromUnits: string, toUnits: string): number | null {
    if (fromUnits === toUnits) return 1
    const fromFamily = arrheniusUnitFamily(fromUnits)
    const toFamily = arrheniusUnitFamily(toUnits)
    if (fromFamily == null || toFamily == null || fromFamily !== toFamily) return null
    const fromFactor = FACTOR_FROM_BASE[fromUnits]
    const toFactor = FACTOR_FROM_BASE[toUnits]
    return toFactor / fromFactor
}

/** `value` (expressed in `fromUnits`) re-expressed in `toUnits`, or `null`
 *  when `arrheniusUnitConversionFactor` refuses the pair (see above). A
 *  thin convenience wrapper -- kept here, not re-implemented at either call
 *  site, so a value conversion and its own factor can never drift apart. */
export function convertArrheniusValue(value: number, fromUnits: string, toUnits: string): number | null {
    const factor = arrheniusUnitConversionFactor(fromUnits, toUnits)
    if (factor == null) return null
    return value * factor
}
