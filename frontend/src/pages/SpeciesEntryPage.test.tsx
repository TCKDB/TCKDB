import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"

// ---------------------------------------------------------------------------
// Two DISTINCT conformer groups, each with its own observations and its own
// non-overlapping calculation/geometry refs -- required by the design brief:
// a fixture with only one conformer can prove that the selector renders
// SOMETHING, never that it renders the SELECTED conformer's own evidence.
// Group "conformer_1" additionally carries two observations (one with no SP
// calculation, mirroring the live CH3 entry's fourth observation) so the
// "1 / 4 / many" conformer-count story and the "multiple deposits stay
// multiple" rule both have real fixture coverage.
// ---------------------------------------------------------------------------

const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const groupOneRef = "cg_one"
const groupTwoRef = "cg_two"
const lot = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }
const server = setupServer()

type HandlerOptions = {
    empty?: boolean
    malformed?: boolean
    status?: number
    noConformers?: boolean
}

function conformerRecords() {
    return [
        {
            conformer_group: { conformer_group_ref: groupOneRef, label: "conformer_1" },
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 6,
                optimization_chain_count: 2,
                geometry_count: 2,
                evidence_coverage: { opt: 3, freq: 2, sp: 1 },
                levels_of_theory: { opt: [lot], freq: [lot], sp: [lot] },
            },
            observations: [
                {
                    conformer_observation: { conformer_observation_ref: "co_1" },
                    calculations: [
                        { calculation_ref: "calc_opt_1a", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_opt_1b", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_freq_1", type: "freq", level_of_theory: lot },
                        {
                            calculation_ref: "calc_sp_1", type: "sp", level_of_theory: lot,
                            software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                            workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "1.1.0" },
                        },
                    ],
                },
                {
                    // No SP calculation on this observation -- mirrors CH3's
                    // live fourth observation (sp coverage 3/4, not 4/4).
                    conformer_observation: { conformer_observation_ref: "co_2" },
                    calculations: [
                        { calculation_ref: "calc_opt_2", type: "opt", level_of_theory: lot },
                        { calculation_ref: "calc_freq_2", type: "freq", level_of_theory: lot },
                    ],
                },
            ],
            calculations: [
                { calculation_ref: "calc_opt_1a", type: "opt" }, { calculation_ref: "calc_opt_1b", type: "opt" },
                { calculation_ref: "calc_freq_1", type: "freq" }, { calculation_ref: "calc_sp_1", type: "sp" },
                { calculation_ref: "calc_opt_2", type: "opt" }, { calculation_ref: "calc_freq_2", type: "freq" },
            ],
            geometries: [
                { calculation_ref: "calc_opt_1a", geometry: { geometry_ref: "geom_g1", geom_hash: "hashg1000000", natoms: 4, role: "final" } },
                { calculation_ref: "calc_opt_1b", geometry: { geometry_ref: "geom_g1", geom_hash: "hashg1000000", natoms: 4, role: "final" } },
                { calculation_ref: "calc_opt_2", geometry: { geometry_ref: "geom_g2", geom_hash: "hashg2000000", natoms: 4, role: "final" } },
            ],
        },
        {
            conformer_group: { conformer_group_ref: groupTwoRef, label: "conformer_2" },
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 3,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 1, sp: 1 },
                levels_of_theory: { opt: [lot], freq: [lot], sp: [lot] },
            },
            observations: [{
                conformer_observation: { conformer_observation_ref: "co_3" },
                calculations: [
                    { calculation_ref: "calc_opt_3", type: "opt", level_of_theory: lot },
                    { calculation_ref: "calc_freq_3", type: "freq", level_of_theory: lot },
                    { calculation_ref: "calc_sp_3", type: "sp", level_of_theory: lot },
                ],
            }],
            calculations: [
                { calculation_ref: "calc_opt_3", type: "opt" }, { calculation_ref: "calc_freq_3", type: "freq" },
                { calculation_ref: "calc_sp_3", type: "sp" },
            ],
            geometries: [
                { calculation_ref: "calc_opt_3", geometry: { geometry_ref: "geom_g3", geom_hash: "hashg3000000", natoms: 4, role: "final" } },
            ],
        },
    ]
}

