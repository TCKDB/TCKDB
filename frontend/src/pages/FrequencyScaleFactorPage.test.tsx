import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import FrequencyScaleFactorPage from "./FrequencyScaleFactorPage"

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
        await screen.findByText("fundamental")
        expect(screen.getByText("0.999")).toBeVisible()
        const lotLink = screen.getByRole("link", { name: "b3lyp/def2tzvp" })
        expect(lotLink).toHaveAttribute("href", "/methods/lot_b3lyp")
        expect(screen.getByText("ARC 1.1.0")).toBeVisible()

        const usageTable = screen.getByRole("table", { name: "Statmech records using this frequency scale factor" })
        expect(within(usageTable).getByText("sm_one")).toBeVisible()
    })
})
