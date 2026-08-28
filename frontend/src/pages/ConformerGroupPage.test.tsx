import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerGroupPage from "./ConformerGroupPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())
function page() { return render(<MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}><Routes><Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} /></Routes></MemoryRouter>) }
const payload = { record: {
    conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", note: null, review: { status: "not_reviewed" } },
    species: { species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state", canonical_smiles: "[CH3]" },
    observations_summary: { total: 2, by_scientific_origin: { computed: 2 } },
    evidence_summary: { calculation_count: 3, optimization_chain_count: 1, geometry_count: 2, evidence_coverage: { opt: 2, freq: 1, sp: 1 } },
    observations: [{ conformer_observation: { conformer_observation_ref: "co_one", scientific_origin: "computed", review: { status: "reviewed" } }, evidence_summary: { calculation_count: 2, geometry_count: 1, has_opt: true, has_freq: true, has_sp: false, levels_of_theory: {} }, calculations: [
        { calculation_ref: "calc_opt", type: "optimization", quality: "ok", review: { status: "reviewed" }, level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { name: "Gaussian" }, workflow_tool_release: { name: "ARC" } },
        { calculation_ref: "calc_freq", type: "frequency", quality: "ok", review: { status: "not_reviewed" }, level_of_theory: { method: "wb97xd", basis: "def2tzvp" }, software_release: { name: "Gaussian" } },
    ], geometries: [{ calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } }] }],
    calculations: [], geometries: [{ calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } }, { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } }],
} }
describe("ConformerGroupPage", () => {
    it("keeps observations, calculation stages, methods, and geometry inventory distinct", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", ({ request }) => { expect(new URL(request.url).searchParams.getAll("include")).toEqual(["observations", "calculations", "geometries"]); return HttpResponse.json(payload) }))
        page(); expect(await screen.findByRole("heading", { name: "conformer_1" })).toBeVisible()
        expect(screen.getByText(/One torsional basin, shown through its deposited observations/)).toBeVisible()
        expect(screen.getByText("3")).toBeVisible(); expect(screen.getByText("1 optimisation chains")).toBeVisible()
        expect(screen.getByText("b3lyp/def2tzvp")).toBeVisible(); expect(screen.getByText("wb97xd/def2tzvp")).toBeVisible()
        expect(screen.getByRole("link", { name: "geo_one" })).toHaveAttribute("href", "/geometries/geo_one")
        expect(screen.getByText(/Their count is not a conformer count/)).toBeVisible()
    })
    it("shows a specific not-found state", async () => { server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json({}, { status: 404 }))); page(); expect(await screen.findByRole("heading", { name: "Conformer basin not found" })).toBeVisible() })
})
