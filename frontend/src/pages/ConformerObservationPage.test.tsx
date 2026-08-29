import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
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

const siblingCore = {
    conformer_observation_ref: "co_two",
    scientific_origin: "computed",
    note: null,
    created_at: "2026-07-21T12:14:32.845900",
    review: { status: "not_reviewed" },
}

const record = {
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
            conformer_observation: siblingCore,
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
}

describe("ConformerObservationPage", () => {
    it("keeps observation, calculation-row and geometry counts distinct and links provenance", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                "observations", "selections", "calculations", "geometries", "review",
            ])
            return HttpResponse.json({ record })
        }))

        page()
        expect(await screen.findByRole("heading", { name: "co_one" })).toBeVisible()

        // Breadcrumbs link to the owning species entry and conformer group.
        expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeVisible()
        expect(screen.getAllByRole("link", { name: "ground state" })[0]).toHaveAttribute(
            "href", "/species-entries/spe_demo",
        )
        expect(screen.getAllByRole("link", { name: "conformer_1" })[0]).toHaveAttribute(
            "href", "/conformer-groups/cg_demo",
        )

        // Three distinct counts: calculation rows, geometries, sibling observations.
        expect(screen.getByText("Calculation rows")).toBeVisible()
        expect(screen.getByText("Distinct stored geometries")).toBeVisible()
        expect(screen.getByText("Other observations in this basin")).toBeVisible()
        expect(screen.getByText("3")).toBeVisible()
        expect(screen.getByText("2")).toBeVisible()
        expect(screen.getByText("1")).toBeVisible()

        // Levels of theory stay attached to their own stage, not flattened.
        // Each appears twice: once in the by-stage summary, once on its calculation row.
        expect(screen.getAllByText("b3lyp/def2tzvp").length).toBeGreaterThanOrEqual(1)
        expect(screen.getAllByText("wb97xd/def2tzvp").length).toBeGreaterThanOrEqual(1)

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

    it("shows a specific not-found state", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer observation not found" })).toBeVisible()
    })
})
