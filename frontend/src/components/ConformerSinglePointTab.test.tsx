import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { ConformerSinglePointTab } from "./ConformerSinglePointTab"

afterEach(() => cleanup())

function conformer(observations: ConformerProjection["observations"]): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: (observations ?? []).length },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations, calculations: [], geometries: [],
    } as unknown as ConformerProjection
}

function page(observations: ConformerProjection["observations"], spEnergies: SpeciesCalculationEnergyRecord[] = []) {
    return render(
        <MemoryRouter>
            <ConformerSinglePointTab conformer={conformer(observations)} spEnergies={spEnergies} />
        </MemoryRouter>,
    )
}

describe("ConformerSinglePointTab", () => {
    it("renders the calculation TYPE as a pill, distinct from the plain-text calculation ref", async () => {
        page([{
            conformer_observation: { conformer_observation_ref: "co_1" },
            calculations: [{ calculation_ref: "calc_sp_1", type: "sp" }],
        }] as unknown as ConformerProjection["observations"])

        const row = (await screen.findByRole("link", { name: "calc_sp_1" })).closest("article") as HTMLElement
        const typePill = within(row).getByText("sp")
        expect(typePill).toHaveClass("value-pill")
        expect(typePill.tagName).toBe("SPAN")

        // The identifier stays plain, never inside a pill.
        const refLink = within(row).getByRole("link", { name: "calc_sp_1" })
        expect(refLink).not.toHaveClass("value-pill")
    })

    it("does not repeat the hartree/conversion-rule sentence that used to sit under the energy toggle", async () => {
        page([{
            conformer_observation: { conformer_observation_ref: "co_1" },
            calculations: [{ calculation_ref: "calc_sp_1", type: "sp" }],
        }] as unknown as ConformerProjection["observations"], [
            { calculation: { calculation_ref: "calc_sp_1", calculation_type: "sp" }, energy: { energy_hartree: -76.5, energy_kind: "electronic_energy" } },
        ])
        await screen.findByRole("link", { name: "calc_sp_1" })
        expect(screen.queryByText(/Always stored in hartree/)).not.toBeInTheDocument()
        // The unit still carries on the displayed value regardless.
        expect(screen.getByText(/-76\.500000 hartree/)).toBeVisible()
    })
})
