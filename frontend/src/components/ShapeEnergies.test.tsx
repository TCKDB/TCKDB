import { cleanup, render, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { LevelOfTheory } from "../api/scientificSchemas"
import type { ShapeCalculation, ShapeEnergy, ShapeRow } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { ShapeEnergies } from "./ShapeEnergies"

afterEach(cleanup)

const level = (ref: string): LevelOfTheory => ({ method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: ref }) as LevelOfTheory
const ready = { status: "ready", records: [], capped: false } as unknown as SinglePointsState

function calculation(ref: string, extra: Partial<ShapeCalculation> = {}): ShapeCalculation {
    return { ref, type: "opt", levelRef: "lot_a", levelLabel: "b3lyp/def2tzvp", level: level("lot_a"), program: "Gaussian 16", workflowTool: null, review: "not_reviewed", ...extra }
}

function energy(ref: string, hartree: number, extra: Partial<ShapeEnergy> = {}): ShapeEnergy {
    return { ...calculation(ref, { type: "sp" }), kind: "sp", energyKind: "electronic_energy", hartree, ...extra }
}

function show(rows: ShapeRow[]) {
    render(<MemoryRouter><ShapeEnergies rows={rows} singlePoints={ready} optimisations={ready} /></MemoryRouter>)
    const table = (name: string) => document.querySelector(`[aria-label="${name}"] tbody`) as HTMLElement
    return { found: table("Calculations that found this geometry"), energies: table("Energies on this geometry") }
}

const cells = (body: HTMLElement) => [...body.querySelectorAll("tr")].map((row) => [...row.querySelectorAll("td")].find((cell) => /^Calculations? \d/.test(cell.textContent ?? ""))?.textContent)

describe("ShapeEnergies numbering", () => {
    it("numbers two calculations of one observation in the same group separately", () => {
        const { found } = show([{ observationRef: "co_1", foundBy: [calculation("c_a"), calculation("c_b")], energies: [] }])
        expect(cells(found)).toEqual(["Calculations 1, 2"])
        expect(within(found).getByRole("link", { name: "Calculation 2" })).toHaveAttribute("href", "/calculations/c_b")
    })

    it("gives a calculation the same number in both tables, and different calculations different numbers", () => {
        const opt = calculation("c_opt")
        const { found, energies } = show([{
            observationRef: "co_1",
            foundBy: [opt],
            energies: [energy("c_opt", -1.5, { kind: "opt", type: "opt", energyKind: "final_energy" }), energy("c_sp", -1.6)],
        }])
        expect(within(found).getByRole("link", { name: "Calculation 1" })).toHaveAttribute("href", "/calculations/c_opt")
        expect(cells(energies)).toEqual(["Calculation 1", "Calculation 2"])
    })
})

describe("ShapeEnergies grouping", () => {
    const rowsOf = (energies: ShapeEnergy[]): ShapeRow[] => [{ observationRef: "co_1", foundBy: [], energies }]

    it("merges equal value, level, program and review into one row", () => {
        const { energies } = show(rowsOf([energy("c1", -40.1), energy("c2", -40.1)]))
        expect(energies.querySelectorAll("tr")).toHaveLength(1)
        expect(cells(energies)).toEqual(["Calculations 1, 2"])
    })

    it("keeps apart rows that differ only by level ref, by program, by review or by workflow tool", () => {
        const { energies } = show(rowsOf([
            energy("c1", -40.1),
            energy("c2", -40.1, { levelRef: "lot_b", level: level("lot_b") }),
            energy("c3", -40.1, { program: "ORCA 5.0" }),
            energy("c4", -40.1, { review: "approved" }),
            energy("c5", -40.1, { workflowTool: "ARC 1.0" }),
        ]))
        expect(energies.querySelectorAll("tr")).toHaveLength(5)
    })

    it("names the workflow tool in the row, so two rows never look identical", () => {
        const { energies } = show(rowsOf([energy("c1", -40.1), energy("c2", -40.1, { workflowTool: "ARC 1.0" })]))
        const texts = [...energies.querySelectorAll("tr")].map((row) => row.textContent)
        expect(texts[0]).not.toContain("run by")
        expect(texts[1]).toContain("run by ARC 1.0")
    })

    it("keeps apart energies of different kinds that share a value and level", () => {
        const { energies } = show(rowsOf([energy("c1", -40.1), energy("c2", -40.1, { energyKind: "correction" })]))
        expect(energies.querySelectorAll("tr")).toHaveLength(2)
        expect(energies.textContent).toContain("correction")
    })
})
