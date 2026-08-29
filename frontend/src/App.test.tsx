import { StrictMode } from "react"
import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import App from "./App"

const speciesRef = "spc_abcde234567abcde234567abcd"
const speciesRefTwo = "spc_bcdef234567bcdef234567abcde"
const entryRef = "spe_cdefg234567cdefg234567abcd"
const server = setupServer()

function overviewSpecies(ref = speciesRef) {
    return {
        species_ref: ref,
        canonical_smiles: "[OH2]",
        inchi_key: "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
        formula: "H2O",
        charge: 0,
        multiplicity: 1,
        stereo_kind: "achiral",
        entries: [{
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            species_entry_label: "ground electronic state",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: true,
                has_statmech: true,
                has_transport: false,
                has_conformers: true,
                calculation_count: 14,
            },
        }],
    }
}
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("public archive shell", () => {
    it("renders visible keyboard navigation and excludes the admin diagnostic route", async () => {
        const user = userEvent.setup(); render(<App />)
        expect(await screen.findByRole("heading", { name: "TCKDB" })).toBeVisible()
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "Species" })).toHaveFocus()
        expect(screen.queryByText(/Machine-Review Inspection/)).not.toBeInTheDocument()
    })

    it("offers a visible accessible Formula/SMILES choice for ambiguous input", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [{ species_ref: speciesRef, formula: "Cl", canonical_smiles: "[Cl]", charge: 0, multiplicity: 2, entries: [] }],
        })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "Cl")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        // The result reads as chemistry (formula + SMILES), not as the raw
        // ref -- the ref stays present, but demoted, alongside it.
        const result = await screen.findByRole("link", { name: /^Cl \[Cl\]/ })
        expect(result).toBeVisible()
        expect(result).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(screen.getByText(speciesRef)).toBeVisible()
    })

    it("completes a StrictMode search and clears its loading state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [{ species_ref: speciesRef, formula: "H2O", canonical_smiles: "O", charge: 0, multiplicity: 1, entries: [] }],
        })))
        const user = userEvent.setup(); render(<StrictMode><App /></StrictMode>)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        const button = screen.getByRole("button", { name: "Search" })
        await user.click(button)
        expect(await screen.findByRole("link", { name: /^H2O O /})).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("routes spc references to species even when entries are returned", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ records: [overviewSpecies()] })
        )))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), speciesRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "H2O" })).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
        expect(screen.getByRole("link", { name: "ground electronic state" }))
            .toHaveAttribute("href", `/species-entries/${entryRef}`)
    })

    it("routes spe references to the precise entry", async () => {
        const entry = {
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: false,
                has_statmech: false,
                has_transport: false,
                has_conformers: false,
                calculation_count: 0,
            },
        }
        const species = {
            species_ref: speciesRef,
            canonical_smiles: "O",
            inchi_key: "X",
            formula: "O",
            charge: 0,
            multiplicity: 1,
            entries: [entry],
        }
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [species] })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [] })),
        )
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), entryRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "O" })).toBeVisible()
        expect(screen.getByText(entryRef)).toBeVisible()
    })

    it("renders formula search results as species-grain Links and follows one", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            if (query.has("species_ref")) return HttpResponse.json({ records: [overviewSpecies()] })
            return HttpResponse.json({ records: [
                {
                    species_ref: speciesRef, formula: "H2O", canonical_smiles: "O", charge: 0, multiplicity: 1,
                    entries: [{ species_entry_ref: entryRef }],
                },
                {
                    species_ref: speciesRefTwo, formula: "H2O2", canonical_smiles: "OO", charge: 0, multiplicity: 1,
                    entries: [],
                },
            ] })
        }))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        // Each row reads by its own chemistry, not by an interchangeable ref:
        // the two matches share the same formula prefix but diverge past it,
        // and each keeps its own entry count ("1 entry" vs "0 entries").
        const result = await screen.findByRole("link", { name: /^H2O O charge 0 · spin singlet \(1\) · 1 entry$/ })
        expect(result).toHaveAttribute("href", `/species/${speciesRef}`)
        const other = screen.getByRole("link", { name: /^H2O2 OO charge 0 · spin singlet \(1\) · 0 entries$/ })
        expect(other).toBeVisible()
        expect(other).toHaveAttribute("href", `/species/${speciesRefTwo}`)
        // The ref stays present and copyable alongside the chemistry, just demoted.
        expect(screen.getByText(speciesRef)).toBeVisible()
        expect(screen.getByText(speciesRefTwo)).toBeVisible()
        await user.click(result)
        expect(await screen.findByRole("heading", { name: "H2O" })).toBeVisible()
    })

    it("keeps structure search at entry grain and shows SMILES when formula is unavailable", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({
            records: [{ species_ref: speciesRef, species_entry_ref: entryRef, smiles: "CCO", charge: 0, multiplicity: 1 }],
        })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "smiles:CCO")
        await user.click(screen.getByRole("button", { name: "Search" }))
        // The structure-search endpoint never returns a formula (#251): the
        // row leads with SMILES instead and says so honestly, rather than
        // leaving a blank or falling back to the ref.
        expect(screen.getByText("formula not available")).toBeVisible()
        const result = await screen.findByRole("link", { name: /^CCO formula not available/ })
        expect(result).toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(screen.getByText(entryRef)).toBeVisible()
    })

    it("keeps only the latest search result and does not navigate after unmount", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") await delay(40)
            const ref = formula === "H2O" ? speciesRef : speciesRefTwo
            const chemistry = formula === "H2O"
                ? { formula: "H2O", canonical_smiles: "O" }
                : { formula: "H2", canonical_smiles: "[H][H]" }
            return HttpResponse.json({ records: [{ species_ref: ref, ...chemistry, charge: 0, multiplicity: 1, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "H2O"); await user.click(screen.getByRole("button", { name: "Search" }))
        await user.clear(input); await user.type(input, "H2"); await user.click(screen.getByRole("button", { name: "Search" }))
        // The stale, slower "H2O" response must never overwrite the "H2" result.
        expect(await screen.findByRole("link", { name: /^H2 \[H\]\[H\]/ })).toBeVisible()
        expect(screen.queryByRole("link", { name: /^H2O O/ })).not.toBeInTheDocument()
        cleanup(); await delay(60); expect(window.location.pathname).toBe("/")
    })

    it("renders empty, malformed-success, and HTTP-error states with idle busy state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") return HttpResponse.json({ records: [] })
            if (formula === "Ca") return HttpResponse.json({ records: [{ species_ref: speciesRef }] })
            return HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("No exact formula record")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "Ca"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("clears a slow valid request synchronously for invalid and ambiguous submits", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async () => {
            await delay(50)
            return HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "spc_BAD"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("26 lowercase base32")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "Cl"); await user.click(button)
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
        // The ref is checked directly (not via an exact link name) because
        // the row's accessible name is now the chemistry, not the ref --
        // checking for the ref's continued *absence* still needs to survive
        // that, since it stands in for "did the stale slow response render".
        await delay(60); expect(screen.queryByText(speciesRef)).not.toBeInTheDocument()
    })

    it("does not allow an ambiguity chooser to search stale textbox content", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            expect(new URL(request.url).searchParams.get("formula")).toBe("Br")
            return HttpResponse.json({
                records: [{ species_ref: speciesRef, formula: "Br", canonical_smiles: "[Br]", charge: 0, multiplicity: 2, entries: [] }],
            })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "Cl"); await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("button", { name: "Formula" })).toBeVisible()
        await user.clear(input); await user.type(input, "Br")
        expect(screen.queryByRole("button", { name: "Formula" })).not.toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Br” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        const result = await screen.findByRole("link", { name: /^Br \[Br\]/ })
        expect(result).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
    })
})

