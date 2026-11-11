import type { GeometryAtom } from "../api/geometryApi"
import { isotopeLabel } from "./isotopeLabel"
import { layoutPoster, MAX_BOND_ATOMS } from "./molPosterLayout"

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`

/** Carbon, then hydrogen, then the rest alphabetically. */
function hillRank(label: string): number {
    return label === "C" ? 0 : label === "H" ? 1 : 2
}

function joinWords(items: readonly string[]): string {
    return items.length < 3 ? items.join(" and ") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`
}

/** Atoms per element as "3 C and 6 H", in Hill order. */
export function elementCounts(atoms: readonly GeometryAtom[]): string {
    const counts = new Map<string, number>()
    for (const atom of atoms) {
        const label = isotopeLabel(atom)
        counts.set(label, (counts.get(label) ?? 0) + 1)
    }
    const labels = [...counts.keys()].sort((a, b) => hillRank(a) - hillRank(b) || a.localeCompare(b))
    return joinWords(labels.map((label) => `${counts.get(label)} ${label}`))
}

/**
 * One sentence about the stored shape, from the coordinates alone: the atoms and, for two atoms, how far apart they are;
 * for more, the atoms per element and the number of bonds drawn in the picture (bonds are inferred from distance).
 */
export function shapeSummary(atoms: readonly GeometryAtom[]): string | null {
    if (atoms.length === 0) return null
    if (atoms.length === 1) return `1 atom, ${isotopeLabel(atoms[0])}.`
    if (atoms.length === 2) {
        const [a, b] = atoms
        const distance = Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
        return `2 atoms, ${isotopeLabel(a)} and ${isotopeLabel(b)}, ${distance.toFixed(2)} Å apart.`
    }
    const head = `${atoms.length} atoms: ${elementCounts(atoms)}`
    if (atoms.length > MAX_BOND_ATOMS) return `${head}. Bonds are not drawn for molecules this large.`
    const bonds = layoutPoster(atoms.map(({ element, x, y, z }) => ({ element, x, y, z })), 1).bonds.length
    return `${head}, with ${plural(bonds, "bond")} drawn.`
}

const FROM: Readonly<Record<string, string>> = {
    opt: "an optimisation",
    freq: "a frequency calculation",
    sp: "a single-point calculation",
    irc: "an IRC calculation",
    scan: "a scan",
    path_search: "a path search",
    conf: "a conformer calculation",
}

/**
 * "final, from an optimisation" for a geometry one calculation produced; "from 3 calculations" when several did; null
 * when nothing is recorded as having produced it. Built from codes only.
 */
export function producedByPhrase(producers: readonly { calculation_type: string; role?: string | null }[]): string | null {
    if (producers.length === 0) return null
    if (producers.length > 1) return `from ${plural(producers.length, "calculation")}`
    const { calculation_type: type, role } = producers[0]
    const from = FROM[type] ?? `a ${type.replaceAll("_", " ")} calculation`
    return role ? `${role.replaceAll("_", " ")}, from ${from}` : `from ${from}`
}
