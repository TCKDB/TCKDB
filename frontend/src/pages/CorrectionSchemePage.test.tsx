import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import CorrectionSchemePage from "./CorrectionSchemePage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

function page(ref = "ecs_bac") {
    return render(
        <MemoryRouter initialEntries={[`/methods/schemes/${ref}`]}>
            <Routes>
                <Route path="/methods/schemes/:ecsRef" element={<CorrectionSchemePage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockResponse(overrides: Record<string, unknown> = {}) {
    return {
        request: { include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
        record: {
            energy_correction_scheme: {
                energy_correction_scheme_ref: "ecs_bac",
                name: "bac_petersson",
                scheme_kind: "bac_petersson",
                version: null,
                units: "kcal_mol",
                note: "Per-species BAC computed by Arkane (bac_type=p).",
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
            literature: null,
            evidence_summary: { atom_param_count: 0, bond_param_count: 2, component_param_count: 0, has_corrections: true, applied_usage_count: 82, has_applied_usage: true, has_literature_source: false },
            available_sections: { has_corrections: true, has_used_by: true, has_literature: false },
            corrections: [
                { correction_kind: "bond", target: "C-H", value: 1.1, component_kind: null },
                { correction_kind: "bond", target: "C-C", value: 2.2, component_kind: null },
            ],
            used_by: [{
                record_type: "species_entry", record_ref: "spe_one", record_id: null, endpoint: "/api/v1/scientific/species-entries/spe_one",
                application_role: "bac_total", applied_value: -3.6, applied_value_unit: "kcal_mol", applied_value_hartree: -0.0057,
                temperature_k: null, applied_note: null, source_calculation_ref: "calc_one", source_calculation_endpoint: "/api/v1/scientific/calculations/calc_one",
                component_count: 2,
            }],
            ...overrides,
        },
    }
}

describe("CorrectionSchemePage: the standalone scheme page", () => {
    it("renders the scheme identity, its parameter table, its owning level of theory (linked), and its applications", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse())))
        page()
        expect(await screen.findByRole("heading", { name: "bac_petersson", level: 1 })).toBeVisible()
        const lotLink = screen.getByRole("link", { name: "b3lyp/def2tzvp" })
        expect(lotLink).toHaveAttribute("href", "/methods/lot_b3lyp")

        const paramTable = screen.getByRole("table", { name: "Bond correction parameters" })
        expect(within(paramTable).getByText("C-H")).toBeVisible()
        expect(within(paramTable).getByText("C-C")).toBeVisible()

        const appTable = screen.getByRole("table", { name: "Applications of this correction scheme" })
        expect(within(appTable).getByText("spe_one")).toBeVisible()
        const calcLink = within(appTable).getByRole("link", { name: "calc_one" })
        expect(calcLink).toHaveAttribute("href", "/calculations/calc_one")
    })

    it("reads a null level_of_theory honestly, as 'not tied to a specific level of theory' -- never 'any level of theory' or blank", async () => {
        server.use(http.get("/api/v1/scientific/energy-correction-schemes/ecs_bac", () => HttpResponse.json(mockResponse({ level_of_theory: null }))))
        page()
        await screen.findByRole("heading", { name: "bac_petersson", level: 1 })
        expect(screen.getByText("not tied to a specific level of theory")).toBeVisible()
    })
})
