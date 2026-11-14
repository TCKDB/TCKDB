import { cleanup, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ShapeRow } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { LevelLabel, LevelLabelScope } from "./LevelLabel"
import { ObservationEvidence } from "./ObservationEvidence"
import { ShapeCalculations } from "./ShapeCalculations"
import { ValueSource } from "./ValueSource"

const level = { method: "ccsd(t)", basis: "cc-pvtz", level_of_theory_ref: "lot_q", dispersion: "d3bj", core_treatment: "frozen_core" }
const ready: SinglePointsState = { status: "ready", records: [], capped: false }

let errors: ReturnType<typeof vi.spyOn>

beforeEach(() => {
    errors = vi.spyOn(console, "error").mockImplementation(() => undefined)
})

afterEach(() => {
    errors.mockRestore()
    cleanup()
})

/** React's own report that one element sits where HTML does not allow it. */
function nestingWarnings(): string[] {
    return errors.mock.calls.map((call: unknown[]) => call.map(String).join(" ")).filter((message: string) => /validateDOMNesting|cannot be a descendant|cannot contain/.test(message))
}

function inScope(children: React.ReactNode) {
    return render(<MemoryRouter><LevelLabelScope>{children}</LevelLabelScope></MemoryRouter>)
}

describe("a level's disclosure beside prose", () => {
    it("the nesting check does catch a disclosure placed inside a paragraph", () => {
        inScope(<p><LevelLabel levelOfTheory={level} /></p>)
        expect(nestingWarnings().length).toBeGreaterThan(0)
    })

    it("a source line inside a paragraph prints the level as text, with no disclosure and no nesting warning", () => {
        const { container } = inScope(<p><ValueSource source={{ recordType: "kinetics", ref: "kin_a" }} levels={[{ role: "energy", label: "ccsd(t)/cc-pvtz", level }]} /></p>)
        expect(container.querySelector("p details")).toBeNull()
        expect(container.querySelector("p .level-label-text")?.textContent).toBe("ccsd(t)/cc-pvtz")
        expect(nestingWarnings()).toEqual([])
    })

    it("a source line can put the qualifiers after the line instead", () => {
        const { container } = inScope(<div><ValueSource source={{ recordType: "kinetics", ref: "kin_a" }} showDetails levels={[{ role: "energy", label: "ccsd(t)/cc-pvtz", level }]} /></div>)
        expect(container.querySelector(".value-source details")).toBeNull()
        expect(container.querySelector("div > details")?.textContent).toContain("dispersion")
        expect(nestingWarnings()).toEqual([])
    })

    it("an observation's evidence keeps the disclosure out of the sentence", () => {
        const calculation = { ref: "calc_o", type: "opt", levelRef: "lot_q", levelLabel: "ccsd(t)/cc-pvtz", level, program: "Molpro 1", workflowTool: null, review: "approved" }
        const row: ShapeRow = { observationRef: "co_a", foundBy: [calculation], energies: [{ ...calculation, kind: "sp", hartree: -40.1 }] }
        const { container } = inScope(<ObservationEvidence row={row} singlePoints={ready} optimisations={ready} />)
        expect(container.querySelector("li .level-label details")).toBeNull()
        expect(container.querySelector("li > details")).not.toBeNull()
        expect(nestingWarnings()).toEqual([])
    })

    it("the calculations of a shape keep the disclosure out of the sentence, in the rows and in the spread line", () => {
        const calculation = (ref: string, type: string) => ({ calculation_ref: ref, type, level_of_theory: level, software_release: { software: "Molpro", version: "1" } })
        const conformer = {
            conformer_group: { conformer_group_ref: "cg_a", label: "conformer_1" },
            observations_summary: { total: 2 },
            observations: [
                { conformer_observation: { conformer_observation_ref: "co_a" }, calculations: [calculation("calc_o1", "opt"), calculation("calc_s1", "sp")] },
                { conformer_observation: { conformer_observation_ref: "co_b" }, calculations: [calculation("calc_o2", "opt"), calculation("calc_s2", "sp")] },
            ],
            calculations: [], geometries: [],
        } as unknown as ConformerProjection
        const record = (ref: string, hartree: number) => ({ calculation: { calculation_ref: ref, calculation_type: "sp" }, energy: { energy_hartree: hartree, energy_kind: "electronic" }, level_of_theory: level, software_release: { software: "Molpro", version: "1" } })
        const sp = { status: "ready", records: [record("calc_s1", -40.1), record("calc_s2", -40.2)], capped: false } as unknown as SinglePointsState
        const { container } = inScope(<ShapeCalculations conformer={conformer} singlePoints={sp} optimisations={ready} />)
        expect(container.querySelector(".shape-spread li")?.textContent).toMatch(/differ by up to/)
        expect(container.querySelector("li .level-label details")).toBeNull()
        expect(container.querySelector(".shape-spread li > details")).not.toBeNull()
        expect(container.querySelectorAll("p details")).toHaveLength(0)
        expect(nestingWarnings()).toEqual([])
    })
})
