import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { ReactionScheme } from "./ReactionScheme"
import { stageGeometryRef } from "../domain/transitionStateStage"

afterEach(cleanup)

const participant = (ref: string, formula: string | null, smiles: string, index: number, stoichiometry = 1) => ({
    species_entry_ref: ref, formula, smiles, stoichiometry, participant_index: index,
})

describe("ReactionScheme", () => {
    it("spells out formulas, plus signs and the arrow for a screen reader, with coefficients", () => {
        render(
            <MemoryRouter>
                <ReactionScheme
                    reactants={[participant("spe_a", "NH2", "[NH2]", 0), participant("spe_a", "NH2", "[NH2]", 1)]}
                    products={[participant("spe_b", "N2H4", "NN", 0)]}
                    reversible={false}
                />
            </MemoryRouter>,
        )
        expect(screen.getByRole("group", { name: "2 NH2 reacts to form N2H4" })).toBeInTheDocument()
    })

    it("says reacts reversibly with for a reversible reaction and falls back to the SMILES without a formula", () => {
        render(
            <MemoryRouter>
                <ReactionScheme reactants={[participant("spe_a", null, "[OH]", 0)]} products={[participant("spe_b", "H2O", "O", 0)]} reversible />
            </MemoryRouter>,
        )
        const scheme = screen.getByRole("group", { name: "[OH] reacts reversibly with H2O" })
        expect(within(scheme).getAllByRole("link")).toHaveLength(2)
        expect(within(scheme).getByRole("link", { name: /\[OH\]/ })).toHaveAttribute("href", "/species-entries/spe_a")
    })
})

describe("stageGeometryRef", () => {
    it("takes the first final geometry and never an input or a guess", () => {
        expect(stageGeometryRef({ geometries: [{ geometry_ref: "geom_in", role: "input" }, { geometry_ref: "geom_f1", role: "final" }, { geometry_ref: "geom_f2", role: "final" }] })).toBe("geom_f1")
        expect(stageGeometryRef({ geometries: [{ geometry_ref: "geom_in", role: "input" }] })).toBeNull()
        expect(stageGeometryRef({ geometries: null })).toBeNull()
    })
})
