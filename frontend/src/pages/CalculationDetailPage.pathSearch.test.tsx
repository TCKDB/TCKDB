import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AuthProvider } from "../components/AuthProvider"
import CalculationDetailPage from "./CalculationDetailPage"

const REF = "calc_ps_one"
const GFN2 = { level_of_theory_ref: "lot_gfn2", method: "gfn2", basis: null, display: "gfn2" }
const B3LYP = { level_of_theory_ref: "lot_b3lyp", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }

const RESULT_NULLS = { sp: null, opt: null, freq: null, scan: null, irc: null, path_search: null, composite: null }

const record = {
    calculation: { calculation_ref: REF, type: "path_search", quality: "raw", created_at: "2026-11-09T20:25:23", review: { status: "not_reviewed" } },
    owner: {
        kind: "transition_state_entry",
        transition_state_entry: {
            transition_state_ref: "ts_one", transition_state_entry_ref: "tse_one", charge: 0, multiplicity: 3,
            status: "optimized", reaction_entry_ref: "rxe_one",
        },
    },
    level_of_theory: GFN2,
    software_release: { software_release_ref: "srel_xtb", software: "xtb", version: "6.3.3" },
    workflow_tool_release: { workflow_tool_release_ref: "wfr_arc", workflow_tool: "ARC", version: "1.1.0" },
    provenance: { has_result: true, geometry_validation_status: "not_present", scf_stability_status: "not_present" },
    available_sections: {
        has_results: true, has_dependencies: true, has_parameters: false, has_constraints: false, has_artifacts: false,
        has_input_geometries: false, has_output_geometries: true, has_geometry_validation: false, has_scf_stability: false,
        has_wavefunction_diagnostic: false, has_spin_diagnostic: false, has_freq_modes: false, has_hessian: false,
        has_scan: false, has_irc: false, has_path_search: true, has_execution_environment: false, has_energy_corrections: false,
        path_search_applicable: true,
    },
    results: { kind: "path_search", ...RESULT_NULLS, path_search: { method: "gsm", is_double_ended: true, converged: null, n_points: 3 } },
    dependencies: [{ role: "optimized_from", direction: "parent", parent_calculation_ref: REF, child_calculation_ref: "calc_opt" }],
    input_geometries: [],
    output_geometries: [0, 1, 2].map((index) => ({ geometry_ref: `geom_${index}`, output_order: index + 1, role: "path_search_point", natoms: 9 })),
    review_history: [{ status: "not_reviewed", note: null, reviewed_at: null }],
}

const points = {
    path_search: {
        method: "gsm", is_double_ended: true, converged: null, n_points: 3, selected_ts_point_index: 1,
        climbing_image_index: null, source_endpoint_count: 2, zero_energy_reference_hartree: null,
        stored_point_count: 3, ts_guess_count: 1, climbing_image_count: 0,
    },
    points: [0, 1, 2].map((index) => ({
        point_index: index,
        path_coordinate: [0, 3.758883, 5.74][index],
        electronic_energy_hartree: null,
        relative_energy_kj_mol: null,
        is_ts_guess: index === 1,
        is_climbing_image: false,
        geometry_ref: `geom_${index}`,
        geometry_link: { geometry_ref: `geom_${index}`, natoms: 9 },
    })),
    pagination: { returned: 3, total: 3 },
    next_cursor: null,
}

function child(ref: string, type: string, results: Record<string, unknown>) {
    return { calculation: { calculation_ref: ref, type }, level_of_theory: B3LYP, results: { kind: type, ...RESULT_NULLS, ...results } }
}

