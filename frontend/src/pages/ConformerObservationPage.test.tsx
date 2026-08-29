import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerObservationPage from "./ConformerObservationPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page() {
    return render(
        <MemoryRouter initialEntries={["/conformer-observations/co_one"]}>
            <Routes>
                <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        conformer_observation: {
            conformer_observation_ref: "co_one",
            scientific_origin: "computed",
            note: "Coarse pre-optimisation basin",
            created_at: "2026-07-21T12:06:50.748258",
            review: { status: "reviewed" },
        },
        conformer_group: {
            conformer_group_ref: "cg_demo",
            label: "conformer_1",
            note: null,
            review: { status: "not_reviewed" },
        },
        species: {
            species_ref: "spc_demo",
            species_entry_ref: "spe_demo",
            species_entry_label: "ground state",
            canonical_smiles: "[CH3]",
        },
        assignment_scheme: null,
        evidence_summary: {
            calculation_count: 3,
            geometry_count: 2,
            has_opt: true,
            has_freq: true,
            has_sp: false,
            has_geometry_validation: true,
            has_scf_stability: false,
            levels_of_theory: {
                opt: [{ level_of_theory_ref: "lot_1", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
                freq: [{ level_of_theory_ref: "lot_2", method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
            },
        },
        available_sections: {
            has_observations: true,
            has_selections: false,
            has_calculations: true,
            has_geometries: true,
            has_review: true,
        },
        observations: [
            {
                conformer_observation: {
                    conformer_observation_ref: "co_one",
                    scientific_origin: "computed",
                    note: null,
                    created_at: "2026-07-21T12:06:50.748258",
                    review: { status: "reviewed" },
                },
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                evidence_summary: {
                    calculation_count: 3, geometry_count: 2, has_opt: true, has_freq: true, has_sp: false,
                    has_geometry_validation: true, has_scf_stability: false, levels_of_theory: {},
                },
                available_sections: {
                    has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                },
            },
            {
                conformer_observation: {
                    conformer_observation_ref: "co_two",
                    scientific_origin: "computed",
                    note: null,
                    created_at: "2026-07-21T12:14:32.845900",
                    review: { status: "not_reviewed" },
                },
                conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                evidence_summary: {
                    calculation_count: 1, geometry_count: 1, has_opt: true, has_freq: false, has_sp: false,
                    has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {},
                },
                available_sections: {
                    has_observations: true, has_selections: false, has_calculations: true, has_geometries: true, has_review: true,
                },
            },
        ],
        calculations: [
            {
                calculation_ref: "calc_opt",
                type: "opt",
                quality: "raw",
                review: { status: "reviewed" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
                workflow_tool_release: { workflow_tool: "ARC" },
            },
            {
                calculation_ref: "calc_freq",
                type: "freq",
                quality: "raw",
                review: { status: "not_reviewed" },
                level_of_theory: { method: "wb97xd", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
            },
        ],
        geometries: [
            { calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } },
        ],
        review_history: [
            { status: "reviewed", reviewed_at: "2026-07-25T00:00:00", note: "Looks consistent" },
        ],
        selections: [],
        ...overrides,
    }
}

describe("ConformerObservationPage", () => {
    it("keeps observation, calculation-row and geometry counts in their own metric", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                "observations", "selections", "calculations", "geometries", "review",
            ])
            return HttpResponse.json({ record: mockRecord() })
        }))

        page()
        expect(await screen.findByRole("heading", { name: "Computed observation" })).toBeVisible()

        // Each count lives in its own metric card — scoping the query to the
        // card catches a swap between "Calculation rows" and "Distinct
        // stored geometries" that a page-wide getByText("3") would miss.
        const calcMetric = screen.getByText("Calculation rows").closest(".metric")
        const geomMetric = screen.getByText("Distinct stored geometries").closest(".metric")
        const siblingMetric = screen.getByText("Other observations in this basin").closest(".metric")
        expect(calcMetric).not.toBeNull()
        expect(geomMetric).not.toBeNull()
        expect(siblingMetric).not.toBeNull()
        expect(within(calcMetric as HTMLElement).getByText("3")).toBeVisible()
        expect(within(geomMetric as HTMLElement).getByText("2")).toBeVisible()
        expect(within(siblingMetric as HTMLElement).getByText("1")).toBeVisible()
    })

    it("pairs each level of theory to its own stage, not a flattened method", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        // Scoped to the by-stage section (not the calculation table, which
        // repeats the same levels per-row) so this can only pass if "opt"
        // and "freq" are each paired with their own <dd>, not merged.
        const stageSection = screen.getByRole("heading", { name: "Levels of theory by stage" }).closest("section")
        expect(stageSection).not.toBeNull()
        const withinStages = within(stageSection as HTMLElement)
        const optRow = withinStages.getByText("opt").closest("div")
        const freqRow = withinStages.getByText("freq").closest("div")
        expect(optRow).not.toBeNull()
        expect(freqRow).not.toBeNull()
        expect(within(optRow as HTMLElement).getByText("b3lyp/def2tzvp")).toBeVisible()
        expect(within(freqRow as HTMLElement).getByText("wb97xd/def2tzvp")).toBeVisible()
        // And the pairing is not merely coincidental adjacency: freq's row
        // must not also contain the opt-stage level.
        expect(within(freqRow as HTMLElement).queryByText("b3lyp/def2tzvp")).not.toBeInTheDocument()
        expect(within(optRow as HTMLElement).queryByText("wb97xd/def2tzvp")).not.toBeInTheDocument()
    })

    it("links breadcrumbs and provenance rows, and surfaces stable public refs", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))

        page()
        await screen.findByRole("heading", { name: "Computed observation" })

        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "Species" }))
            .toHaveAttribute("href", "/species/spc_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Species entry" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")
        expect(within(breadcrumb).getByRole("link", { name: "Conformer basin" }))
            .toHaveAttribute("href", "/conformer-groups/cg_demo")

        // Human-labelled links live in the body, separate from the breadcrumb.
        expect(screen.getByRole("link", { name: "ground state" }))
            .toHaveAttribute("href", "/species-entries/spe_demo")
        expect(screen.getByRole("link", { name: "conformer_1" }))
            .toHaveAttribute("href", "/conformer-groups/cg_demo")

        // Stable public refs stay visible and copyable even when a label exists.
        expect(screen.getByText("co_one", { selector: "dd" })).toBeVisible()
        expect(screen.getByText("cg_demo", { selector: "dd" })).toBeVisible()
        expect(screen.getByText("spc_demo", { selector: "dd" })).toBeVisible()

        // Sibling list excludes this observation itself and links onward.
        expect(screen.getByRole("link", { name: "co_two" })).toHaveAttribute(
            "href", "/conformer-observations/co_two",
        )
        expect(screen.queryByRole("link", { name: "co_one" })).not.toBeInTheDocument()

        // Review-trust layer is separate from the summary metrics.
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.getByText("Looks consistent")).toBeVisible()

        // Geometry links point at the geometry detail route.
        expect(screen.getByRole("link", { name: "geo_one" })).toHaveAttribute("href", "/geometries/geo_one")
    })

    it("reports a check as 'recorded', never as a pass/fail verdict", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord() })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText(/geometry validation recorded/)).toBeVisible()
        expect(screen.getByText(/SCF stability not recorded/)).toBeVisible()
        expect(screen.queryByText(/SCF stability no\b/)).not.toBeInTheDocument()
    })

    it("gives the disclosure a real heading so heading-navigation does not skip it", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: mockRecord({ selections: [{ selection_kind: "lowest_energy" }] }) })
        )))
        page()
        expect(await screen.findByRole("heading", { name: "Curation selections (1)" })).toBeVisible()
    })

    it("shows a specific not-found state for a 404", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer observation not found" })).toBeVisible()
    })

    it("gives a wrong-handle-type 422 its own non-retryable state, distinct from a transient outage", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "handle_type_mismatch",
            detail: "handle_type_mismatch: expected a conformer_observation handle (prefix 'co') but got prefix 'cg'",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/expected a conformer_observation handle/)).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Conformer observation unavailable" })).not.toBeInTheDocument()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state, distinct from a wrong-prefix ref", async () => {
        // `invalid_handle` — right prefix, unparseable body — is distinct
        // from `handle_type_mismatch` above and is what live traffic
        // actually returns for a malformed ref. This pins the shared
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord`
        // on this page too, not just the surface it was changed for.
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'co_' not a recognised conformer_observation handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised conformer_observation handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("treats an absent calculations key as 'not requested', distinct from an empty one", async () => {
        const withoutKey = mockRecord()
        delete (withoutKey as Record<string, unknown>).calculations
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: withoutKey })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText("This section was not requested for this view.")).toBeVisible()
        expect(screen.queryByText("No calculation rows were returned for this observation.")).not.toBeInTheDocument()
    })

    it("renders calculations: null the same as calculations: [] — both are 'requested, empty'", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: null,
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: false, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText("No calculation rows were returned for this observation.")).toBeVisible()
    })

    it("flags a contradiction when the archive marks evidence present but returns none", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [],
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: true, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        expect(screen.getByText(/The archive marks this observation as having recorded evidence here/)).toBeVisible()
    })

    it("renders a single-observation basin with no sibling list, not an error", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    observations: [{
                        conformer_observation: {
                            conformer_observation_ref: "co_one",
                            scientific_origin: "computed",
                            note: null,
                            created_at: "2026-07-21T12:06:50.748258",
                            review: { status: "reviewed" },
                        },
                        conformer_group: { conformer_group_ref: "cg_demo", label: "conformer_1", review: { status: "not_reviewed" } },
                        species: { species_ref: "spc_demo", species_entry_ref: "spe_demo" },
                        evidence_summary: {
                            calculation_count: 3, geometry_count: 2, has_opt: true, has_freq: true, has_sp: false,
                            has_geometry_validation: true, has_scf_stability: false, levels_of_theory: {},
                        },
                        available_sections: {
                            has_observations: true, has_selections: false, has_calculations: true,
                            has_geometries: true, has_review: true,
                        },
                    }],
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { name: "Computed observation" })
        const siblingMetric = screen.getByText("Other observations in this basin").closest(".metric")
        expect(within(siblingMetric as HTMLElement).getByText("0")).toBeVisible()
        expect(screen.getByText("No other deposited observations were returned for this basin.")).toBeVisible()
        expect(screen.queryByRole("link", { name: "co_two" })).not.toBeInTheDocument()
    })
})
