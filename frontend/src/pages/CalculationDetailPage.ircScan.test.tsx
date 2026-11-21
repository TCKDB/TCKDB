import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AuthProvider } from "../components/AuthProvider"
import CalculationDetailPage from "./CalculationDetailPage"

const B3LYP = { level_of_theory_ref: "lot_b3lyp", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }
const RESULT_NULLS = { sp: null, opt: null, freq: null, scan: null, irc: null, path_search: null, composite: null }

function record(ref: string, type: "irc" | "scan", results: Record<string, unknown>) {
    return {
        calculation: { calculation_ref: ref, type, quality: "raw", created_at: "2026-11-09T20:25:23", review: { status: "not_reviewed" } },
        owner: {
            kind: "transition_state_entry",
            transition_state_entry: { transition_state_ref: "ts_one", transition_state_entry_ref: "tse_one", charge: 0, multiplicity: 3, status: "optimized", reaction_entry_ref: null },
        },
        level_of_theory: B3LYP,
        software_release: { software_release_ref: "srel_g16", software: "Gaussian", version: "16" },
        provenance: { has_result: true, geometry_validation_status: "not_present", scf_stability_status: "not_present" },
        available_sections: {
            has_results: true, has_dependencies: false, has_parameters: false, has_constraints: false, has_artifacts: false,
            has_input_geometries: false, has_output_geometries: true, has_geometry_validation: false, has_scf_stability: false,
            has_wavefunction_diagnostic: false, has_spin_diagnostic: false, has_freq_modes: false, has_hessian: false,
            has_scan: type === "scan", has_irc: type === "irc", has_path_search: false, has_execution_environment: false, has_energy_corrections: false,
            scan_applicable: type === "scan", irc_applicable: type === "irc",
        },
        results: { kind: type, ...RESULT_NULLS, ...results },
        dependencies: [],
        input_geometries: [],
        output_geometries: [0, 1, 2].map((index) => ({ geometry_ref: `geom_${index}`, output_order: index + 1, role: type === "irc" ? "irc_forward" : "scan_point", natoms: 9 })),
        review_history: [{ status: "not_reviewed", note: null, reviewed_at: null }],
    }
}

const IRC = {
    irc: { direction: "both", zero_energy_reference_hartree: -117.786148505, forward_point_count: 1, reverse_point_count: 1, ts_point_count: 1, point_count: 3 },
    points: [
        { point_index: 0, direction: "reverse", is_ts: false, reaction_coordinate: -1.5, electronic_energy_hartree: -117.84, relative_energy_kj_mol: -150.2, geometry_ref: "geom_0" },
        { point_index: 1, direction: "forward", is_ts: false, reaction_coordinate: 1.5, electronic_energy_hartree: -117.85, relative_energy_kj_mol: -170.4, geometry_ref: "geom_1" },
        { point_index: 2, direction: null, is_ts: true, reaction_coordinate: 0, electronic_energy_hartree: -117.786148505, relative_energy_kj_mol: 0, geometry_ref: "geom_2" },
    ],
    pagination: { returned: 3, total: 3 },
    next_cursor: null,
}

const SCAN = {
    scan: { dimension: 1, is_relaxed: true, zero_energy_reference_hartree: -117.3, coordinate_count: 1, point_count: 3 },
    coordinates: [{ coordinate_index: 1, coordinate_kind: "dihedral", atom_indices: [4, 1, 2, 3], start_value: 180, end_value: 196, step_count: 3, value_unit: "degree" }],
    points: [1, 2, 3].map((index) => ({
        point_index: index, electronic_energy_hartree: [-117.3, -117.2995, -117.298][index - 1], relative_energy_kj_mol: [0, 1.3, 5.2][index - 1], geometry_ref: `geom_${index}`,
        coordinate_values: [{ coordinate_index: 1, coordinate_value: 172 + index * 8, value_unit: "degree" }],
    })),
    pagination: { returned: 3, total: 3 },
    next_cursor: null,
}

const server = setupServer(
    http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
    http.get("/api/v1/scientific/calculations/calc_irc/irc", () => HttpResponse.json(IRC)),
    http.get("/api/v1/scientific/calculations/calc_irc", () => HttpResponse.json({ record: record("calc_irc", "irc", { irc: { direction: "both", has_forward: true, has_reverse: true } }) })),
    http.get("/api/v1/scientific/calculations/calc_scan/scan", () => HttpResponse.json(SCAN)),
    http.get("/api/v1/scientific/calculations/calc_scan", () => HttpResponse.json({ record: record("calc_scan", "scan", { scan: { dimension: 1, is_relaxed: true } }) })),
    http.get("/api/v1/*", () => HttpResponse.json({ detail: "not in this test" }, { status: 404 })),
)
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => cleanup())
afterAll(() => server.close())

function page(ref: string) {
    return render(
        <AuthProvider>
            <MemoryRouter initialEntries={[`/calculations/${ref}`]}>
                <Routes>
                    <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

function fact(label: string): string {
    return screen.getAllByText(label, { selector: "dt" })[0].nextElementSibling?.textContent ?? ""
}

function expectNoCardWall() {
    const geometries = screen.getByRole("heading", { name: "Geometries" }).closest("section") as HTMLElement
    expect(within(geometries).queryAllByRole("link", { name: /Geometry/ })).toHaveLength(0)
    expect(within(geometries).getByText(/The 3 output geometries are the points listed/)).toBeInTheDocument()
}

describe("an IRC calculation", () => {
    it("shows The path with its legs and saddle point instead of the Result block and the card wall", async () => {
        page("calc_irc")
        expect(await screen.findByRole("heading", { name: "The path" })).toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Result" })).toBeNull()
        expect(screen.queryByText("IRC trajectory")).toBeNull()
        expectNoCardWall()
        await screen.findByText("Saddle point", { selector: "dt" })
        expect(fact("Direction")).toBe("both legs")
        expect(fact("Points")).toBe("3 stored · 1 forward · 1 reverse")
        expect(fact("Saddle point")).toBe("point 2 · reaction coordinate 0.000")
    })

    it("draws energy relative to the saddle point along the coordinate as reported, opening on the saddle point", async () => {
        page("calc_irc")
        expect(await screen.findByText("kJ/mol relative to the saddle point")).toBeInTheDocument()
        expect(screen.getByText("reaction coordinate, as the program reported it")).toBeInTheDocument()
        expect(screen.getByText("saddle point · 0.0")).toBeInTheDocument()
        expect(screen.getByText("reverse end · −150.2")).toBeInTheDocument()
        expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", "Point 2 (saddle point): reaction coordinate 0.000, 0.0 kJ/mol")
    })
})

describe("a scan calculation", () => {
    it("shows The scan along its coordinate in the stored unit", async () => {
        page("calc_scan")
        expect(await screen.findByRole("heading", { name: "The scan" })).toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Result" })).toBeNull()
        expectNoCardWall()
        await screen.findByText("Coordinate", { selector: "dt" })
        expect(fact("Coordinate")).toBe("dihedral 4-1-2-3 · 180.0 to 196.0 degrees · 3 steps")
        expect(fact("Relaxed")).toBe("yes")
        expect(screen.getByText("dihedral 4-1-2-3, degrees")).toBeInTheDocument()
        expect(screen.getByText("kJ/mol relative to point 1")).toBeInTheDocument()
    })
})