const server = setupServer(
    http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
    http.get(`/api/v1/scientific/calculations/${REF}/path-search`, () => HttpResponse.json(points)),
    http.get(`/api/v1/scientific/calculations/${REF}`, () => HttpResponse.json({ record })),
    http.get("/api/v1/scientific/calculations/search", ({ request }) => {
        const parent = new URL(request.url).searchParams.get("parent_calculation_ref")
        if (parent === REF) return HttpResponse.json({ records: [child("calc_opt", "opt", { opt: { converged: true, n_steps: 2, final_energy_hartree: -117.786148505 } })] })
        if (parent === "calc_opt") {
            return HttpResponse.json({
                records: [
                    child("calc_irc", "irc", { irc: { direction: "both", point_count: 92 } }),
                    child("calc_freq", "freq", { freq: { n_imag: 1, imag_freq_cm1: -1886.4263 } }),
                ],
            })
        }
        return HttpResponse.json({ records: [] })
    }),
    http.get("/api/v1/scientific/reaction-entries/rxe_one/energy-profile", () => HttpResponse.json({
        request: {},
        reaction_entry_ref: "rxe_one",
        profiles: [{
            transition_state_entry_ref: "tse_one", transition_state_entry_review_status: "not_reviewed", level_of_theory: B3LYP,
            program: "Gaussian", energy_types: ["electronic"], versions: ["16"], energy_kind: "electronic", definition: "",
            reactants: [], products: [],
            transition_state: {
                transition_state_entry_ref: "tse_one", calculation_ref: "calc_opt", energy_type: "electronic", electronic_energy_hartree: -117.78,
                software: { program: "Gaussian" }, review_status: "not_reviewed",
            },
            energy_hartree: { reactants: 0, transition_state: 0, products: 0 }, relative_kj_mol: { reactants: 0, transition_state: 0, products: 0 },
        }],
    })),
    http.get("/api/v1/*", () => HttpResponse.json({ detail: "not in this test" }, { status: 404 })),
)
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => cleanup())
afterAll(() => server.close())

function page() {
    return render(
        <AuthProvider>
            <MemoryRouter initialEntries={[`/calculations/${REF}`]}>
                <Routes>
                    <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

function fact(label: string): string {
    const term = screen.getAllByText(label, { selector: "dt" })[0]
    return term.nextElementSibling?.textContent ?? ""
}

describe("a path-search calculation", () => {
    it("shows The path instead of the Result block and the output-geometry cards", async () => {
        page()
        expect(await screen.findByRole("heading", { name: "The path" })).toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Result" })).toBeNull()
        expect(screen.queryByText("Path-search trajectory")).toBeNull()
        const geometries = screen.getByRole("heading", { name: "Geometries" }).closest("section") as HTMLElement
        expect(within(geometries).queryAllByRole("link", { name: /Geometry/ })).toHaveLength(0)
        expect(within(geometries).getByText(/The 3 output geometries are the points listed/)).toBeInTheDocument()
    })

    it("states the facts of the search, with the picked guess and where it sits", async () => {
        page()
        await screen.findByText("Picked guess")
        expect(fact("Method")).toBe("GSM (growing string)")
        expect(fact("Ends")).toBe("double-ended · 2 endpoints")
        expect(fact("Points")).toBe("3 stored of 3")
        expect(fact("Picked guess")).toBe("point 1 · 3.759 along the path")
        expect(fact("Climbing image")).toBe("none (not a NEB)")
        expect(fact("Converged")).toBe("not recorded")
        expect(fact("Level · program")).toContain("xtb 6.3.3 · ARC 1.1.0")
    })

    it("draws the empty-energy picture", async () => {
        page()
        expect(await screen.findByText("kJ/mol relative to point 0: not deposited for this path")).toBeInTheDocument()
        expect(screen.getByText("1 · picked guess")).toBeInTheDocument()
    })

    it("follows the path to the optimisation and what ran on it, each with its own level and values", async () => {
        page()
        const lineage = await screen.findByRole("list", { name: "Calculations that followed from this path search" })
        expect(within(lineage).getByText("optimised from point 1")).toBeInTheDocument()
        expect(within(lineage).getByRole("link", { name: /Fine optimisation/ })).toHaveAttribute("href", "/calculations/calc_opt")
        expect(within(lineage).getByText("b3lyp/def2tzvp · converged in 2 steps · −117.786149 hartree")).toBeInTheDocument()
        expect(await within(lineage).findByText("b3lyp/def2tzvp · 1 imaginary · 1886.43i cm⁻¹")).toBeInTheDocument()
        expect(within(lineage).getByText("b3lyp/def2tzvp · both legs · 92 points")).toBeInTheDocument()
        expect(within(lineage).getByText("GSM · gfn2 · 3 points")).toBeInTheDocument()
    })

    it("says the energy hill on the transition-state pages is at another level and does not hold these points", async () => {
        page()
        const note = await screen.findByText((_, element) => element?.classList.contains("path-lineage-back") === true
            && (element.textContent ?? "").includes("The energy hill on those pages is at b3lyp/def2tzvp; this path was searched at gfn2 and its points are not on that hill."))
        expect(note).toBeInTheDocument()
        expect(within(note).getByRole("link", { name: /Transition state entry/ })).toHaveAttribute("href", "/transition-state-entries/tse_one")
        expect(within(note).getByRole("link", { name: /Reaction version/ })).toHaveAttribute("href", "/reaction-entries/rxe_one")
    })
})
