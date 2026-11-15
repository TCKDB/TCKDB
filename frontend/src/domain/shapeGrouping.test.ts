import { describe, expect, it } from "vitest"
import type { LevelOfTheory } from "../api/scientificSchemas"
import { spreadLines, spreadText, type ShapeCalculation, type ShapeEnergy, type ShapeRow } from "./conformerGallery"
import { energyKey, foundByKey, groupBy, numberCalculations } from "./shapeGrouping"

const level = (ref: string): LevelOfTheory => ({ method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: ref }) as LevelOfTheory

function calculation(ref: string, extra: Partial<ShapeCalculation> = {}): ShapeCalculation {
    return { ref, type: "opt", levelRef: "lot_a", levelLabel: "b3lyp/def2tzvp", level: level("lot_a"), program: "Gaussian 16", workflowTool: null, review: "not_reviewed", ...extra }
}

function energy(ref: string, hartree: number | null, extra: Partial<ShapeEnergy> = {}): ShapeEnergy {
    return { ...calculation(ref, { type: "sp" }), kind: "sp", energyKind: "electronic_energy", hartree, ...extra }
}

const row = (foundBy: ShapeCalculation[], energies: ShapeEnergy[], observationRef = "co_1"): ShapeRow => ({ observationRef, foundBy, energies })

describe("spreadText", () => {
    it("writes a spread that rounds to nothing as less than 0.01", () => {
        expect(spreadText(0)).toBe("< 0.01")
        expect(spreadText(0.0049)).toBe("< 0.01")
    })

    it("writes a spread of 0.005 and above to two decimals", () => {
        expect(spreadText(0.005)).toBe("0.01")
        expect(spreadText(12.3456)).toBe("12.35")
    })

    it("gives identical values a spread that reads as less than 0.01", () => {
        const rows = [row([], [energy("c1", -40.1), energy("c2", -40.1)])]
        const lines = spreadLines(rows)
        expect(lines).toHaveLength(1)
        expect(spreadText(lines[0].kjMol)).toBe("< 0.01")
    })
})

describe("spreadLines", () => {
    it("never mixes kinds of energy, so a stray non-electronic value does not make a spread with the real ones", () => {
        const rows = [row([], [
            energy("c1", -39.7),
            energy("c2", -39.7),
            energy("c3", 0.0217, { energyKind: "correction" }),
            energy("c4", 0.0218, { energyKind: "correction" }),
        ])]
        const lines = spreadLines(rows)
        expect(lines.map((line) => [line.energyKind, line.count])).toEqual([["electronic_energy", 2], ["correction", 2]])
        expect(Math.max(...lines.map((line) => line.kjMol))).toBeLessThan(1)
    })

    it("does not compare a single point with an optimisation's final energy", () => {
        const rows = [row([], [energy("c1", -40.1), energy("c2", -40.2, { kind: "opt", energyKind: "final_energy" })])]
        expect(spreadLines(rows)).toEqual([])
    })
})

describe("numberCalculations", () => {
    it("gives each distinct calculation one number, in the order first met, found-by before energies", () => {
        const opt = calculation("c_opt")
        const freq = calculation("c_freq", { type: "freq" })
        const rows = [row([opt, freq], [energy("c_sp", -1), energy("c_opt", -2, { kind: "opt", energyKind: "final_energy", type: "opt" })])]
        const numbers = numberCalculations(rows)
        expect([...numbers]).toEqual([["c_opt", 1], ["c_freq", 2], ["c_sp", 3]])
    })

    it("keeps every number where it is when an extra energy arrives later", () => {
        const listed = [row([calculation("c_opt")], [energy("c_sp", -1)], "co_1"), row([calculation("c_opt2")], [energy("c_sp2", -2)], "co_2")]
        const before = numberCalculations(listed)
        const after = numberCalculations([
            { ...listed[0], energies: [...listed[0].energies, energy("c_late", -3, { extra: true })] },
            listed[1],
        ])
        for (const [ref, number] of before) expect(after.get(ref)).toBe(number)
        expect(after.get("c_late")).toBe(before.size + 1)
    })

    it("numbers two calculations of one observation that share a group separately", () => {
        const rows = [row([calculation("c_a"), calculation("c_b")], [])]
        const groups = groupBy(rows.flatMap((r) => r.foundBy), foundByKey)
        expect(groups).toHaveLength(1)
        const numbers = numberCalculations(rows)
        expect(groups[0].members.map((member) => numbers.get(member.ref))).toEqual([1, 2])
    })
})

describe("grouping keys", () => {
    const same = (a: ShapeCalculation, b: ShapeCalculation) => foundByKey(a) === foundByKey(b)

    it("groups calculations that agree on type, level ref, program, tool and review", () => {
        expect(same(calculation("a"), calculation("b"))).toBe(true)
    })

    it("keeps apart calculations that differ only by level ref, program, workflow tool or review", () => {
        expect(same(calculation("a"), calculation("b", { levelRef: "lot_b", level: level("lot_b") }))).toBe(false)
        expect(same(calculation("a"), calculation("b", { program: "ORCA 5.0" }))).toBe(false)
        expect(same(calculation("a"), calculation("b", { workflowTool: "ARC 1.0" }))).toBe(false)
        expect(same(calculation("a"), calculation("b", { review: "approved" }))).toBe(false)
    })

    it("keeps apart energies that differ by value, kind of energy, level ref, program, tool or review", () => {
        const base = energy("a", -1)
        expect(energyKey(base)).toBe(energyKey(energy("b", -1)))
        for (const other of [
            energy("b", -1.5),
            energy("b", -1, { energyKind: "correction" }),
            energy("b", -1, { levelRef: "lot_b" }),
            energy("b", -1, { program: "ORCA 5.0" }),
            energy("b", -1, { workflowTool: "ARC 1.0" }),
            energy("b", -1, { review: "approved" }),
            energy("b", null),
        ]) expect(energyKey(other)).not.toBe(energyKey(base))
    })
})
