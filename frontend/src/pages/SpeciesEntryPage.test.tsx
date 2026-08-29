import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import App from "../App"

const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const groupRef = "cg_rsoqvj37biuvkucdr6dpaba6iy"
const geometryOne = "geom_qcnisbgb4abax5oxym3dtjxu34"
const geometryTwo = "geom_or52ifyemdi3eewsjym2fuvo3a"
const geometryOneHash = "515c0c32db80802112cc7ae5663102f1384cdbbc9dccf9bd8065fa52b365c49c"
const geometryTwoHash = "753a8648fb0f07db4e39c6ef90a3e71ad8046ca90bd291addfbb6635f0a9488a"
const server = setupServer()

type HandlerOptions = {
    empty?: boolean
    malformed?: boolean
    status?: number
    multiLot?: boolean
    noConformers?: boolean
}

function handlers(options: HandlerOptions = {}) {
    return [
        http.get("/api/v1/scientific/species/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            expect(query.get("species_entry_ref")).toBe(entryRef)
            expect(query.getAll("include")).toEqual(["thermo", "statmech", "transport", "conformers"])
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            if (options.malformed) {
                return HttpResponse.json({ records: [{ species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca" }] })
            }
            if (options.empty) return HttpResponse.json({ records: [] })
            return HttpResponse.json({ records: [{
                species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca",
                canonical_smiles: "[CH3]",
                inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
                formula: "CH3",
                charge: 0,
                multiplicity: 2,
                entries: [{
                    species_entry_ref: entryRef,
                    species_entry_kind: "minimum",
                    electronic_state_kind: "ground",
                    review: { status: "not_reviewed" },
                    availability: {
                        has_thermo: true,
                        has_statmech: true,
                        has_transport: false,
                        has_conformers: true,
                        calculation_count: 14,
                    },
                }],
            }] })
        }),
        http.get("/api/v1/scientific/conformers/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            expect(query.get("species_entry_ref")).toBe(entryRef); expect(query.get("limit")).toBe("50")
            expect(query.getAll("include")).toEqual(["observations", "calculations", "geometries"])
            if (options.status) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.status })
            const levels = [
                { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" },
                ...(options.multiLot ? [{ method: "CCSD(T)", basis: "cc-pVTZ" }] : []),
            ]
            const calculations = Array.from({ length: 14 }, (_, index) => ({
                calculation_ref: `calc_${String(index + 1).padStart(2, "0")}`,
                type: index < 7 ? "opt" : index < 11 ? "freq" : "sp",
                level_of_theory: levels[0],
            }))
            if (options.noConformers) return HttpResponse.json({ records: [] })
            const geometryLinks = [
                ...calculations.slice(0, 4).map((calculation) => ({
                    calculation_ref: calculation.calculation_ref,
                    geometry: { geometry_ref: geometryOne, geom_hash: geometryOneHash, natoms: 4 },
                })),
                ...calculations.slice(4, 7).map((calculation) => ({
                    calculation_ref: calculation.calculation_ref,
                    geometry: { geometry_ref: geometryTwo, geom_hash: geometryTwoHash, natoms: 4 },
                })),
            ]
            return HttpResponse.json({ records: [{
                conformer_group: { conformer_group_ref: groupRef, label: "conformer_1" },
                observations_summary: { total: 4 },
                evidence_summary: {
                    calculation_count: 14,
                    optimization_chain_count: 4,
                    geometry_count: 2,
                    evidence_coverage: { opt: 4, freq: 4, sp: 3 },
                    levels_of_theory: { opt: levels, freq: levels, sp: levels },
                },
                observations: Array.from({ length: 4 }, (_, index) => ({
                    conformer_observation: { conformer_observation_ref: `co_${index + 1}` },
                })),
                calculations,
                geometries: geometryLinks,
            }] })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/thermo`, () => HttpResponse.json({
            species_entry_ref: entryRef,
            review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
            records: [{
                thermo_ref: "thm_smoke_one",
                scientific_origin: "computed",
                model_kind: "nasa",
                review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
                supersession: null,
                h298_kj_mol: 143.9, s298_j_mol_k: 194.4,
                h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
                nasa: {
                    t_low: 10, t_mid: 673.9, t_high: 3000,
                    low_temperature_coefficients: [1, 2, 3, 4, 5, 6, 7],
                    high_temperature_coefficients: [1, 2, 3, 4, 5, 6, 7],
                },
                nasa9: null, wilhoit: null, points: null,
                temperature_coverage: {
                    requested_min_k: null, requested_max_k: null, record_min_k: 10, record_max_k: 3000,
                    covers_requested_range: true, overlap_fraction: null, extrapolation_distance_k: 0,
                },
                evidence_completeness: { score: 6, max: 8, checklist: { has_source_calculations: true } },
                provenance: {
                    primary_calculation: null, level_of_theory: null, software: null,
                    statmech_ref: null, freq_calculation_ref: null, sp_calculation_ref: null,
                },
                group_additivity: null,
            }],
            pagination: { offset: 0, limit: 50, returned: 1, total: 1, post_collapse_total: 1 },
        })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/statmech`, () => HttpResponse.json({
            review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
            records: [{
                statmech: {
                    statmech_ref: "sm_smoke_one", scientific_origin: "computed", statmech_treatment: "rrho",
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
                software_release: null, workflow_tool_release: null, literature: null,
                evidence_summary: {
                    source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
                    has_sp_calculation: true, sp_from_optimization: false, has_rotor_scans: false,
                    torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: true,
                },
                available_sections: {
                    has_source_calculations: true, has_torsions: false, has_electronic_levels: false,
                    has_frequencies: true, has_conformers: true, has_review: true,
                },
            }],
            pagination: { offset: 0, limit: 50, returned: 1, total: 1, post_collapse_total: 1 },
        })),
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

describe("species-entry vertical slice", () => {
    it("renders the CH3 identity and its basin-to-geometry lineage without a made-up name", async () => {
        server.use(...handlers({ multiLot: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        expect(await screen.findByRole("heading", { name: "CH3" })).toBeVisible()
        expect(screen.getByText("[CH3]")).toBeVisible()
        expect(screen.getByRole("link", { name: "spc_atp56uqux2ajao7hvckx7gx7ca" })).toHaveAttribute(
            "href", "/species/spc_atp56uqux2ajao7hvckx7gx7ca",
        )
        expect(screen.getByText("WCYWZMWISLQXQU-UHFFFAOYSA-N")).toBeVisible()
        expect(screen.getByText("0 / doublet (2)")).toBeVisible()
        expect(screen.getByText("Conformers · thermo · statmech")).toBeVisible()
        expect(screen.getByText((_, element) => element?.textContent === "14 rows · 4 chains")).toBeVisible()
        expect(screen.getByText((_, element) => element?.textContent === "opt 4/4 · freq 4/4 · sp 3/4")).toBeVisible()
        expect(screen.getByText((_, element) => element?.textContent === "2")).toBeVisible()
        expect(screen.getByText((_, element) => element?.textContent === "7 output links")).toBeVisible()
        expect(screen.getAllByText("CCSD(T)/cc-pVTZ")).toHaveLength(3)
        expect(screen.getAllByText("b3lyp/def2tzvp")).toHaveLength(3)
        expect(screen.queryByText(/methyl radical/i)).not.toBeInTheDocument()
        expect(screen.getByRole("link", { name: /conformer_1/ })).toHaveAttribute(
            "href", `/conformer-groups/${groupRef}`,
        )
        expect(screen.getByRole("link", { name: /calc_01/ })).toHaveAttribute("href", "/calculations/calc_01")
        expect(screen.getByRole("link", { name: "co_1" })).toHaveAttribute(
            "href", "/conformer-observations/co_1",
        )
        expect(screen.getByRole("link", { name: new RegExp(geometryOne) })).toHaveAttribute(
            "href", `/geometries/${geometryOne}`,
        )
        expect(screen.getByRole("link", { name: new RegExp(geometryTwo) })).toHaveAttribute(
            "href", `/geometries/${geometryTwo}`,
        )
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

    it("states honestly when the entry has no projected conformer basins", async () => {
        server.use(...handlers({ noConformers: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}/conformers`)
        render(<App />)
        expect(await screen.findByText("No conformer basins are projected for this entry.")).toBeVisible()
        expect(screen.getByRole("link", { name: "Conformers" })).toHaveAttribute("aria-current", "page")
    })
})

describe.each([
    ["", "Overview", "From basin to stored geometry"],
    ["conformers", "Conformers", "From basin to stored geometry"],
    ["calculations", "Calculations", "Levels of theory by calculation type"],
    ["thermo", "Thermochemistry", "thm_smoke_one"],
    ["statmech", "Statistical mechanics", "sm_smoke_one"],
    ["transport", "Transport", "No transport records are deposited for this entry. This is the archive's own answer — not a failed request — so nothing further will load if you retry."],
    ["unknown", "Overview", "From basin to stored geometry"],
])("entry section %s", (path, navLabel, content) => {
    it("renders the requested section and marks its navigation item current", async () => {
        server.use(...handlers({ multiLot: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}${path ? `/${path}` : ""}`)
        render(<App />)
        expect(await screen.findByText(content)).toBeVisible()
        expect(screen.getByRole("link", { name: navLabel })).toHaveAttribute("aria-current", "page")
    })
})

describe("overview tab still shows the summary availability cards, unchanged", () => {
    it("links thermo/statmech into their real record sections and states transport plainly unavailable", async () => {
        server.use(...handlers({ multiLot: true }))
        window.history.replaceState({}, "", `/species-entries/${entryRef}`)
        render(<App />)
        await screen.findByText("From basin to stored geometry")
        expect(screen.getAllByRole("link", { name: "View record section" })).toHaveLength(2)
        expect(screen.getByText("Unavailable in this entry")).toBeVisible()
    })
})
