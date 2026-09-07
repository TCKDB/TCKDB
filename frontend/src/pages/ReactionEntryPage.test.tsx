import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ReactionEntryPage from "./ReactionEntryPage"

const ENTRY_REF = "rxe_test1"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page(ref = ENTRY_REF) {
    return render(
        <MemoryRouter initialEntries={[`/reaction-entries/${ref}`]}>
            <Routes>
                <Route path="/reaction-entries/:entryRef" element={<ReactionEntryPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockFull(overrides: Record<string, unknown> = {}) {
    return {
        reaction_entry: {
            reaction_entry_ref: ENTRY_REF,
            reaction_ref: "rxn_test1",
            equation: "O + [CH3] <=> C + [OH]",
            reversible: true,
            family: "H_Abstraction",
            review: { status: "not_reviewed" },
            atom_maps: [],
        },
        review_summary: { total: 7, not_reviewed: 7, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
        species: {
            reactants: [
                { species_entry_ref: "spe_water", smiles: "O", formula: "H2O", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" } },
                { species_entry_ref: "spe_ch3", smiles: "[CH3]", formula: "CH3", stoichiometry: 1, participant_index: 1, review: { status: "not_reviewed" } },
            ],
            products: [
                { species_entry_ref: "spe_ch4", smiles: "C", formula: "CH4", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" } },
                { species_entry_ref: "spe_oh", smiles: "[OH]", formula: "HO", stoichiometry: 1, participant_index: 1, review: { status: "not_reviewed" } },
            ],
        },
        kinetics: [{
            kinetics_ref: "kin_test1",
            scientific_origin: "computed",
            model_kind: "modified_arrhenius",
            review: { status: "not_reviewed" },
            parameters: { A: 3025.44, A_units: "cm3_mol_s", n: 3.11242, Ea_kj_mol: 39.9711 },
            uncertainty: { A_uncertainty: 1.24736, A_uncertainty_kind: "multiplicative", n_uncertainty: 0.0287896, Ea_uncertainty_kj_mol: 0.16464 },
            tunneling_model: "eckart",
            is_third_body: false,
            temperature_coverage: { record_min_k: 300, record_max_k: 3000 },
            evidence_completeness: { score: 7, max: 9, checklist: { source_calculations: true, ts_opt_evidence: true } },
            levels: { geometry: null, frequency: { method: "b3lyp", basis: "def2tzvp" }, energy: { method: "b3lyp", basis: "def2tzvp" }, energy_source: "sp" },
            provenance: {
                transition_state_entry_ref: null,
                ts_opt_calculation_ref: null,
                ts_freq_calculation_ref: "calc_freqlink",
                ts_sp_calculation_ref: "calc_splink",
                primary_level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                primary_software: { software: "Gaussian", version: "16" },
                software_release: { software: "Arkane", version: null },
                workflow_tool_release: { workflow_tool: "ARC", version: "1.1.0" },
                network_kinetics_ref: null,
            },
        }],
        transition_states: [{
            transition_state_ref: "ts_test1",
            transition_state_entry_ref: "tse_test1",
            status: "optimized",
            review: { status: "not_reviewed" },
            evidence_summary: {
                calculation_count: 4, has_opt: true, has_freq: true, has_sp: true, has_irc: true,
                has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
                levels_of_theory: {
                    opt: [{ method: "b3lyp", basis: "def2tzvp" }],
                    freq: [{ method: "b3lyp", basis: "def2tzvp" }],
                    sp: [{ method: "b3lyp", basis: "def2tzvp" }],
                },
            },
            calculations: {
                ts_opt: { calculation_ref: "calc_opt1", type: "opt" },
                ts_freq: { calculation_ref: "calc_freq1", type: "freq" },
                ts_sp: { calculation_ref: "calc_sp1", type: "sp" },
                ts_irc: { calculation_ref: "calc_irc1", type: "irc" },
            },
            dependencies: [
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" },
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_sp1", role: "single_point_on" },
                { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_irc1", role: "irc_start" },
            ],
        }],
        calculations: [
            { calculation_ref: "calc_opt1", calculation_type: "opt", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software: { software: "Gaussian", version: "16" } },
            { calculation_ref: "calc_freq1", calculation_type: "freq", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software: { software: "Gaussian", version: "16" } },
            { calculation_ref: "calc_sp1", calculation_type: "sp", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software: { software: "Gaussian", version: "16" } },
            { calculation_ref: "calc_irc1", calculation_type: "irc", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software: { software: "Gaussian", version: "16" } },
            { calculation_ref: "calc_freqlink", calculation_type: "freq", level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software: { software: "Gaussian", version: "16" } },
            { calculation_ref: "calc_splink", calculation_type: "sp", level_of_theory: null, software: null },
        ],
        networks: [],
        ...overrides,
    }
}

function handleFull(payload: object) {
    server.use(http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/full`, () => HttpResponse.json(payload)))
}

/**
 * Refs that have no structural reason to repeat -- `kin_`/`spe_`/`net_` --
 * must appear EXACTLY once outside the References disclosure. `tse_`/
 * `calc_` refs are excluded from the strict count: the approved design
 * (plan §2's mock, and the brief's own "dependency-graph child nodes link
 * to /calculations/{ref}" + "SpeciesEntryLink per participant" rules)
 * deliberately re-surfaces a calculation ref in BOTH the "Calculations by
 * stage" table AND the dependency graph that draws the same calculations
 * as nodes -- a real, intentional second mention, not a duplication bug.
 * This fixture keeps kin_/spe_/net_ refs collision-free by construction
 * (e.g. the kinetics record's own `ts_*_calculation_ref` links point at
 * DIFFERENT calc refs than the TS entry's own `calculations` map, per the
 * plan §7 "resolver disagreement" case) so the strict count is meaningful
 * for those three prefixes.
 */
function occurrencesOutsideRefs(container: HTMLElement, value: string): number {
    const full = container.textContent ?? ""
    const disclosure = container.querySelector(".refs-disclosure")
    const insideRefs = disclosure?.textContent ?? ""
    return (full.split(value).length - 1) - (insideRefs.split(value).length - 1)
}

describe("ReactionEntryPage -- DOM-vs-payload identity", () => {
    it("every kin_/spe_/net_ ref in the fixture appears exactly once outside References", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")

        for (const ref of ["kin_test1", "spe_water", "spe_ch3", "spe_ch4", "spe_oh"]) {
            expect(occurrencesOutsideRefs(container, ref), `${ref} should appear exactly once outside References`).toBe(1)
        }
    })

    it("every tse_/calc_ ref in the fixture appears at least once outside References", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")

        for (const ref of ["tse_test1", "calc_opt1", "calc_freq1", "calc_sp1", "calc_irc1", "calc_freqlink", "calc_splink"]) {
            expect(occurrencesOutsideRefs(container, ref)).toBeGreaterThanOrEqual(1)
        }
    })

    it("every A/n/Ea/T value from the kinetics record is present, string-equal to the payload", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const text = container.textContent ?? ""
        for (const value of ["3025.44", "3.11242", "39.9711", "300", "3000"]) {
            expect(text).toContain(value)
        }
    })
})

describe("ReactionEntryPage -- three-state network wording", () => {
    it("[] -- the exact archive-honest sentence", async () => {
        handleFull(mockFull({ networks: [] }))
        page()
        expect(await screen.findByText("No pressure-dependent network in this archive admits this reaction entry.")).toBeVisible()
    })

    it("populated -- a list of networks", async () => {
        handleFull(mockFull({
            networks: [{
                network_ref: "net_test1",
                name: "hydrazine",
                solve_temperature_min_k: 300,
                solve_temperature_max_k: 2000,
                solve_pressure_min_bar: 0.01,
                solve_pressure_max_bar: 100,
                channel_count: 21,
                review: { status: "not_reviewed" },
            }],
        }))
        const { container } = page()
        expect(await screen.findByText("net_test1")).toBeVisible()
        expect(screen.getByText("hydrazine")).toBeVisible()
        expect(screen.getByText("21")).toBeVisible()
        // `net_` refs are never cited in this page's References disclosure
        // (plan §2's non-goal: no network page yet, so a network is a plain
        // row, not a related-ref citation) -- exactly one mention.
        expect(occurrencesOutsideRefs(container, "net_test1")).toBe(1)
    })

    it("key absent (pre-deployment API) -- section renders nothing until the fallback resolves", async () => {
        const payload = mockFull()
        delete (payload as Record<string, unknown>).networks
        handleFull(payload)
        server.use(http.get("/api/v1/scientific/networks/search", () => HttpResponse.json({
            records: [{
                network: { network_ref: "net_fallback", name: "hydrazine", solve_temperature_min_k: 300, solve_temperature_max_k: 2000, solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100, review: { status: "not_reviewed" } },
                evidence_summary: { channel_count: 6 },
            }],
        })))
        page()
        await screen.findByText("kin_test1")
        // The fallback fetch resolves, populating the section.
        expect(await screen.findByText("net_fallback")).toBeVisible()
    })
})

describe("ReactionEntryPage -- network-only kinetics sentence", () => {
    it("kinetics: [] and networks populated -- points at the network instead of rendering channel kinetics", async () => {
        handleFull(mockFull({
            kinetics: [],
            networks: [{
                network_ref: "net_only1",
                name: "hydrazine",
                solve_temperature_min_k: 300,
                solve_temperature_max_k: 2000,
                solve_pressure_min_bar: 0.01,
                solve_pressure_max_bar: 100,
                channel_count: 21,
                review: { status: "not_reviewed" },
            }],
        }))
        page()
        expect(await screen.findByText(/No rate coefficient deposited on this entry/)).toBeVisible()
        expect(screen.getByText("net_only1", { selector: "a" })).toBeVisible()
    })

    it("kinetics: [] and no network -- the plain archive-absence sentence, not the network-only one", async () => {
        handleFull(mockFull({ kinetics: [], networks: [] }))
        page()
        expect(await screen.findByText("No rate coefficient has been deposited for this reaction entry.")).toBeVisible()
    })
})

// One kinetics record, parameterised by whether `levels` is served --
// everything else (including both `ts_freq_calculation_ref` and
// `ts_sp_calculation_ref` pointing at calc refs that ARE among the
// top-level `calculations[]` list, so the fallback derivation has real
// evidence to cross-reference) is held fixed between the two variants.
function kineticsRecordFixture(levels: { geometry: null; frequency: { method: string; basis: string }; energy: { method: string; basis: string }; energy_source: string } | undefined) {
    return {
        kinetics_ref: "kin_test1",
        scientific_origin: "computed",
        model_kind: "modified_arrhenius",
        review: { status: "not_reviewed" },
        parameters: { A: 3025.44, A_units: "cm3_mol_s", n: 3.11242, Ea_kj_mol: 39.9711 },
        uncertainty: {},
        tunneling_model: "eckart",
        is_third_body: false,
        temperature_coverage: { record_min_k: 300, record_max_k: 3000 },
        evidence_completeness: { score: 7, max: 9, checklist: {} },
        ...(levels !== undefined ? { levels } : {}),
        provenance: {
            transition_state_entry_ref: null,
            ts_opt_calculation_ref: null,
            ts_freq_calculation_ref: "calc_freq1",
            ts_sp_calculation_ref: "calc_sp1",
            primary_level_of_theory: null,
            primary_software: null,
            software_release: null,
            workflow_tool_release: null,
            network_kinetics_ref: null,
        },
    }
}

describe("ReactionEntryPage -- fallback levels derivation", () => {
    it("equals the server-served levels when both a levels object and enough calculation evidence to derive independently are present", async () => {
        // Same fixture, once WITH `levels` served and once WITHOUT (forcing
        // this page's own `deriveKineticsLevelsFallback` derivation from
        // `provenance.ts_*_calculation_ref` cross-referenced against the
        // top-level `calculations[]` list) -- both must render the same
        // Geometry/Frequencies/Energy facts.
        const servedLevels = { geometry: null, frequency: { method: "b3lyp", basis: "def2tzvp" }, energy: { method: "b3lyp", basis: "def2tzvp" }, energy_source: "sp" }

        handleFull(mockFull({ kinetics: [kineticsRecordFixture(servedLevels)] }))
        const served = page()
        await screen.findByText("kin_test1")
        const servedEnergyRow = served.container.querySelector('section[aria-labelledby="kinetics-heading"] .card > dl.kv-list')!
        const servedText = servedEnergyRow.textContent ?? ""
        served.unmount()
        server.resetHandlers()

        handleFull(mockFull({ kinetics: [kineticsRecordFixture(undefined)] }))
        const derived = page()
        await screen.findByText("kin_test1")
        const derivedEnergyRow = derived.container.querySelector('section[aria-labelledby="kinetics-heading"] .card > dl.kv-list')!
        const derivedText = derivedEnergyRow.textContent ?? ""

        expect(derivedText).toContain("b3lyp/def2tzvp")
        expect(servedText).toContain("b3lyp/def2tzvp")
    })
})

describe("ReactionEntryPage -- transition-state dependency graph", () => {
    it("renders the calculation dependency graph (role=img) when this TS entry's dependencies are non-empty", async () => {
        handleFull(mockFull())
        page()
        expect(await screen.findByRole("img", { name: /Dependency graph for calc_opt1/ })).toBeInTheDocument()
    })

    it("renders the guess/optimized/validated status strip with the served status selected", async () => {
        handleFull(mockFull())
        page()
        await screen.findByText("kin_test1")
        const strip = screen.getByTestId("ts-status-strip")
        const selected = strip.querySelector(".card--selected")
        expect(selected?.textContent).toContain("Optimized")
    })
})
