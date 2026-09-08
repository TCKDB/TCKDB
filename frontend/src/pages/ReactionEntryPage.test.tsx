import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
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
            evidence_completeness: {
                score: 7, max: 9,
                checklist: {
                    has_source_calculations: true,
                    has_transition_state_entry: true,
                    has_ts_opt_evidence: true,
                    has_ts_freq_evidence: true,
                    has_ts_sp_evidence: true,
                    has_path_search_or_irc_evidence: true,
                    has_uncertainty: true,
                    has_geometry_validation: false,
                    has_scf_stability: false,
                },
            },
            levels: { geometry: { method: "b3lyp", basis: "def2tzvp" }, frequency: { method: "b3lyp", basis: "def2tzvp" }, energy: { method: "b3lyp", basis: "def2tzvp" }, energy_source: "sp" },
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
 * Refs that have no structural reason to repeat -- `spe_`/`net_` -- must
 * appear EXACTLY once outside the References disclosure. `tse_`/`calc_`/
 * `kin_` refs are excluded from the strict count: the approved design
 * (plan §2's mock, and the brief's own "dependency-graph child nodes link
 * to /calculations/{ref}" + "SpeciesEntryLink per participant" rules)
 * deliberately re-surfaces a calculation ref in BOTH the "Calculations by
 * stage" table AND the dependency graph that draws the same calculations
 * as nodes -- a real, intentional second mention, not a duplication bug.
 * `kin_` joined this carve-out in PR 3 (`ArrheniusChart.tsx`): the same
 * kinetics ref legitimately re-surfaces in the chart's own legend chip and
 * its k(T)-table `Disclosure` heading, alongside the record card's own
 * "Kinetics ref" fact -- the SAME "intentional second mention naming what
 * it draws" shape as the calc-ref carve-out, not a duplication bug either.
 * (Both `ArrheniusChart.tsx` nodes fold the ref into a single combined text
 * run -- "series N — kin_…", "k(T) table — kin_…" -- rather than a bare
 * `<code>{ref}</code>` leaf, precisely so `findByText(ref)`'s EXACT-match
 * uniqueness assumption below still resolves to the one card fact; the
 * substring-based count here still (correctly) sees all three mentions.)
 * This fixture keeps spe_/net_ refs collision-free by construction so the
 * strict count is meaningful for those two prefixes.
 */
function occurrencesOutsideRefs(container: HTMLElement, value: string): number {
    const full = container.textContent ?? ""
    const disclosure = container.querySelector(".refs-disclosure")
    const insideRefs = disclosure?.textContent ?? ""
    return (full.split(value).length - 1) - (insideRefs.split(value).length - 1)
}

describe("ReactionEntryPage -- DOM-vs-payload identity", () => {
    it("every spe_/net_ ref in the fixture appears exactly once outside References", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")

        for (const ref of ["spe_water", "spe_ch3", "spe_ch4", "spe_oh"]) {
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

    // Review finding: `toBeGreaterThanOrEqual(1)` here verifies nothing that
    // `findByText("kin_test1")` two lines above didn't already establish --
    // `kin_test1` (unlike tse_/calc_, whose multiplicities genuinely vary
    // with how many stages/dependency edges a fixture happens to carry) has
    // an EXACT, known count on this fixture: the kinetics card's own
    // "Kinetics ref" fact, the Arrhenius chart's legend chip
    // ("series 1 — kin_test1"), and the k(T) table's Disclosure heading
    // ("k(T) table — kin_test1") -- three sites, never more, never fewer.
    it("kin_test1 appears exactly 3 times outside References: the card fact, the chart legend chip, and the k(T) table heading", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(occurrencesOutsideRefs(container, "kin_test1")).toBe(3)
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

// Review finding (blocking #3): the ONE call site wiring `ArrheniusChart`
// into `ReactionKineticsSection.tsx` had no page-level guard -- reverting
// that file to `main` (which never called the component at all) removed
// the chart from the page entirely and this whole test file stayed
// 25/25 green, because nothing here ever asked the page itself for the
// Arrhenius SVG.
describe("ReactionEntryPage -- the Arrhenius chart actually renders on the page", () => {
    it("the kinetics section contains an Arrhenius plot SVG for a plottable record", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const kineticsSection = container.querySelector('section[aria-labelledby="kinetics-heading"]')!
        const svg = within(kineticsSection as HTMLElement).getByRole("img", { name: /Arrhenius plot/ })
        expect(svg).toBeInTheDocument()
        expect(svg.querySelector("polyline")).not.toBeNull()
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

        // `/networks/:ref` is a site 404 today (no network page yet, plan
        // §1's own non-goal) -- the ref renders in the data face with a
        // copy button, NOT as a link to a page that doesn't exist.
        expect(screen.queryByRole("link", { name: "net_test1" })).not.toBeInTheDocument()
        const refCell = screen.getByText("net_test1").closest("td")!
        expect(refCell.querySelector(".copy-button")).not.toBeNull()
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

// One kinetics record, parameterised by the served `levels` value (or its
// absence, forcing `deriveKineticsLevelsFallback`). `ts_freq_calculation_ref`/
// `ts_sp_calculation_ref` point at `calc_freq1`/`calc_sp1` -- the SAME two
// calc refs `mockFull()`'s default `transition_states[0]` cites via its own
// `freq_on`/`single_point_on` dependency edges (parent `calc_opt1`), so the
// fallback derivation's dependency-edge walk has real, matching evidence to
// resolve `geometry` from -- `ts_opt_calculation_ref: null` is the REAL
// shape every live record now has (PR 398's post-review commit: no kinetics
// role ever accepts an opt-typed citation directly).
function kineticsRecordFixture(levels: Record<string, unknown> | undefined) {
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

function productLevelsBlockText(container: HTMLElement): string {
    return (container.querySelector('section[aria-labelledby="kinetics-heading"] .reaction-product-levels') as HTMLElement).textContent ?? ""
}

describe("ReactionEntryPage -- fallback levels derivation", () => {
    it("equals the server-served levels, EXACTLY (not merely 'contains'), when both a levels object and enough calculation evidence to derive independently are present", async () => {
        // The served `levels` here is exactly what a correct
        // `deriveKineticsLevelsFallback` should ALSO compute from this
        // fixture's own calc refs: geometry via the freq_on(calc_opt1 ->
        // calc_freq1) dependency edge, frequency/energy from calc_freq1/
        // calc_sp1's own served levels (both b3lyp/def2tzvp per `mockFull()`'s
        // default `calculations[]`). Gutting the fallback derivation (e.g.
        // returning all-null) changes ONLY the second render, breaking this
        // equality -- a plain `.toContain("b3lyp/def2tzvp")` on each side
        // independently would NOT have caught that (each side still
        // contains that substring somewhere else on the page).
        const servedLevels = {
            geometry: { method: "b3lyp", basis: "def2tzvp" },
            frequency: { method: "b3lyp", basis: "def2tzvp" },
            energy: { method: "b3lyp", basis: "def2tzvp" },
            energy_source: "sp",
        }

        handleFull(mockFull({ kinetics: [kineticsRecordFixture(servedLevels)] }))
        const served = page()
        await screen.findByText("kin_test1")
        const servedText = productLevelsBlockText(served.container)
        expect(servedText).toContain("b3lyp/def2tzvp")
        served.unmount()
        server.resetHandlers()

        handleFull(mockFull({ kinetics: [kineticsRecordFixture(undefined)] }))
        const derived = page()
        await screen.findByText("kin_test1")
        const derivedText = productLevelsBlockText(derived.container)

        expect(derivedText).toBe(servedText)
    })

    // Closes a gap the equality test above cannot, on its own: if
    // `ReactionKineticsSection` ignored the server's own `levels` and
    // always ran the fallback derivation, THIS fixture's served and
    // derived paths would coincidentally still agree (they resolve the
    // same underlying facts). Here the served `levels` is DELIBERATELY
    // set to a level the dependency-edge derivation would NOT produce
    // from the same provenance -- proving the served value is trusted
    // as-is, not silently re-derived out from under it.
    it("trusts the server's own served levels verbatim, never silently re-deriving over them", async () => {
        const servedLevels = {
            geometry: { method: "ccsd(t)", basis: "cc-pvtz" }, // NOT what derivation would compute (b3lyp/def2tzvp, via calc_opt1)
            frequency: { method: "ccsd(t)", basis: "cc-pvtz" },
            energy: { method: "ccsd(t)", basis: "cc-pvtz" },
            energy_source: "sp",
        }
        handleFull(mockFull({ kinetics: [kineticsRecordFixture(servedLevels)] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const text = productLevelsBlockText(container)
        expect(text).toContain("ccsd(t)/cc-pvtz")
        expect(text).not.toContain("b3lyp/def2tzvp")
    })

    it("derives geometry via the freq_on dependency-edge parent-opt walk when levels is absent (mirrors the backend's own resolver)", async () => {
        handleFull(mockFull({ kinetics: [kineticsRecordFixture(undefined)] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const geometryRow = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] .reaction-product-levels dt'))
            .find((dt) => dt.textContent === "Geometry")
        expect(geometryRow?.nextElementSibling?.textContent).toContain("b3lyp/def2tzvp")
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

    it("renders Geometry/Frequencies/Energy facts for the TS block (ProductLevelsFact is not silently dropped)", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]')!
        const dts = Array.from(tsSection.querySelectorAll(".reaction-product-levels dt")).map((el) => el.textContent)
        expect(dts).toEqual(["Geometry", "Frequencies", "Energy"])
    })

    // The gap this test exists to close: `ReactionTransitionStatesSection`'s
    // own `reactants`/`products`/`reversible` props were built and unit
    // tested in isolation, but the ONE call site that matters --
    // `EntryDetail` below, inside THIS file -- kept passing only
    // `transitionStates`/`calculations` for a full review cycle, so the
    // equation caption above the dependency graph was unreachable code on
    // the live page despite every unit test passing. Only a render of the
    // real page (this file, not the section's own test file) can catch a
    // wiring gap at the CALL SITE -- exercised here via the page's own
    // already-served `species.reactants`/`.products` fixture (spe_water/
    // spe_ch3 -> spe_ch4/spe_oh), never synthetic props handed to the
    // section directly.
    it("wires the page's own species participants into the dependency graph's equation caption", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]')!
        const caption = tsSection.querySelector('[data-testid="dep-graph-equation-caption"]')
        expect(caption, "equation caption not rendered above the TS dependency graph -- reactants/products/reversible not reaching ReactionTransitionStatesSection from EntryDetail's own record").not.toBeNull()
        const reactantLink = within(caption as HTMLElement).getByRole("link", { name: "H2O" })
        expect(reactantLink).toHaveAttribute("href", "/species-entries/spe_water")
        const productLink = within(caption as HTMLElement).getByRole("link", { name: "CH4" })
        expect(productLink).toHaveAttribute("href", "/species-entries/spe_ch4")
        // `reversible` reaches the caption via a THIRD prop the two link
        // assertions above cannot exercise at all -- review finding:
        // dropping `reversible={entry.reversible}` from the call site left
        // the full suite green while the caption printed "→" instead of
        // "⇌" for every one of this archive's reactions (all 42 live
        // reaction entries are reversible), a false chemistry claim in an
        // authoritative-looking position. `mockFull`'s own fixture is
        // `reversible: true`.
        expect(caption!.textContent).toContain("⇌")
        expect(caption!.textContent).not.toContain("→")
    })

    // Review finding: `species` is a nullable §3A field
    // (`api/reactionEntryApi.ts`'s `reactionFullResponseSchema`), and
    // `EntryDetail` normalises its absence to `{reactants: [], products:
    // []}` -- empty arrays, which are TRUTHY. An earlier version of the
    // section's own gate (`reactants && products`) rendered a bare
    // "Reaction:  ⇌ " with nothing on either side for exactly this
    // response shape; the section's OWN test file could not catch it
    // because it exercised the gate with `undefined`, a shape no real
    // caller produces (the page always normalises to `[]`, never passes
    // `undefined` through). Only a render of the real page against a
    // payload with `species` genuinely omitted proves the fix.
    it("renders no equation caption -- and never a bare arrow -- when /full omits species entirely", async () => {
        const payload = mockFull()
        delete (payload as Record<string, unknown>).species
        handleFull(payload)
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]')!
        expect(tsSection.querySelector('[data-testid="dep-graph-equation-caption"]')).toBeNull()
        expect(tsSection.textContent).not.toMatch(/⇌/)
        expect(tsSection.textContent).not.toMatch(/Reaction:/)
        // Everything that does NOT depend on species still renders.
        expect(tsSection.querySelector('[data-testid="dep-graph-subject-caption"]')).not.toBeNull()
    })
})

// Post-review: the reviewer measured that several facts on this page could
// be silently hard-coded or swapped without any test noticing, because
// every existing fixture happened to use the SAME value everywhere (e.g.
// "not_reviewed" for every review status, so a mutation hard-coding the
// pill to a fixed string would still pass). These tests use DISTINCT,
// non-default values per fact specifically so a hard-coded or swapped
// render is distinguishable from a correctly wired one.
describe("ReactionEntryPage -- individual facts are wired, not hard-coded (post-review mutation coverage)", () => {
    it("the header review pill reflects the entry's OWN served status, not a fixed 'approved'/'not reviewed'", async () => {
        handleFull(mockFull({ reaction_entry: { ...mockFull().reaction_entry, review: { status: "under_review" } } }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const header = container.querySelector(".record-identity-kicker-row")!
        expect(header.textContent).toContain("under review")
        expect(header.textContent).not.toContain("approved")
        expect(header.textContent).not.toContain("not reviewed")
    })

    it("the Review section's six counts are each their OWN served number, not swapped with a sibling row", async () => {
        handleFull(mockFull({
            review_summary: { approved: 3, under_review: 5, not_reviewed: 7, deprecated: 11, rejected: 13, total: 39 },
        }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const reviewSection = container.querySelector('section[aria-labelledby="review-heading"]')!
        const rows = Array.from(reviewSection.querySelectorAll(".coverage-checklist > div")).map((row) => ({
            label: row.querySelector("dt")?.textContent,
            value: row.querySelector("dd")?.textContent,
        }))
        expect(rows).toEqual([
            { label: "Approved", value: "3" },
            { label: "Under review", value: "5" },
            { label: "Not reviewed", value: "7" },
            { label: "Deprecated", value: "11" },
            { label: "Rejected", value: "13" },
            { label: "Total joined records", value: "39" },
        ])
    })

    // Independent review: `EvidenceChecklist`'s "N rows" fallback used to
    // collapse this card behind a NUMBER THAT NEVER CHANGES (the card
    // always has 6 category rows), regardless of the real total -- MEASURED
    // live, `rxe_h3z3f7tjfsj3gbvvh7lmmpikx4` collapsed to "6 rows" while the
    // true total was 4. This fixture's `total: 39` (distinct from the fixed
    // 6-row count) proves the collapsed summary is wired to the real
    // `review_summary.total`, not the row list's own length.
    it("the Review card's collapsed summary is the TRUE total joined records, never the fixed 6-row count", async () => {
        handleFull(mockFull({
            review_summary: { approved: 3, under_review: 5, not_reviewed: 7, deprecated: 11, rejected: 13, total: 39 },
        }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const reviewSection = container.querySelector('section[aria-labelledby="review-heading"]')!
        const card = reviewSection.querySelector(".card.card--derived.coverage-card") as HTMLElement
        const rollup = card.querySelector(".coverage-checklist-summary") as HTMLElement
        expect(rollup).toHaveTextContent("39 joined records")
        expect(rollup.textContent).not.toContain("6 rows")

        // Still collapsed by default (a real summary was supplied) --
        // opening it reaches the same rows the test above already pinned.
        const details = card.querySelector("details") as HTMLDetailsElement
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        expect(details.open).toBe(false)
        expect(checklist).not.toBeVisible()
        fireEvent.click(details.querySelector("summary")!)
        expect(checklist).toBeVisible()
    })

    it("the Participants table's SMILES column renders each participant's own served SMILES, never blank", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const smilesCells = Array.from(container.querySelectorAll('td[data-label="SMILES"]')).map((td) => td.textContent)
        expect(smilesCells).toEqual(["O", "[CH3]", "C", "[OH]"])
        expect(smilesCells.every((text) => text && text.length > 0)).toBe(true)
    })

    it("the Pressure-dependent network table's T/P ranges and channel count are each their own served value", async () => {
        handleFull(mockFull({
            networks: [{
                network_ref: "net_distinct1",
                name: "hydrazine",
                solve_temperature_min_k: 111,
                solve_temperature_max_k: 222,
                solve_pressure_min_bar: 0.5,
                solve_pressure_max_bar: 333,
                channel_count: 17,
                review: { status: "not_reviewed" },
            }],
        }))
        const { container } = page()
        await screen.findByText("net_distinct1")
        const row = Array.from(container.querySelectorAll("tr")).find((tr) => tr.textContent?.includes("net_distinct1"))!
        expect(row.querySelector('td[data-label="Solve T range"]')?.textContent).toBe("111–222 K")
        expect(row.querySelector('td[data-label="Solve P range"]')?.textContent).toBe("0.5–333 bar")
        expect(row.querySelector('td[data-label="Channels"]')?.textContent).toBe("17")
    })
})

describe("ReactionEntryPage -- kinetics card evidence prose and k(T) table formatting", () => {
    // Round-2 review finding: A=9444750000 (the live `rxe_snamm...` route)
    // rendered as a bare 10-digit integer -- fixed via `formatArrheniusValue`.
    it("a large A renders in scientific notation, never as a bare 10-digit integer", async () => {
        const record = kineticsRecordFixture(undefined)
        record.parameters = { A: 9444750000, A_units: "per_s", n: 0, Ea_kj_mol: 0 }
        handleFull(mockFull({ kinetics: [record] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const aRow = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] dt')).find((dt) => dt.textContent === "A")
        expect(aRow?.nextElementSibling?.textContent).toContain("9.4448×10⁹")
        expect(aRow?.nextElementSibling?.textContent).not.toContain("9444750000")
    })

    it("a small A renders in scientific notation too", async () => {
        const record = kineticsRecordFixture(undefined)
        record.parameters = { A: 0.00234, A_units: "cm3_mol_s", n: 0, Ea_kj_mol: 0 }
        handleFull(mockFull({ kinetics: [record] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const aRow = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] dt')).find((dt) => dt.textContent === "A")
        expect(aRow?.nextElementSibling?.textContent).toContain("2.3400×10⁻³")
    })

    it("evidence completeness rows render prose labels, not raw API keys", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const labels = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] .coverage-checklist dt')).map((dt) => dt.textContent)
        expect(labels).toEqual([
            "Source calculations",
            "Transition-state entry",
            "TS opt evidence",
            "TS freq evidence",
            "TS sp evidence",
            "Path search or IRC evidence",
            "Uncertainty",
            "Geometry validation",
            "SCF stability",
        ])
        // None of the raw underscored keys leak through.
        expect(container.textContent).not.toContain("has_source_calculations")
        expect(container.textContent).not.toContain("HAS_SOURCE_CALCULATIONS")
    })

    it("the k(T) table header names the unit next to k, distinct from the T (K) column", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const headers = Array.from(container.querySelectorAll(".kinetics-k-table thead th")).map((th) => th.textContent)
        expect(headers).toEqual(["T (K)", "k (cm³ mol⁻¹ s⁻¹)", "log₁₀ k"])
    })

    // Round-2 review finding: `.data-table th` (design-system.css)
    // uppercases every header via `text-transform`, which rendered this
    // table's "k (cm³ mol⁻¹ s⁻¹)" header as "K (CM³ MOL⁻¹ S⁻¹)" -- visually
    // indistinguishable from "T (K)" -- even though the underlying
    // `textContent` (asserted above) was already correctly lowercase.
    // `vite.config.ts`'s `test.css: true` makes `getComputedStyle` honor
    // real stylesheet rules in this test environment, so this checks the
    // COMPUTED style, not just the DOM text a CSS-blind assertion cannot
    // distinguish from a visually-broken render.
    it("the k(T) table header is NOT uppercased -- 'k' must render lowercase, not as 'K'", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const headers = Array.from(container.querySelectorAll(".kinetics-k-table thead th"))
        expect(headers).toHaveLength(3)
        for (const th of headers) {
            expect(getComputedStyle(th).textTransform).toBe("none")
        }
    })

    it("Fit software with no recorded version says so explicitly, matching the mock's own wording", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const fitSoftwareRow = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] dt'))
            .find((dt) => dt.textContent === "Fit software")
        expect(fitSoftwareRow?.nextElementSibling?.textContent).toBe("Arkane (version not recorded)")
    })

    // The live "resolver disagreement" case (plan §7): a kinetics record's
    // OWN cited sp calculation is not among this reaction's TS graph at
    // all. The row still LINKS (the calc is a real, followable archive
    // record), but carries an inline caveat explaining the discrepancy --
    // MEASURED gap this closes: the row used to render unlinked with no
    // explanation at all.
    it("the TS sp calculation row links even when the ref is not among this entry's own calculations, with an explanatory caveat", async () => {
        const payload = mockFull()
        payload.kinetics[0].provenance.ts_sp_calculation_ref = "calc_unlinked_sp"
        // NOT added to `calculations[]` -- the live resolver-disagreement shape.
        handleFull(payload)
        const { container } = page()
        await screen.findByText("kin_test1")
        const link = container.querySelector('a[href="/calculations/calc_unlinked_sp"]')
        expect(link).not.toBeNull()
        expect(link!.textContent).toBe("calc_unlinked_sp")
        expect(container.textContent).toContain("not among the calculations this reaction entry's transition-state graph itself lists")
    })
})

// Owner report this fixes: "we should add a 4th column ... that is the
// reference rather than it being in the same column as Formula" -- the
// Formula cell used to carry the linked formula PLUS the spe_... ref PLUS
// its own copy button, rendering as "C₉H₉ spe_... Copy" in one cell.
describe("ReactionEntryPage -- Participants table: the reference has its own column, not the Formula cell", () => {
    it("the Formula cell carries only the linked formula (or its SMILES fallback) -- no ref, no copy button", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const formulaCells = Array.from(container.querySelectorAll('td[data-label="Formula"]'))
        expect(formulaCells.length).toBeGreaterThan(0)
        for (const cell of formulaCells) {
            expect(cell.querySelector("code.data")).toBeNull()
            expect(cell.querySelector(".copy-button")).toBeNull()
        }
        // The formula cell still links to the participant's species-entry page.
        const waterCell = formulaCells.find((cell) => cell.textContent?.includes("H2O") || cell.textContent === "H₂O")
        expect(waterCell?.querySelector("a")).not.toBeNull()
    })

    // MUTATION GUARD (mandatory mutation table item a): putting the ref
    // back into the Formula cell must turn this red.
    it("MUTATION GUARD: the participant's own species_entry_ref never appears inside a Formula cell", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const formulaCells = Array.from(container.querySelectorAll('td[data-label="Formula"]'))
        for (const ref of ["spe_water", "spe_ch3", "spe_ch4", "spe_oh"]) {
            for (const cell of formulaCells) {
                expect(cell.textContent).not.toContain(ref)
            }
        }
    })

    it("adds a Ref column header, between SMILES and Review, carrying the ref and its copy button", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const table = container.querySelector('table[aria-label="Reactant participants"]') as HTMLElement
        const headers = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent)
        expect(headers).toEqual(["Formula", "SMILES", "Ref", "Review"])

        const refCell = table.querySelector('td[data-label="Ref"]') as HTMLElement
        expect(refCell).not.toBeNull()
        expect(refCell.querySelector("code.data")?.textContent).toBe("spe_water")
        expect(refCell.querySelector(".copy-button")).not.toBeNull()
    })

    it("every participant ref still appears exactly once outside References -- moved column, not a new/lost mention", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        for (const ref of ["spe_water", "spe_ch3", "spe_ch4", "spe_oh"]) {
            expect(occurrencesOutsideRefs(container, ref)).toBe(1)
        }
    })
})

// Owner: "these pill boxes like deposited etc. - can they be clickable
// links to wherever they are meant to go?" -- a row asserting PRESENCE
// links to its own section; "none deposited" (and every other absence)
// never does.
describe("ReactionEntryPage -- evidence checklist: collapsible, and presence-only pill links", () => {
    function evidenceCard(container: HTMLElement): HTMLElement {
        return Array.from(container.querySelectorAll('[data-component="evidence-checklist"]'))
            .find((card) => card.textContent?.includes("Evidence on this reaction entry")) as HTMLElement
    }

    it("the evidence card is collapsed by default (owner: 'Evidence blocks should be expandable rather')", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        const details = card.querySelector("details") as HTMLDetailsElement
        expect(details).not.toBeNull()
        expect(details.open).toBe(false)
        expect(card.querySelector(".coverage-checklist")).not.toBeVisible()
        // The collapsed summary still names how many rows are present vs
        // absent -- a reader who never opens it still learns whether
        // evidence exists (this fixture: Kinetics/TS entries/IRC present,
        // Atom map/Path search/network absent).
        expect(card.querySelector(".coverage-checklist-summary")).toHaveTextContent("3 present, 3 absent")
    })

    it("opening it shows the same rows an always-open card used to", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        fireEvent.click(card.querySelector("summary")!)
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        expect(checklist).toBeVisible()
        const dt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Kinetics records")
        expect(dt?.nextElementSibling?.textContent).toBe("1 deposited")
    })

    it("a present row with a real section on this page (Kinetics/TS entries) links to it", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        fireEvent.click(card.querySelector("summary")!)
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement

        const kineticsDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Kinetics records")
        const kineticsLink = kineticsDt?.nextElementSibling?.querySelector("a")
        expect(kineticsLink).not.toBeNull()
        expect(kineticsLink).toHaveAttribute("href", "#kinetics-heading")

        const tsDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Transition-state entries")
        const tsLink = tsDt?.nextElementSibling?.querySelector("a")
        expect(tsLink).not.toBeNull()
        expect(tsLink).toHaveAttribute("href", "#ts-heading")
    })

    // MUTATION GUARD (mandatory mutation table item b): guards THIS PAGE's
    // own conditional -- it only ever passes `to` alongside `tone: "pill"`
    // on the Kinetics/TS-entries/network rows (see the `...(x.length ? {
    // to: "..." } : {})` spreads above). It does NOT independently prove
    // the shared component's own enforcement: under a mutation of
    // `EvidenceChecklist`'s `RowValue` alone (honouring `to` regardless of
    // `tone`), this page never supplies `to` on a muted row in the first
    // place, so this test stays green -- `EvidenceChecklist.test.tsx`'s
    // own "MUTATION GUARD: 'to' on a pill-muted row is silently ignored"
    // test is the one that catches THAT mutation. Still worth keeping:
    // it's the guard against a regression in THIS page's own wiring (e.g.
    // someone adding `to` to the Atom map row "for consistency").
    it("MUTATION GUARD: 'none deposited' rows (Atom map with no atom maps) are never links", async () => {
        handleFull(mockFull({ reaction_entry: { ...mockFull().reaction_entry, atom_maps: [] } }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        fireEvent.click(card.querySelector("summary")!)
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement

        const atomMapDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Atom map")
        const atomMapDd = atomMapDt?.nextElementSibling as HTMLElement
        expect(atomMapDd.textContent).toBe("none deposited")
        expect(atomMapDd.querySelector("a")).toBeNull()

        const networkDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Pressure-dependent network membership")
        const networkDd = networkDt?.nextElementSibling as HTMLElement
        expect(networkDd.textContent).toBe("none deposited")
        expect(networkDd.querySelector("a")).toBeNull()
    })

    it("a present row with no section on this page (Atom map, Path search, IRC evidence) stays plain -- never invents a target", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        fireEvent.click(card.querySelector("summary")!)
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement

        // This fixture's IRC evidence is present (has_irc: true) but has no
        // section of its own -- confirm it renders as a plain pill, not a link.
        const ircDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "IRC evidence")
        const ircDd = ircDt?.nextElementSibling as HTMLElement
        expect(ircDd.textContent).toBe("present")
        expect(ircDd.querySelector("a")).toBeNull()
        expect(ircDd.querySelector(".value-pill")).not.toBeNull()
    })

    it("the network-membership row links to the network section once the fallback resolves and the archive has a network", async () => {
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
        await screen.findByText("kin_test1")
        const card = evidenceCard(container)
        fireEvent.click(card.querySelector("summary")!)
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        const networkDt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Pressure-dependent network membership")
        const networkLink = networkDt?.nextElementSibling?.querySelector("a")
        expect(networkLink).not.toBeNull()
        expect(networkLink).toHaveAttribute("href", "#network-heading")
    })
})
