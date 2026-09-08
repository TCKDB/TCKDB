import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import MethodsIndexPage from "./MethodsIndexPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const LOT_ENDPOINT = "/api/v1/scientific/level-of-theories/browse"
const SOFTWARE_ENDPOINT = "/api/v1/scientific/meta/software"
const WORKFLOW_TOOL_ENDPOINT = "/api/v1/scientific/meta/workflow-tools"

function page() {
    return render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)
}

function emptyVocab() {
    return [
        http.get(SOFTWARE_ENDPOINT, () => HttpResponse.json({ results: [] })),
        http.get(WORKFLOW_TOOL_ENDPOINT, () => HttpResponse.json({ results: [] })),
    ]
}

function lotRecord(overrides: Record<string, unknown> = {}) {
    return {
        level_of_theory: {
            level_of_theory_ref: "lot_one",
            method: "b3lyp",
            basis: "def2tzvp",
            dispersion: null,
            solvent: null,
            lot_hash: "hash1",
            created_at: "2026-07-21T11:59:29Z",
        },
        evidence_summary: {
            calculation_usage_count: 416,
            has_correction_schemes: true,
            has_frequency_scale_factors: true,
            distinct_software_count: 1,
        },
        available_sections: { has_correction_schemes: true, has_frequency_scale_factors: true, has_used_by: true, has_software: true },
        ...overrides,
    }
}

describe("MethodsIndexPage: loads and renders the three provenance-vocabulary tables", () => {
    it("renders a level-of-theory row linking into /methods/:lotRef, and software/workflow-tool rows", async () => {
        server.use(
            http.get(LOT_ENDPOINT, () => HttpResponse.json({
                records: [lotRecord()],
                pagination: { offset: 0, limit: 200, returned: 1, total: 1 },
            })),
            http.get(SOFTWARE_ENDPOINT, () => HttpResponse.json({ results: [{ value: "Gaussian", count: 416 }] })),
            http.get(WORKFLOW_TOOL_ENDPOINT, () => HttpResponse.json({ results: [{ value: "ARC", count: 416 }] })),
        )
        page()
        expect(await screen.findByRole("heading", { name: "Methods", level: 1 })).toBeVisible()
        const lotLink = await screen.findByRole("link", { name: "b3lyp" })
        expect(lotLink).toHaveAttribute("href", "/methods/lot_one")
        const lotTable = screen.getByRole("table", { name: "Levels of theory" })
        expect(within(lotTable).getByText("416")).toBeVisible()
        const softwareTable = await screen.findByRole("table", { name: "Software" })
        expect(within(softwareTable).getByText("Gaussian")).toBeVisible()
        const workflowTable = await screen.findByRole("table", { name: "Workflow tool" })
        expect(within(workflowTable).getByText("ARC")).toBeVisible()
    })

    it("shows an honest empty state when no levels of theory are deposited", async () => {
        server.use(
            http.get(LOT_ENDPOINT, () => HttpResponse.json({ records: [], pagination: { offset: 0, limit: 200, returned: 0, total: 0 } })),
            ...emptyVocab(),
        )
        page()
        expect(await screen.findByText("No levels of theory have been deposited in this archive yet.")).toBeVisible()
    })

    /**
     * MUTATION TABLE (a): grouping key for the index. `docs/plans/
     * methods-surface.md` §0 ruling 1 (`plan-methods-surface-v2`, not
     * committed to this repo): the index groups by `level_of_theory_ref`,
     * never by `(method, basis)` display text -- two distinct levels can
     * share a method and basis while differing only in dispersion,
     * solvent, or spin treatment. This fixture is constructed exactly to
     * catch a grouping-key regression: two DISTINCT `level_of_theory_ref`
     * rows that share the SAME method/basis text (one with dispersion, one
     * without). A build that (wrongly) grouped by method/basis text would
     * collapse this to one visible row; grouping correctly by ref keeps
     * both.
     */
    it("renders two rows for two distinct level_of_theory_ref values sharing the same method and basis", async () => {
        server.use(
            http.get(LOT_ENDPOINT, () => HttpResponse.json({
                records: [
                    lotRecord({ level_of_theory: { ...lotRecord().level_of_theory, level_of_theory_ref: "lot_no_disp", dispersion: null } }),
                    lotRecord({ level_of_theory: { ...lotRecord().level_of_theory, level_of_theory_ref: "lot_with_disp", dispersion: "GD3BJ" } }),
                ],
                pagination: { offset: 0, limit: 200, returned: 2, total: 2 },
            })),
            ...emptyVocab(),
        )
        page()
        const table = await screen.findByRole("table", { name: "Levels of theory" })
        const rows = within(table).getAllByRole("row")
        // Header row + two data rows -- both share "b3lyp"/"def2tzvp" text,
        // so this count is the only thing that distinguishes "grouped by
        // ref" from "grouped by method/basis text".
        expect(rows).toHaveLength(3)
        const links = within(table).getAllByRole("link", { name: "b3lyp" })
        expect(links).toHaveLength(2)
        expect(links.map((link) => link.getAttribute("href")).sort()).toEqual(["/methods/lot_no_disp", "/methods/lot_with_disp"])
    })
})
