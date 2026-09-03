import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

// A cell's own DOM row -- lets an assertion bind a value to the OBSERVATION
// it belongs to, not just assert the value appears somewhere on the page.
function rowFor(observationRef: string): HTMLElement {
    const cell = screen.getByRole("link", { name: observationRef })
    return cell.closest("tr") as HTMLElement
}

describe("ConformerSinglePointTab", () => {
    it("renders one table, not one card per observation -- observation/calculation/energy/level of theory/software columns", async () => {
        page([{
            conformer_observation: { conformer_observation_ref: "co_1" },
            calculations: [{ calculation_ref: "calc_sp_1", type: "sp", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { software: "Gaussian" } }],
        }] as unknown as ConformerProjection["observations"], [
            { calculation: { calculation_ref: "calc_sp_1", calculation_type: "sp" }, energy: { energy_hartree: -78.623756, energy_kind: "electronic_energy" } },
        ])

        const table = await screen.findByRole("table", { name: "Single-point energies" })
        const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent)
        expect(headers).toEqual(["Observation", "Calculation", "Energy", "Level of theory", "Software"])

        const row = rowFor("co_1")
        expect(within(row).getByRole("link", { name: "calc_sp_1" })).toBeVisible()
        expect(within(row).getByText(/-78\.623756 hartree/)).toBeVisible()
        expect(within(row).getByText("b3lyp/def2tzvp")).toBeVisible()
        expect(within(row).getByText("Gaussian")).toBeVisible()
    })

    it("keeps an observation with NO single-point calculation as its own row, rather than dropping it", async () => {
        page([
            {
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [{ calculation_ref: "calc_sp_1", type: "sp" }],
            },
            {
                conformer_observation: { conformer_observation_ref: "co_2" },
                calculations: [{ calculation_ref: "calc_opt_2", type: "opt" }],
            },
        ] as unknown as ConformerProjection["observations"])

        await screen.findByRole("link", { name: "co_1" })
        const emptyRow = rowFor("co_2")
        expect(within(emptyRow).getByText("no single-point calculation recorded")).toBeVisible()
        expect(within(emptyRow).queryByRole("link", { name: /calc_/ })).not.toBeInTheDocument()
    })

    it("gives an observation with TWO single-point calculations two rows, never merging them", async () => {
        page([{
            conformer_observation: { conformer_observation_ref: "co_1" },
            calculations: [
                { calculation_ref: "calc_sp_1", type: "sp" },
                { calculation_ref: "calc_sp_2", type: "sp" },
            ],
        }] as unknown as ConformerProjection["observations"])

        const rows = (await screen.findAllByRole("link", { name: "co_1" })).map((link) => link.closest("tr"))
        expect(rows).toHaveLength(2)
        expect(screen.getByRole("link", { name: "calc_sp_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "calc_sp_2" })).toBeVisible()
    })

    it("shows 'not recorded' for a calculation ref with no matching energy record, without dropping the row", async () => {
        page([{
            conformer_observation: { conformer_observation_ref: "co_1" },
            calculations: [{ calculation_ref: "calc_sp_missing", type: "sp" }],
        }] as unknown as ConformerProjection["observations"], [])

        const row = rowFor("co_1")
        expect(await within(row).findByRole("link", { name: "calc_sp_missing" })).toBeVisible()
        // Scoped to the Energy cell specifically -- the level-of-theory and
        // software cells ALSO say "not recorded" for this bare fixture, so
        // a bare `getByText` here would match three cells, not one.
        const energyCell = row.querySelector('[data-label="Energy"]') as HTMLElement
        expect(within(energyCell).getByText("not recorded")).toBeVisible()
    })

    it("has ONE unit switcher for the whole table, and switching it updates every row's energy together", async () => {
        const user = userEvent.setup()
        page([
            {
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [{ calculation_ref: "calc_sp_1", type: "sp" }],
            },
            {
                conformer_observation: { conformer_observation_ref: "co_2" },
                calculations: [{ calculation_ref: "calc_sp_2", type: "sp" }],
            },
        ] as unknown as ConformerProjection["observations"], [
            { calculation: { calculation_ref: "calc_sp_1", calculation_type: "sp" }, energy: { energy_hartree: -1, energy_kind: "electronic_energy" } },
            { calculation: { calculation_ref: "calc_sp_2", calculation_type: "sp" }, energy: { energy_hartree: -2, energy_kind: "electronic_energy" } },
        ])

        await screen.findByRole("link", { name: "co_1" })
        // Exactly one fieldset of unit buttons on the page, not one per row.
        expect(screen.getAllByRole("group", { name: /Energy display unit/ })).toHaveLength(1)

        await user.click(screen.getByRole("button", { name: "eV" }))
        expect(within(rowFor("co_1")).getByText(/-27\.2114 eV/)).toBeVisible()
        expect(within(rowFor("co_2")).getByText(/-54\.4228 eV/)).toBeVisible()
        // Switching away and back reproduces the exact original string --
        // every render recomputes from the stored hartree value, nothing
        // here accumulates rounding error across toggles.
        await user.click(screen.getByRole("button", { name: "hartree" }))
        expect(within(rowFor("co_1")).getByText(/-1\.000000 hartree/)).toBeVisible()
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