function thermoRecords() {
    return [
        {
            thermo_ref: "thm_pop_a",
            scientific_origin: "computed", model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 143.9, s298_j_mol_k: 194.4, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
            nasa: null, nasa9: null, wilhoit: null, points: null, temperature_coverage: null,
            evidence_completeness: { score: 6, max: 8, checklist: { has_source_calculations: true } },
            provenance: {
                primary_calculation: {
                    calculation_ref: "calc_sp_1",
                    calculation_type: "sp",
                    converged: null,
                    geometry_validation_status: "not_present",
                    scf_stability_status: "not_present",
                    level_of_theory: lot,
                    software: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                },
                level_of_theory: lot,
                software: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16" },
                statmech_ref: "sm_1",
                freq_calculation_ref: "calc_freq_1",
                sp_calculation_ref: "calc_sp_1",
            },
            group_additivity: null,
        },
        {
            thermo_ref: "thm_pop_b",
            scientific_origin: "computed", model_kind: "nasa",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            supersession: null,
            h298_kj_mol: 192.4, s298_j_mol_k: 380.3, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
            nasa: null, nasa9: null, wilhoit: null, points: null, temperature_coverage: null,
            evidence_completeness: { score: 6, max: 8, checklist: { has_source_calculations: false } },
            // Population B: produced by a thermo tool directly, no opt/freq/sp
            // chain this client can trace to one observation.
            provenance: {
                primary_calculation: null,
                level_of_theory: null,
                software: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.1.0" },
                statmech_ref: null,
                freq_calculation_ref: null,
                sp_calculation_ref: null,
            },
            group_additivity: null,
        },
    ]
}

function statmechRecord(ref: string, withConformers: boolean, conformers: Array<{ conformer_group_ref: string; label: string }>) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho",
            rigid_rotor_kind: "asymmetric_top", point_group: "D3h", external_symmetry: 6, is_linear: false,
            uses_projected_frequencies: null, optical_isomers: 1, rotational_constant_a_cm1: null,
            rotational_constant_b_cm1: null, rotational_constant_c_cm1: null,
            frequency_scale_factor_value: 0.999, note: null, created_at: "2026-07-29T08:26:29.315550",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        supersession: null,
        species: {
            species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca", species_entry_ref: entryRef,
            species_entry_label: null, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
            charge: 0, multiplicity: 2,
        },
        transition_state: null,
        frequency_scale_factor: null,
        software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: "1.1.0" },
        workflow_tool_release: null, literature: null,
        evidence_summary: {
            source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
            has_sp_calculation: true, sp_from_optimization: false, has_rotor_scans: false,
            torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: true,
        },
        available_sections: {
            has_source_calculations: true, has_torsions: false, has_electronic_levels: false,
            has_frequencies: true, has_conformers: true, has_review: true,
        },
        ...(withConformers ? { conformers } : {}),
    }
}