const publicRoutes: Array<[path: string, heading: string, ref?: string]> = [
    ["/species", "Species", undefined],
    ["/species/spc_abcde234567abcde234567abcd", "H2O", speciesRef],
    ["/conformer-groups/cfg_abc", "Conformer group", "cfg_abc"],
    ["/conformer-observations/cfo_abc", "Computed observation", "cfo_abc"],
    ["/calculations/calc_abc", "Single-point calculation", "calc_abc"],
    ["/geometries/geom_abc", "Geometry", "geom_abc"],
    ["/reactions", "Reactions", undefined],
    ["/reactions/rxn_abc", "Reaction", "rxn_abc"],
    ["/methods", "Methods", undefined],
]

describe.each(publicRoutes)("route shell %s", (path, heading, ref) => {
    it("renders the declared public route deterministically", async () => {
        if (path.startsWith("/species/")) {
            server.use(http.get("/api/v1/scientific/species/search", () => (
                HttpResponse.json({ records: [overviewSpecies()] })
            )))
        }
        if (path.startsWith("/conformer-groups/")) {
            server.use(http.get("/api/v1/scientific/conformer-groups/:ref", () => HttpResponse.json({
                record: {
                    conformer_group: {
                        conformer_group_ref: "cfg_abc", label: "Conformer group",
                        review: { status: "not_reviewed" },
                    },
                    species: { species_ref: speciesRef, species_entry_ref: entryRef },
                    observations_summary: { total: 0, by_scientific_origin: {} },
                    evidence_summary: {
                        calculation_count: 0, optimization_chain_count: 0, geometry_count: 0,
                        evidence_coverage: { opt: 0, freq: 0, sp: 0 },
                    },
                    observations: [], calculations: [], geometries: [],
                },
            })))
        }
        if (path.startsWith("/conformer-observations/")) {
            server.use(http.get("/api/v1/scientific/conformer-observations/:ref", () => HttpResponse.json({
                record: {
                    conformer_observation: {
                        conformer_observation_ref: "cfo_abc",
                        scientific_origin: "computed",
                        review: { status: "not_reviewed" },
                    },
                    conformer_group: {
                        conformer_group_ref: "cfg_abc", label: "Conformer group",
                        review: { status: "not_reviewed" },
                    },
                    species: { species_ref: speciesRef, species_entry_ref: entryRef },
                    assignment_scheme: null,
                    evidence_summary: {
                        calculation_count: 0, geometry_count: 0, has_opt: false, has_freq: false,
                        has_sp: false, has_geometry_validation: false, has_scf_stability: false,
                        levels_of_theory: {},
                    },
                    available_sections: {
                        has_observations: false, has_selections: false, has_calculations: false,
                        has_geometries: false, has_review: false,
                    },
                    observations: [], selections: [], calculations: [], geometries: [], review_history: [],
                },
            })))
        }
        if (path.startsWith("/calculations/")) {
            server.use(http.get("/api/v1/scientific/calculations/:ref", () => HttpResponse.json({
                record: {
                    calculation: {
                        calculation_ref: "calc_abc", type: "sp", quality: "raw",
                        created_at: "2026-07-21T12:06:50.748258",
                        review: { status: "not_reviewed" },
                    },
                    owner: {
                        kind: "species_entry",
                        species_entry: {
                            species_ref: speciesRef, species_entry_ref: entryRef,
                            canonical_smiles: "[OH2]", inchi_key: "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                            charge: 0, multiplicity: 1,
                            species_entry_kind: "minimum", electronic_state_kind: "ground",
                        },
                        transition_state_entry: null,
                    },
                    level_of_theory: null, software_release: null, workflow_tool_release: null, literature: null,
                    provenance: {
                        has_result: false, converged: null,
                        geometry_validation_status: "not_present", scf_stability_status: "not_present",
                        submission_ref: null,
                    },
                    available_sections: {
                        has_results: false, has_dependencies: false, has_parameters: false,
                        has_constraints: false, has_artifacts: false, has_input_geometries: false,
                        has_output_geometries: false, has_geometry_validation: false, has_scf_stability: false,
                        has_wavefunction_diagnostic: false, has_spin_diagnostic: false, has_freq_modes: false,
                        has_hessian: false, has_scan: false, has_irc: false, has_path_search: false,
                        has_execution_environment: false, has_energy_corrections: false,
                    },
                    results: null, dependencies: [], review_history: [],
                    input_geometries: [], output_geometries: [],
                },
            })))
        }
        if (path.startsWith("/geometries/")) {
            server.use(http.get("/api/v1/scientific/geometries/:ref", () => HttpResponse.json({
                geometry_ref: "geom_abc",
                natoms: 0,
                geom_hash: "hash_abc",
                format: "cartesian",
                coordinate_units: "angstrom",
                symbols: [],
                coords: [],
                atoms: [],
                xyz_text: null,
                created_at: "2026-07-21T12:06:50.748258",
                provenance: { produced_by: [], used_as_input_by: [] },
            })))
        }
        window.history.replaceState({}, "", path); render(<App />)
        expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
        if (ref) expect(screen.getByText(ref)).toBeVisible()
        if (path.includes("/species-entries/") && path.split("/").length === 4) {
            expect(screen.getByText(path.split("/").at(-1) ?? "", { selector: "code" })).toBeVisible()
        }
    })
})

it("retains the admin machine-review route outside public navigation", async () => {
    window.history.replaceState({}, "", "/admin/machine-review-inspection")
    render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>)
    expect(await screen.findByRole("heading", { name: "Submission Machine-Review Inspection" })).toBeVisible()
})
