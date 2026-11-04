import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryThermoSection } from "./EntryThermoSection"
import { expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import "../design-system.css"

// "Settings behind this value" on a thermo record: the calculation THIS record links for each level and that
// calculation's grid and SCF convergence, from `include=calculation_settings`.

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_settings"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/thermo`

const settingsBlock = (calc: string | null) => [
    {
        role: "geometry",
        calculation_ref: calc,
        calculation_type: "opt",
        absent_reason: calc ? null : "not_linked",
        settings: calc
            ? [
                { setting: "grid", state: "assumed_default_no_output_log", assumed_value: "defgrid2", reported: [] },
                { setting: "scf_convergence", state: "reported", reported: [{ key: "scf.tol_energy_eh", value: "1e-08" }] },
            ]
            : [],
    },
    { role: "frequency", calculation_ref: null, absent_reason: "not_linked", settings: [] },
    { role: "energy", calculation_ref: null, absent_reason: "not_linked", settings: [] },
]

function record(thermoRef: string, extra: Record<string, unknown> = {}) {
    return {
        thermo_ref: thermoRef,
        scientific_origin: "computed",
        model_kind: "scalar",
        review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        supersession: null,
        h298_kj_mol: 111.1,
        s298_j_mol_k: 222.2,
        h298_uncertainty_kj_mol: null,
        s298_uncertainty_j_mol_k: null,
        nasa: null,
        nasa9: null,
        wilhoit: null,
        points: null,
        temperature_coverage: null,
        evidence_completeness: { score: 1, max: 8, checklist: {} },
        provenance: {
            primary_calculation: null,
            level_of_theory: null,
            software_release: null,
            workflow_tool_release: null,
            statmech_ref: null,
            freq_calculation_ref: null,
            sp_calculation_ref: null,
        },
        group_additivity: null,
        ...extra,
    }
}

function response(records: unknown[]) {
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length },
    }
}

function page() {
    return render(
        <MemoryRouter>
            <EntryThermoSection entryRef={entryRef} conformers={[]} />
        </MemoryRouter>,
    )
}

describe("EntryThermoSection -- settings behind each value", () => {
    it("asks for calculation_settings and shows each record's own block, never another record's", async () => {
        const includes: string[][] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            includes.push(new URL(request.url).searchParams.getAll("include"))
            return HttpResponse.json(response([
                record("thm_alpha", { calculation_settings: settingsBlock("calc_alpha_geometry") }),
                record("thm_beta", { h298_kj_mol: 5, calculation_settings: settingsBlock(null) }),
            ]))
        }))
        page()
        const alpha = (await screen.findByText("thm_alpha")).closest("article") as HTMLElement
        expect(includes[0]).toContain("calculation_settings")
        const block = within(alpha).getByRole("region", { name: "Settings behind this value" })
        expect(within(block).getByRole("link", { name: "calc_alpha_geometry" })).toHaveAttribute("href", "/calculations/calc_alpha_geometry")
        expect(within(block).getByText("Assumed default (no log)")).toBeInTheDocument()
        expect(within(block).getByText("Reported")).toBeInTheDocument()
        const beta = (await screen.findByText("thm_beta")).closest("article") as HTMLElement
        expect(within(beta).queryByRole("link", { name: "calc_alpha_geometry" })).toBeNull()
        expect(within(beta).getAllByText("No calculation linked")).toHaveLength(3)
    })

    it("prints no depositor-typed text from the settings block", async () => {
        const planted = plantSentinels([record("thm_alpha", { calculation_settings: settingsBlock("calc_alpha_geometry") })], ["assumed_value", "key", "value"])
        expectPlanted(planted)
        server.use(http.get(ENDPOINT, () => HttpResponse.json(response(planted.value))))
        page()
        await screen.findByText("thm_alpha")
        await screen.findByText("Settings behind this value")
        expectNoDepositorText()
    })

    it("shows no block when the record carries none", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(response([record("thm_alpha")]))))
        page()
        await screen.findByText("thm_alpha")
        expect(screen.queryByText("Settings behind this value")).toBeNull()
    })
})