function handlers(options: HandlerOptions = {}) {
    return [
        http.get("/api/v1/scientific/species/search", () => {
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            if (options.malformed) return HttpResponse.json({ records: [{ species_ref: "spc_bad" }] })
            if (options.empty) return HttpResponse.json({ records: [] })
            return HttpResponse.json({ records: [{
                species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca",
                canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
                charge: 0, multiplicity: 2,
                entries: [{
                    species_entry_ref: entryRef, species_entry_kind: "minimum", electronic_state_kind: "ground",
                    review: { status: "not_reviewed" },
                    availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: 9 },
                }],
            }] })
        }),
        http.get("/api/v1/scientific/conformers/search", () => {
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            if (options.noConformers) return HttpResponse.json({ records: [] })
            return HttpResponse.json({ records: conformerRecords() })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/thermo`, () => HttpResponse.json({
            species_entry_ref: entryRef,
            review_summary: { approved: 0, under_review: 0, not_reviewed: 2, deprecated: 0, rejected: 0, total: 2 },
            records: thermoRecords(),
            pagination: { offset: 0, limit: 50, returned: 2, total: 2, post_collapse_total: 2 },
        })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/statmech`, ({ request }) => {
            const includesConformers = new URL(request.url).searchParams.getAll("include").includes("conformers")
            return HttpResponse.json({
                review_summary: { approved: 0, under_review: 0, not_reviewed: 2, deprecated: 0, rejected: 0, total: 2 },
                records: [
                    statmechRecord("sm_1", includesConformers, [{ conformer_group_ref: groupOneRef, label: "conformer_1" }]),
                    statmechRecord("sm_2", includesConformers, []),
                ],
                pagination: { offset: 0, limit: 50, returned: 2, total: 2, post_collapse_total: 2 },
            })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/transport`, () => HttpResponse.json({
            review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
            records: [],
            pagination: { offset: 0, limit: 50, returned: 0, total: 0, post_collapse_total: 0 },
        })),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("species-entry page: identity and errors", () => {
    it("renders the CH3 identity without the removed availability card grid", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(await screen.findByRole("heading", { name: "CH3" })).toBeVisible()
        expect(screen.getByText("[CH3]")).toBeVisible()
        expect(screen.getByRole("link", { name: "spc_atp56uqux2ajao7hvckx7gx7ca" })).toHaveAttribute(
            "href", "/species/spc_atp56uqux2ajao7hvckx7gx7ca",
        )
        expect(screen.getByText("Conformers · thermo · statmech")).toBeVisible()
        // The availability card grid ("Available in this entry" / "View
        // record section") is gone -- plain navigation replaced it.
        expect(screen.queryByText("Available in this entry")).not.toBeInTheDocument()
        expect(screen.queryByText("Unavailable in this entry")).not.toBeInTheDocument()
        expect(screen.queryByRole("link", { name: "View record section" })).not.toBeInTheDocument()
    })

    it("distinguishes empty, malformed-success, HTTP error, and loading states", async () => {
        server.use(...handlers({ empty: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(screen.getByRole("heading", { name: "Loading species entry" })).toBeVisible()
        expect(await screen.findByRole("heading", { name: "Entry not found" })).toBeVisible()
        cleanup()
        server.use(...handlers({ malformed: true }))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Entry data could not be read")
        cleanup()
        server.use(...handlers({ status: 503 }))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Entry unavailable")
    })
})

describe("species-entry page: conformer picker", () => {
    it("shows one basin card per conformer and defaults to selecting the first", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        const conformerOne = screen.getByRole("button", { name: /conformer_1/ })
        const conformerTwo = screen.getByRole("button", { name: /conformer_2/ })
        expect(conformerOne).toHaveAttribute("aria-pressed", "true")
        expect(conformerTwo).toHaveAttribute("aria-pressed", "false")
        // Counts stay distinct: observations, calculation rows, and coverage
        // are three different numbers for group one, never conflated.
        expect(within(conformerOne).getByText("2 observations · 6 calculation rows")).toBeVisible()
        expect(within(conformerOne).getByText("opt 3/2 · freq 2/2 · sp 1/2")).toBeVisible()
        expect(within(conformerTwo).getByText("1 observation · 3 calculation rows")).toBeVisible()
        // The URL becomes addressable for the default selection (reload survives it).
        // Group one's three calculation outputs (two opt calcs sharing one
        // geometry, plus a third opt calc with its own) collapse to two
        // DISTINCT stored geometries -- output-link count and distinct-object
        // count are different numbers and must not be conflated.
        await screen.findByText((_, element) => element?.textContent === "2 distinct stored geometries from 3 calculation outputs for conformer_1.")
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupOneRef)
    })

    it("states honestly when the entry has no projected conformer basins, while entry-level tabs still work", async () => {
        const user = userEvent.setup()
        server.use(...handlers({ noConformers: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(await screen.findByText("No conformer basins are projected for this entry.")).toBeVisible()
        expect(screen.getByText("No conformer basins are projected for this entry, so there is no geometry evidence to show.")).toBeVisible()
        // Thermo/statmech/transport are entry-scoped lists, independent of
        // whether any conformer basin is projected -- they still render.
        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        expect(await screen.findByText("thm_pop_a")).toBeVisible()
        expect(screen.getByText("thm_pop_b")).toBeVisible()
    })
})

describe("species-entry page: tabs are a real, keyboard-operable ARIA tablist", () => {
    it("has tablist/tab/tabpanel roles wired together, and ArrowRight moves focus without stealing selection", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        const tablist = screen.getByRole("tablist", { name: "Conformer evidence" })
        const geometryTab = within(tablist).getByRole("tab", { name: "Geometry" })
        const spTab = within(tablist).getByRole("tab", { name: "Single-point energy" })
        expect(geometryTab).toHaveAttribute("aria-selected", "true")
        expect(geometryTab).toHaveAttribute("tabIndex", "0")
        expect(spTab).toHaveAttribute("tabIndex", "-1")
        expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", geometryTab.id)

        geometryTab.focus()
        await user.keyboard("{ArrowRight}")
        expect(document.activeElement).toBe(spTab)
        // Focus moved; selection (the active tab/panel) did not change yet --
        // manual-activation pattern.
        expect(geometryTab).toHaveAttribute("aria-selected", "true")
    })

    it("never renders one tab's content on another tab (thermo and statmech panels stay distinct)", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")

        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        expect(await within(screen.getByRole("tabpanel")).findByText("thm_pop_a")).toBeVisible()
        // Only one tabpanel is ever mounted at a time -- the statmech
        // record's own heading text must not appear on the thermo panel.
        expect(within(screen.getByRole("tabpanel")).queryByText("Statistical mechanics")).not.toBeInTheDocument()

        await user.click(screen.getByRole("tab", { name: "Statistical mechanics" }))
        // "sm_1" also appears (not visibly) inside the pre-existing
        // "Conformer context" disclosure this same statmech list already
        // renders -- match on the visible record card's own `<code>` ref.
        const smRefs = await within(screen.getByRole("tabpanel")).findAllByText("sm_1")
        expect(smRefs.some((node) => node.tagName === "CODE")).toBe(true)
        expect(within(screen.getByRole("tabpanel")).queryByText("thm_pop_a")).not.toBeInTheDocument()
        expect(within(screen.getByRole("tabpanel")).queryByText("Thermochemistry")).not.toBeInTheDocument()
    })

    it("shows the live transport empty state, not a hypothetical placeholder", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        await user.click(screen.getByRole("tab", { name: "Transport" }))
        expect(await screen.findByText(
            "No transport records are deposited for this entry. This is the archive's own answer — not a failed request — so nothing further will load if you retry.",
        )).toBeVisible()
    })
})

describe("species-entry page: selecting a conformer scopes geometry, single-point, thermo and statmech to it", () => {
    it("switches Geometry/SP tab content to the newly selected conformer's own evidence, and the choice survives via the URL", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("Choose a conformer")
        expect(await screen.findByRole("link", { name: "geom_g1" })).toBeVisible()
        expect(screen.queryByText("geom_g3")).not.toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: /conformer_2/ }))
        expect(await screen.findByRole("link", { name: "geom_g3" })).toBeVisible()
        expect(screen.queryByText("geom_g1")).not.toBeInTheDocument()
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupTwoRef)

        await user.click(screen.getByRole("tab", { name: "Single-point energy" }))
        expect(await screen.findByRole("link", { name: "calc_sp_3" })).toBeVisible()
        // The conformer choice is still the query param after a tab switch.
        expect(new URLSearchParams(window.location.search).get("conformer")).toBe(groupTwoRef)
    })

    it("lists every observation on the Single-point tab, including one with no deposited SP calculation", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/sp`)
        render(<App />)
        expect(await screen.findByRole("link", { name: "co_1" })).toBeVisible()
        expect(screen.getByRole("link", { name: "co_2" })).toBeVisible()
        expect(screen.getByText("No single-point calculation recorded for this observation.")).toBeVisible()
    })

    it("renders population A thermo under the selected conformer, and population B as entry-level -- never as an error", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/thermo`)
        render(<App />)
        expect(await screen.findByText("thm_pop_a")).toBeVisible()
        expect(screen.getByText("thm_pop_b")).toBeVisible()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
        const groupHeadings = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent)
        expect(groupHeadings).toContain("From conformer_1")
        expect(groupHeadings).toContain("Entry-level")
    })

    it("renders statmech's real conformer link the same way, once it resolves", async () => {
        server.use(...handlers())
        window.history.replaceState({}, "", `/species-entries/${entryRef}/statmech`)
        render(<App />)
        // "sm_1"/"sm_2" also appear (not visibly) inside the pre-existing
        // "Conformer context" disclosure this list already renders --
        // match on the visible record cards' own `<code>` refs.
        const smOneRefs = await screen.findAllByText("sm_1")
        expect(smOneRefs.some((node) => node.tagName === "CODE")).toBe(true)
        const smTwoRefs = screen.getAllByText("sm_2")
        expect(smTwoRefs.some((node) => node.tagName === "CODE")).toBe(true)
        const groupHeadings = screen.getAllByRole("heading", { level: 3 })
            .filter((node) => node.className === "conformer-evidence-group-heading")
            .map((node) => node.textContent)
        expect(groupHeadings).toEqual(["From conformer_1", "Entry-level"])
    })
})
