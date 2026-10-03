import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AuthProvider } from "../components/AuthProvider"
import CalculationDetailPage from "./CalculationDetailPage"
import { resetAllRequestCaches } from "../api/requestCache"
import "../design-system.css"

// A composite calculation page (ADR 0021, P7b): the verification the server
// sends, its contradiction marker, the term numbering, the inputs as links,
// and the conformer row without the depositor's label.

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => {
    resetAllRequestCaches()
    server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
})
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/calculations/calc_cbs"

function page() {
    return render(
        <AuthProvider>
            <MemoryRouter initialEntries={["/calculations/calc_cbs"]}>
                <Routes>
                    <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

const SCHEME = { composite_scheme_ref: "csch_1", kind: "extrapolation", name: "CBS[ref:CCSD(T)/cc-pVQZ + ...]" }

function record(overrides: Record<string, unknown> = {}, composite: Record<string, unknown> | null = null) {
    const none = false
    return {
        calculation: {
            calculation_ref: "calc_cbs",
            type: "composite",
            quality: "raw",
            created_at: "2026-10-03T12:00:00",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        owner: {
            kind: "species_entry",
            species_entry: {
                species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state",
                formula: "CH4", canonical_smiles: "C", inchi_key: "VNWKTOKETHGBQD-UHFFFAOYSA-N", charge: 0, multiplicity: 1,
                species_entry_kind: "minimum", electronic_state_kind: "ground",
            },
            transition_state_entry: null,
        },
        conformer: null,
        level_of_theory: {
            level_of_theory_ref: "lot_cbs", method: SCHEME.name, basis: null, display: SCHEME.name, label: SCHEME.name,
            composite_scheme: SCHEME,
        },
        software_release: null,
        workflow_tool_release: null,
        literature: null,
        provenance: {
            has_result: true, converged: null, geometry_validation_status: "not_present", scf_stability_status: "not_present",
            submission_ref: "sub_demo",
        },
        available_sections: {
            has_results: true, has_dependencies: none, has_parameters: none, has_constraints: none, has_artifacts: none,
            has_input_geometries: none, has_output_geometries: none, has_geometry_validation: none, has_scf_stability: none,
            has_wavefunction_diagnostic: none, has_spin_diagnostic: none, has_freq_modes: none, has_hessian: none,
            has_scan: none, has_irc: none, has_path_search: none, has_execution_environment: none, has_energy_corrections: none,
        },
        results: composite === null ? null : { kind: "composite", composite },
        dependencies: [],
        input_geometries: [],
        output_geometries: [],
        review_history: [],
        composite_energy_verification: null,
        legacy_composite_shape: null,
        ...overrides,
    }
}

const ASSEMBLED = {
    assembly: "assembled",
    electronic_energy_hartree: -76.37,
    e0_hartree: null,
    recipe_zpe_hartree: null,
    terms: [{ term_position: 0, value_hartree: -76.06 }, { term_position: 1, value_hartree: -0.31 }],
    inputs: [
        { term_position: 0, slot: "value", calculation_ref: "calc_in_a", cardinal_number: null },
        { term_position: 1, slot: "high", calculation_ref: "calc_in_b", cardinal_number: 4 },
    ],
}

async function load(rec: unknown) {
    server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: rec })))
    page()
    return screen.findByRole("heading", { level: 1, name: /^composite of / })
}

describe("composite calculation page", () => {
    it("shows the verification badge the server sent", async () => {
        await load(record({ composite_energy_verification: { state: "recomputed", assembly: "assembled" } }, ASSEMBLED))
        const block = document.getElementById("composite-verification") as HTMLElement
        expect(within(block).getByText("Recomputed from its inputs")).toBeInTheDocument()
        expect(block.querySelector("[data-verification-tone='confirmed']")).not.toBeNull()
        expect(document.querySelector("[data-contradiction-marker]")).toBeNull()
    })

    it("shows the badge even when the composite result row is absent (the server says unverifiable)", async () => {
        await load(record({ composite_energy_verification: { state: "unverifiable", assembly: "program_run", reason: "no_result_stated" } }, null))
        const block = document.getElementById("composite-verification") as HTMLElement
        expect(within(block).getByText("Cannot be checked")).toBeInTheDocument()
        expect(block.textContent).toContain("no composite result is recorded")
    })

    it("says verification is not recorded for a composite calculation with none", async () => {
        await load(record({}, ASSEMBLED))
        expect(screen.getByText("Verification is not recorded for this calculation.")).toBeInTheDocument()
    })

    it("marks a contradiction beside the headline energy, linking to the verification block", async () => {
        await load(record({ composite_energy_verification: { state: "recompute_mismatch", assembly: "assembled", difference_hartree: -0.001, tolerance_hartree: 4.5e-6 } }, ASSEMBLED))
        const marker = document.querySelector("[data-contradiction-marker]") as HTMLAnchorElement
        expect(marker).not.toBeNull()
        expect(marker.getAttribute("href")).toBe("#composite-verification")
        expect(document.getElementById("composite-verification")?.querySelector("[data-verification-tone='contradiction']")).not.toBeNull()
        expect(marker.closest(".calc-headline-energy")).not.toBeNull()
    })

    it("numbers terms from 1 and links each input calculation", async () => {
        await load(record({}, ASSEMBLED))
        const terms = screen.getByRole("table", { name: "Recipe terms of this composite calculation" })
        expect(within(terms).getAllByRole("row").slice(1).map((r) => r.querySelector("td")?.textContent)).toEqual(["1", "2"])
        const inputs = screen.getByRole("table", { name: "Calculations this composite is built from" })
        expect(within(inputs).getAllByRole("row").slice(1).map((r) => r.querySelector("td")?.textContent)).toEqual(["1", "2"])
        expect(within(inputs).getByRole("link", { name: "calc_in_a" })).toHaveAttribute("href", "/calculations/calc_in_a")
        expect(within(inputs).getByText("high side")).toBeInTheDocument()
    })

    it("links the recipe once, as 'view recipe'", async () => {
        await load(record({}, ASSEMBLED))
        const links = screen.getAllByRole("link", { name: "view recipe" })
        expect(links.length).toBeGreaterThanOrEqual(1)
        for (const link of links) expect(link).toHaveAttribute("href", "/methods/composite-schemes/csch_1")
    })

    it("never shows a depositor's conformer group label", async () => {
        await load(record({ conformer: { conformer_observation_ref: "cobs_1", conformer_group_ref: "cg_1", conformer_group_label: "conformer_1" } }, ASSEMBLED))
        expect(screen.queryByText("conformer_1")).not.toBeInTheDocument()
        expect(screen.getByRole("link", { name: "group cg_1" })).toHaveAttribute("href", "/conformer-groups/cg_1")
    })
})
