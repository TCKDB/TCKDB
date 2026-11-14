import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import FrequencyScaleFactorPage from "./FrequencyScaleFactorPage"
import { expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

function page(ref = "fsf_one") {
    return render(
        <MemoryRouter initialEntries={[`/methods/frequency-scale-factors/${ref}`]}>
            <Routes>
                <Route path="/methods/frequency-scale-factors/:fsfRef" element={<FrequencyScaleFactorPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockResponse(overrides: Record<string, unknown> = {}) {
    return {
        request: { include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
        record: {
            frequency_scale_factor: {
                frequency_scale_factor_ref: "fsf_one",
                scale_kind: "fundamental",
                value: 0.999,
                note: "http://cccbdb.nist.gov/vibscalejust.asp",
                created_at: "2026-07-21T11:59:29Z",
            },
            level_of_theory: {
                level_of_theory_ref: "lot_b3lyp",
                method: "b3lyp",
                basis: "def2tzvp",
                dispersion: null,
                solvent: null,
                spin_treatment: null,
                label: null,
                display: "b3lyp/def2tzvp",
            },
            software_release: { software_release_ref: "", software: "Gaussian", version: null },
            workflow_tool_release: { workflow_tool_release_ref: "wfr_one", workflow_tool: "ARC", version: "1.1.0" },
            literature: null,
            evidence_summary: { has_literature_source: false, has_workflow_tool_source: true, has_software_dimension: true, statmech_usage_count: 3, has_statmech_usage: true },
            available_sections: { has_used_by: true, has_literature: false },
            used_by: [{ record_type: "statmech", record_ref: "sm_one", record_id: null, endpoint: "/api/v1/scientific/statmech/sm_one" }],
            ...overrides,
        },
    }
}

describe("FrequencyScaleFactorPage: the standalone scale-factor page", () => {
    it("renders the value, its owning level of theory (linked), workflow tool, and usage", async () => {
        server.use(http.get("/api/v1/scientific/frequency-scale-factors/fsf_one", () => HttpResponse.json(mockResponse())))
        page()
        expect(await screen.findByRole("heading", { level: 1, name: "Fundamental scale factor 0.999" })).toBeVisible()
        expect(screen.getByText("0.999")).toBeVisible()
        await waitFor(() => expect(titleHasKind(document.title, "/methods/frequency-scale-factors/:ref")).toBe(true))
        const lotLink = screen.getByRole("link", { name: "b3lyp/def2tzvp · Gaussian" })
        expect(lotLink).toHaveAttribute("href", "/methods/lot_b3lyp")
        expect(screen.getByText("ARC 1.1.0")).toBeVisible()

        const usageTable = screen.getByRole("table", { name: "Statmech records using this frequency scale factor" })
        expect(within(usageTable).getByText("Record 1")).toBeVisible()
        expect(usageTable.textContent).not.toContain("sm_one")
    })
})

describe("FrequencyScaleFactorPage -- no depositor-typed text", () => {
    it("prints none of the fields a depositor typed, in text or in any attribute", async () => {
        // `level_of_theory.label` is rendered by the server from the level's own facets, not typed.
        const planted = plantSentinels(mockResponse(), ["note"])
        expectPlanted(planted)
        const { value } = planted
        server.use(http.get("/api/v1/scientific/frequency-scale-factors/fsf_one", () => HttpResponse.json(value)))
        page()
        expect(await screen.findByRole("heading", { level: 1, name: "Fundamental scale factor 0.999" })).toBeVisible()
        expect(screen.getByText("0.999")).toBeVisible()

        expectNoDepositorText()
    })
})

describe("FrequencyScaleFactorPage: wording and links", () => {
    it("writes a ZPE scale kind as ZPE, never Zpe", async () => {
        server.use(http.get("/api/v1/scientific/frequency-scale-factors/fsf_one", () => HttpResponse.json(mockResponse({
            frequency_scale_factor: { ...mockResponse().record.frequency_scale_factor, scale_kind: "zpe", value: 0.98712345 },
        }))))
        page()
        expect(await screen.findByRole("heading", { level: 1, name: "ZPE scale factor 0.987123" })).toBeVisible()
        expect(screen.queryByText(/Zpe/)).not.toBeInTheDocument()
    })

    it("links a record that has a page and leaves a statmech record as plain text", async () => {
        server.use(http.get("/api/v1/scientific/frequency-scale-factors/fsf_one", () => HttpResponse.json(mockResponse({
            used_by: [
                { record_type: "species_entry", record_ref: "spe_one", record_id: null, endpoint: "/x" },
                { record_type: "statmech", record_ref: "sm_one", record_id: null, endpoint: "/y" },
            ],
        }))))
        page()
        await screen.findByRole("heading", { level: 1 })
        const usageTable = screen.getByRole("table", { name: "Statmech records using this frequency scale factor" })
        const links = within(usageTable).getAllByRole("link")
        expect(links).toHaveLength(1)
        expect(links[0]).toHaveAttribute("href", "/species-entries/spe_one")
        expect(links[0].textContent).toBe("Record 1")
        expect(within(usageTable).getByText("Record 2")).toBeVisible()
        expect(within(usageTable).getByText("Record 2").closest("a")).toBeNull()
        expect(usageTable.textContent).not.toMatch(/spe_one|sm_one/)
    })
})
