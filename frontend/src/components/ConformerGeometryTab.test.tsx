import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { ConformerGeometryTab } from "./ConformerGeometryTab"

afterEach(() => cleanup())

function conformer(
    observations: ConformerProjection["observations"],
    geometries: ConformerProjection["geometries"] = [],
): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: (observations ?? []).length },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations, calculations: [], geometries,
    } as unknown as ConformerProjection
}

function page(
    observations: ConformerProjection["observations"],
    geometries: ConformerProjection["geometries"] = [],
) {
    return render(
        <MemoryRouter>
            <ConformerGeometryTab conformer={conformer(observations, geometries)} />
        </MemoryRouter>,
    )
}

const lot = { method: "B3LYP", basis: "6-31G(d)" }

describe("ConformerGeometryTab", () => {
    it("attributes each producing geometry to its calculation, level of theory, and observation -- finding #11", async () => {
        page(
            [{
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [{ calculation_ref: "calc_opt_1", type: "opt", level_of_theory: lot }],
            }] as unknown as ConformerProjection["observations"],
            [{ calculation_ref: "calc_opt_1", geometry: { geometry_ref: "geom_g1", natoms: 4, geom_hash: "hashg1000000", role: "final" } }] as unknown as ConformerProjection["geometries"],
        )
        await screen.findByRole("link", { name: "co_1" })
        // The observation summary line stays byte-identical -- an external
        // test (SpeciesEntryPage.test.tsx) asserts its exact text.
        expect(screen.getByText((_, element) => (
            element?.tagName === "LI" && element.textContent === "co_1 — 1 optimization calculation"
        ))).toBeVisible()
        // Attribution row: calculation, level of theory, and the geometry
        // it produced, all on one row a reader can actually click through.
        expect(screen.getByRole("link", { name: "calc_opt_1" })).toHaveAttribute("href", "/calculations/calc_opt_1")
        expect(screen.getByText(/B3LYP\/6-31G\(d\)/)).toBeVisible()
        expect(screen.getByRole("link", { name: "geom_g1" })).toHaveAttribute("href", "/geometries/geom_g1")
    })

    it("styles the observation and calculation refs as links -- colour and underline, not plain black monospace", async () => {
        // Asserted on the raw `style` attribute, not `toHaveStyle` --
        // jsdom's default anchor stylesheet already renders `<a>` underlined
        // with no inline style at all, which made an earlier version of
        // this test pass against a mutant with NO `textDecoration` in
        // `linkStyle` (MEASURED: `toHaveStyle({ textDecoration:
        // "underline" })` is vacuous for an `<a>` in this environment).
        // Reading the attribute string directly requires this component's
        // OWN inline style to be the one setting it.
        page(
            [{
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [{ calculation_ref: "calc_opt_1", type: "opt", level_of_theory: lot }],
            }] as unknown as ConformerProjection["observations"],
            [{ calculation_ref: "calc_opt_1", geometry: { geometry_ref: "geom_g1", natoms: 4, geom_hash: "hashg1000000", role: "final" } }] as unknown as ConformerProjection["geometries"],
        )
        const observationLink = await screen.findByRole("link", { name: "co_1" })
        for (const link of [
            observationLink,
            screen.getByRole("link", { name: "calc_opt_1" }),
            screen.getByRole("link", { name: "geom_g1" }),
        ]) {
            const style = link.getAttribute("style") ?? ""
            expect(style).toContain("color: var(--accent)")
            expect(style).toContain("text-decoration: underline")
        }
    })

    it("groups two optimisation calculations that end at the SAME geometry into one row, not one duplicated link per calculation", async () => {
        // A staged coarse-then-fine reoptimisation: two opt calculations on
        // one observation, both converging on the same final geometry --
        // the live shape this grouping exists for. A mutation that dropped
        // the grouping back to one row per calculation would render
        // "geom_g1" as two separate links with the same accessible name,
        // which `getByRole` below rejects as ambiguous.
        page(
            [{
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [
                    { calculation_ref: "calc_opt_1a", type: "opt", level_of_theory: lot },
                    { calculation_ref: "calc_opt_1b", type: "opt", level_of_theory: lot },
                ],
            }] as unknown as ConformerProjection["observations"],
            [
                { calculation_ref: "calc_opt_1a", geometry: { geometry_ref: "geom_g1", natoms: 4, geom_hash: "hashg1000000", role: "final" } },
                { calculation_ref: "calc_opt_1b", geometry: { geometry_ref: "geom_g1", natoms: 4, geom_hash: "hashg1000000", role: "final" } },
            ] as unknown as ConformerProjection["geometries"],
        )
        expect(await screen.findByRole("link", { name: "geom_g1" })).toHaveAttribute("href", "/geometries/geom_g1")
        expect(screen.getByRole("link", { name: "calc_opt_1a" })).toBeVisible()
        expect(screen.getByRole("link", { name: "calc_opt_1b" })).toBeVisible()
    })

    it("says the geometry is not recorded for an opt calculation with no matching geometry link, rather than dropping the row", async () => {
        page(
            [{
                conformer_observation: { conformer_observation_ref: "co_1" },
                calculations: [{ calculation_ref: "calc_opt_1", type: "opt", level_of_theory: lot }],
            }] as unknown as ConformerProjection["observations"],
            [],
        )
        const link = await screen.findByRole("link", { name: "calc_opt_1" })
        const row = link.closest("li") as HTMLElement
        expect(within(row).getByText(/geometry not recorded/)).toBeVisible()
    })
})
