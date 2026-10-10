/**
 * Isotope-aware atom labels and formulas for geometry display.
 *
 * Convention: a superscript mass-number prefix (²H, ¹³C), the IUPAC nuclide
 * form, for every labelled atom — not the D/T shorthand. One rule covers
 * H, C and everything else, and matches the stored value (#682 stores a
 * D/T spelling as H + mass number 2/3).
 *
 * Only the server's `isotope_mass_number` counts. This module never infers
 * an isotope from an element symbol or a mass. Legacy rows that still hold
 * the literal symbol `D`/`T` with a null mass are shown as stored.
 */

const SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"

export function superscriptNumber(n: number): string {
    return String(n).replace(/\d/g, (d) => SUPERSCRIPT_DIGITS[Number(d)])
}

export interface IsotopeLabelledAtom {
    element: string
    isotope_mass_number?: number | null
}

/** `²H` for a labelled atom, the bare symbol otherwise. */
export function isotopeLabel(atom: IsotopeLabelledAtom): string {
    const mass = atom.isotope_mass_number
    return mass == null ? atom.element : `${superscriptNumber(mass)}${atom.element}`
}

export function hasIsotopeLabel(atoms: IsotopeLabelledAtom[]): boolean {
    return atoms.some((atom) => atom.isotope_mass_number != null)
}

/**
 * H-second ordering (as `hillFormula` on the geometry page: C first, H
 * second, rest alphabetical; not strict Hill when there is no carbon) that keeps
 * isotopologues distinct: D2O is written `²H2O`, HDO is `H²HO`. Within one
 * element the unlabelled atoms come first, then labelled ones by mass.
 */
export function isotopeHillFormula(atoms: IsotopeLabelledAtom[]): string {
    if (atoms.length === 0) return ""
    const counts = new Map<string, { element: string; mass: number | null; n: number }>()
    for (const atom of atoms) {
        const mass = atom.isotope_mass_number ?? null
        const key = `${atom.element}|${mass ?? ""}`
        const entry = counts.get(key)
        if (entry) entry.n += 1
        else counts.set(key, { element: atom.element, mass, n: 1 })
    }
    const rank = (el: string) => (el === "C" ? 0 : el === "H" ? 1 : 2)
    const groups = [...counts.values()].sort((a, b) => {
        if (a.element !== b.element) {
            const byRank = rank(a.element) - rank(b.element)
            return byRank !== 0 ? byRank : a.element.localeCompare(b.element)
        }
        return (a.mass ?? 0) - (b.mass ?? 0)
    })
    return groups
        .map((g) => `${isotopeLabel({ element: g.element, isotope_mass_number: g.mass })}${g.n > 1 ? g.n : ""}`)
        .join("")
}
