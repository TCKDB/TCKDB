import type { ShapeCalculation, ShapeEnergy, ShapeRow } from "./conformerGallery"

/** The same calculation always has the same number: its place in the order the calculations are first met on this shape, listed ones before extra energies. */
export type CalculationNumbers = ReadonlyMap<string, number>

export function numberCalculations(rows: readonly ShapeRow[]): CalculationNumbers {
    const numbers = new Map<string, number>()
    const meet = (ref: string) => { if (!numbers.has(ref)) numbers.set(ref, numbers.size + 1) }
    // The calculations each observation lists come first, so an energy that an energy read adds later never shifts them.
    for (const row of rows) {
        for (const calculation of row.foundBy) meet(calculation.ref)
        for (const energy of row.energies) if (!energy.extra) meet(energy.ref)
    }
    for (const row of rows) for (const energy of row.energies) if (energy.extra) meet(energy.ref)
    return numbers
}

/** Calculations that say the same thing, in the order first met; each keeps its own number and link. */
export function groupBy<T extends ShapeCalculation>(items: readonly T[], keyOf: (item: T) => string): { first: T; members: T[] }[] {
    const groups = new Map<string, { first: T; members: T[] }>()
    for (const item of items) {
        const key = keyOf(item)
        const group = groups.get(key) ?? { first: item, members: [] }
        if (!group.members.some((member) => member.ref === item.ref)) group.members.push(item)
        groups.set(key, group)
    }
    return [...groups.values()]
}

/** What a row says about where a calculation ran: level (by ref, else label), program, workflow tool and review. */
export const calculationKey = (calculation: ShapeCalculation): string =>
    `${calculation.levelRef ?? calculation.levelLabel ?? "-"}\n${calculation.program ?? "-"}\n${calculation.workflowTool ?? "-"}\n${calculation.review ?? "-"}`

export const foundByKey = (calculation: ShapeCalculation): string => `${calculation.type}\n${calculationKey(calculation)}`

export const energyKey = (energy: ShapeEnergy): string => `${energy.kind}\n${energy.energyKind ?? "-"}\n${energy.hartree ?? "none"}\n${calculationKey(energy)}`

