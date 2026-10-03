import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import type { LevelOfTheory } from "../api/scientificSchemas"
import { allProductLevelsAgree, EMPTY_PRODUCT_LEVELS, type ProductLevels } from "../domain/productLevels"
import { lotLabel } from "../api/scientificSchemas"
import { ProductLevelsFact, ProductLevelsTableCells } from "./ProductLevels"

const b3lyp: LevelOfTheory = { method: "B3LYP", basis: "CBSB7", display: "B3LYP/CBSB7", level_of_theory_ref: "lot_g" }
const cbs: LevelOfTheory = {
    method: "CBS-QB3",
    display: "CBS-QB3",
    level_of_theory_ref: "lot_cbs",
    composite_scheme: { composite_scheme_ref: "csch_1", kind: "named_method", name: "CBS-QB3" },
}

afterEach(cleanup)

function levels(extra: Partial<ProductLevels>): ProductLevels {
    return { ...EMPTY_PRODUCT_LEVELS, geometry: b3lyp, frequency: b3lyp, energy: cbs, energy_source: "composite", ...extra }
}

function show(value: ProductLevels) {
    return render(<MemoryRouter><dl><ProductLevelsFact levels={value} /></dl></MemoryRouter>)
}

describe("ProductLevelsFact, composite additions", () => {
    it("shows the server notation as the headline and keeps the three rows beneath", () => {
        const { container } = show(levels({ notation: "CBS-QB3" }))
        expect(container.querySelector("[data-levels-notation]")?.textContent).toContain("CBS-QB3")
        for (const label of ["Geometry", "Frequencies", "Energy"]) expect(screen.getByText(label)).toBeInTheDocument()
    })

    it("shows no headline, and composes none, when the notation is null", () => {
        const { container } = show(levels({ notation: null }))
        expect(container.querySelector("[data-levels-notation]")).toBeNull()
        expect(screen.queryByText("Level of theory")).toBeNull()
        for (const label of ["Geometry", "Frequencies", "Energy"]) expect(screen.getByText(label)).toBeInTheDocument()
    })

    it("notes a recipe-sourced geometry and frequency, naming and linking the recipe", () => {
        show(levels({ geometry_source: "composite_recipe", frequency_source: "composite_recipe" }))
        expect(screen.getAllByText(/from the/)).toHaveLength(2)
        expect(screen.getAllByRole("link", { name: "CBS-QB3" }).length).toBeGreaterThanOrEqual(2)
    })

    it("adds no recipe note for an ordinary geometry", () => {
        show(levels({ geometry_source: "opt", frequency_source: "freq" }))
        expect(screen.queryByText(/from the/)).toBeNull()
    })

    it("shows the verification badge beside a composite energy, and a contradiction distinctly", () => {
        const ok = show(levels({ composite_energy_verification: { state: "recomputed", assembly: "assembled" } }))
        expect(ok.container.querySelector("[data-verification-tone='confirmed']")).not.toBeNull()
        ok.unmount()
        const bad = show(levels({ composite_energy_verification: { state: "recompute_mismatch", assembly: "assembled", difference_hartree: -0.001 } }))
        expect(bad.container.querySelector("[data-verification-tone='contradiction']")).not.toBeNull()
        expect(screen.getByText("Contradiction")).toBeInTheDocument()
    })

    it("says verification is not recorded for a composite energy with none, and says nothing for a non-composite", () => {
        const none = show(levels({ composite_energy_verification: null }))
        expect(screen.getByText("Verification not recorded")).toBeInTheDocument()
        none.unmount()
        show(levels({ energy: b3lyp, energy_source: "sp" }))
        expect(screen.queryByText("Verification not recorded")).toBeNull()
    })

    it("shows a muted legacy note", () => {
        const { container } = show(levels({ legacy_composite_shape: "named_method_level_on_non_composite_calculation" }))
        expect(container.querySelector("[data-legacy-composite-shape]")?.textContent).toContain("older shape")
    })
})

describe("an identical-values group", () => {
    it("is not shared when members differ in notation or verification", () => {
        const a = levels({ notation: "CBS-QB3" })
        expect(allProductLevelsAgree([a, { ...a }])).toBe(true)
        expect(allProductLevelsAgree([a, { ...a, notation: "other" }])).toBe(false)
        expect(allProductLevelsAgree([a, { ...a, composite_energy_verification: { state: "unverifiable" } }])).toBe(false)
    })
})

describe("ProductLevelsTableCells notation column", () => {
    function cells(value: ProductLevels) {
        return render(<MemoryRouter><table><tbody><tr><ProductLevelsTableCells levels={value} showNotation /></tr></tbody></table></MemoryRouter>)
    }

    it("shows the notation, and 'not recorded' (never blank) when it is null", () => {
        const withNotation = cells(levels({ notation: "CBS-QB3" }))
        expect(withNotation.container.querySelector("td[data-label='Level of theory']")?.textContent).toBe("CBS-QB3")
        withNotation.unmount()
        const without = cells(levels({ notation: null }))
        expect(without.container.querySelector("td[data-label='Level of theory']")?.textContent).toBe("not recorded")
    })

    it("adds the column only when asked", () => {
        const { container } = render(<MemoryRouter><table><tbody><tr><ProductLevelsTableCells levels={levels({ notation: "x" })} /></tr></tbody></table></MemoryRouter>)
        expect(container.querySelector("td[data-label='Level of theory']")).toBeNull()
    })
})

describe("lotLabel", () => {
    it("prefers the server's full label, so two core treatments read differently, and falls back without one", () => {
        const base = { method: "CCSD(T)", basis: "cc-pCVTZ", display: "CCSD(T)/cc-pCVTZ" }
        expect(lotLabel({ ...base, label: "CCSD(T)/cc-pCVTZ (core=all_electron)" })).toBe("CCSD(T)/cc-pCVTZ (core=all_electron)")
        expect(lotLabel({ ...base, label: null })).toBe("CCSD(T)/cc-pCVTZ")
        expect(lotLabel({ method: "AM1" })).toBe("AM1")
    })
})
