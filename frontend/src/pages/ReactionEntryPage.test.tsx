import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
// `design-system.css` is normally reached only via `index.css`'s `@import`
// at the top -- the real app loads `index.css` once, globally, in
// `main.tsx`, before any page's own stylesheet. This test renders
// `ReactionEntryPage` in isolation (no `index.css` in its own import
// graph), so `.t-preserve-case`/`.kv-list dt`'s own uppercase rule would
// otherwise be ABSENT from jsdom's stylesheet set entirely -- the
// "computed text-transform is none" guard below needs the real rule
// present to prove anything (an unstyled element's computed value is `""`
// in jsdom, not a meaningful "not uppercase"). Direct import, not
// `index.css` itself, to avoid pulling in that file's own unrelated global
// rules for a test scoped to this one page.
import "../design-system.css"
import ReactionEntryPage from "./ReactionEntryPage"
import { ADVERSARIAL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels, setAt } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"
import { rankingWords, textOf } from "../test/rankingWords"

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

/** The energy-profile and transition-state reads every entry page makes; a test that needs them overrides these. */
function handleSideReads(profile: object = { request: {}, reaction_entry_ref: ENTRY_REF, profiles: [], unavailable: [], cited_by_kinetics: [] }) {
    server.use(
        http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/energy-profile`, () => HttpResponse.json(profile)),
        http.get("/api/v1/scientific/transition-state-entries/:ref", () => HttpResponse.json({ detail: "not found" }, { status: 404 })),
    )
}

beforeEach(() => handleSideReads())

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
        await waitFor(() => expect(titleHasKind(document.title, "/reaction-entries/:ref")).toBe(true))

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

    // SCIENTIFIC ERROR regression guard (sweep finding, same class as
    // `ArrheniusChart.test.tsx`'s "s⁻¹"/"log₁₀ k" guards): `.kv-list dt`
    // (design-system.css) uppercases every fact label via
    // `--type-label-transform` -- correct for prose labels elsewhere on
    // this page but not for the Arrhenius parameter names "A"/"n"/"Ea" in
    // `ReactionKineticsSection.tsx`. "n" (the temperature exponent)
    // upper-cased to "N" is the sharpest instance -- a different symbol,
    // not a capitalised version of the same one. A `textContent` assertion
    // cannot see this (the DOM text is still correctly-cased; only the
    // COMPUTED style differs) -- this asserts `getComputedStyle`, per
    // `vite.config.ts`'s `test.css: true`.
    it("computed text-transform is none on the A/n/Ea parameter-name dt's, textContent stays lower-case 'n'", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const kineticsSection = container.querySelector('section[aria-labelledby="kinetics-heading"]')!
        const dts = Array.from(kineticsSection.querySelectorAll("dt")).filter((dt) =>
            ["A", "n", "Ea"].includes(dt.textContent ?? ""),
        )
        expect(dts.map((dt) => dt.textContent)).toEqual(["A", "n", "Ea"])
        for (const dt of dts) {
            expect(getComputedStyle(dt as HTMLElement).textTransform).toBe("none")
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
        expect(screen.getAllByText("Pressure-dependent network").length).toBeGreaterThan(0)
        expect(document.body.innerHTML).not.toContain("hydrazine")
        expect(screen.getByText("21")).toBeVisible()
        // `net_` refs are never cited in this page's References disclosure
        // (plan §2's non-goal: no network page yet, so a network is a plain
        // row, not a related-ref citation) -- exactly one mention.
        expect(occurrencesOutsideRefs(container, "net_test1")).toBe(1)

        // `/networks/:ref` is a real page now (PR 2 of
        // `docs/plans/pressure-dependent-network-surface.md`) -- the ref
        // renders as a link to it, in the data face, alongside its own copy
        // button (both, not one instead of the other -- matching every
        // other `Ref` column on this page).
        const refLink = screen.getByRole("link", { name: "net_test1" })
        expect(refLink).toHaveAttribute("href", "/networks/net_test1")
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

    it("kinetics: [] and several networks -- names every network, and says none is preferred, instead of naming only the first", async () => {
        const network = (ref: string) => ({
            network_ref: ref, name: "hydrazine", solve_temperature_min_k: 300, solve_temperature_max_k: 2000,
            solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100, channel_count: 21, review: { status: "not_reviewed" },
        })
        handleFull(mockFull({ kinetics: [], networks: [network("net_one"), network("net_two")] }))
        page()
        expect(await screen.findByText(/served by 2 networks, none preferred over another/)).toBeVisible()
        expect(screen.getByText("net_one", { selector: "a" })).toBeVisible()
        expect(screen.getByText("net_two", { selector: "a" })).toBeVisible()
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

    it("renders a transition state whose owner is withheld (profile=curated): no owner ref, the entry still there", async () => {
        const full = mockFull() as { transition_states: Record<string, unknown>[] }
        full.transition_states[0].transition_state_ref = null
        handleFull(full)
        page()
        await screen.findByText("kin_test1")
        expect(screen.queryByText("ts_test1")).not.toBeInTheDocument()
        const strip = screen.getByTestId("ts-status-strip")
        expect(strip.querySelector("[aria-current=step]")?.textContent).toContain("Optimized")
    })

    it("renders the guess/optimized/validated status strip with the served status selected", async () => {
        handleFull(mockFull())
        page()
        await screen.findByText("kin_test1")
        const strip = screen.getByTestId("ts-status-strip")
        const selected = strip.querySelector("[aria-current=step]")
        expect(selected?.textContent).toContain("Optimized")
        expect(strip.querySelectorAll("li")).toHaveLength(3)
    })

    it("renders Geometry/Frequencies/Energy facts for the TS block (ProductLevelsFact is not silently dropped)", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]')!
        const dts = Array.from(tsSection.querySelectorAll(".reaction-product-levels dt")).map((el) => el.textContent)
        expect(dts).toEqual(["Geometry", "Frequencies", "Energy"])
    })

    // Owner complaint, round 2 (2026-09): an earlier version of the TS
    // dependency-graph fix put an equation caption ("Reaction: ...") above
    // the graph -- "This does need repeating what reaction since the top
    // of the page says which reaction". The page's own `<h1>` (asserted
    // elsewhere in this file) already states the equation; this section
    // must never restate it, regardless of what `/full` serves for
    // `species`.
    it("never restates the reaction equation above the TS dependency graph -- the page's own h1 already states it", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]')!
        expect(tsSection.querySelector('[data-testid="dep-graph-equation-caption"]')).toBeNull()
        expect(tsSection.textContent).not.toMatch(/⇌/)
        expect(tsSection.textContent).not.toMatch(/^Reaction:/)
    })

    // Round-1 fix (still standing): the centre node itself carries what
    // the optimisation is OF, via a real link to the TS entry inside the
    // node -- no caption needed above the graph to say it in prose.
    it("names the dependency graph centre node's own subject as a link inside the node, from the page's own served TS ref", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const tsSection = container.querySelector('section[aria-labelledby="ts-heading"]') as HTMLElement
        const centreNode = within(tsSection).getByTestId("dep-node-centre-calc_opt1")
        const subjectLink = within(centreNode).getByTestId("dep-node-subject-calc_opt1")
        expect(subjectLink).toHaveTextContent("Transition state")
        expect(subjectLink).toHaveAttribute("href", "/transition-state-entries/tse_test1")
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

    // The old, separate "SMILES" column is gone (SMILES leads inside the
    // "Species" column now -- see `ParticipantsTable`'s own comment); this
    // asserts the same underlying fact the old test did, that every
    // participant's own served SMILES shows up, never blank.
    it("the Participants table's Species column renders each participant's own served SMILES, never blank", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const speciesCells = Array.from(container.querySelectorAll('td[data-label="Species"] code.data')).map((code) => code.textContent)
        expect(speciesCells).toEqual(["O", "[CH3]", "C", "[OH]"])
        expect(speciesCells.every((text) => text && text.length > 0)).toBe(true)
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

describe("ReactionEntryPage -- rounded temperature ranges", () => {
    it("shows the network solve range and the kinetics record range to 6 significant figures", async () => {
        const record = kineticsRecordFixture(undefined)
        record.temperature_coverage = { record_min_k: 300.0000001, record_max_k: 3000.0000002 }
        handleFull(mockFull({
            kinetics: [record],
            networks: [{
                network_ref: "net_rounded1", name: "hydrazine", solve_temperature_min_k: 111.0000001, solve_temperature_max_k: 222,
                solve_pressure_min_bar: 0.5, solve_pressure_max_bar: 333, channel_count: 1, review: { status: "not_reviewed" },
            }],
        }))
        const { container } = page()
        await screen.findByText("net_rounded1")
        const row = Array.from(container.querySelectorAll("tr")).find((tr) => tr.textContent?.includes("net_rounded1"))!
        expect(row.querySelector('td[data-label="Solve T range"]')?.textContent).toBe("111–222 K")
        expect(container.textContent).toContain("300–3000 K")
        expect(container.textContent).not.toContain("300.0000001")
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

    // #620: A only means something beside the T0 it was fitted at.
    const kineticsLabels = (container: HTMLElement) =>
        Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] dt')).map((dt) => dt.textContent)

    it("shows T0 when the record's reference temperature is not 1 K", async () => {
        const record = kineticsRecordFixture(undefined)
        record.parameters = { A: 1e10, A_units: "cm3_mol_s", n: 2, Ea_kj_mol: 0, T0_k: 298.15 } as never
        handleFull(mockFull({ kinetics: [record] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(kineticsLabels(container)).toContain("T0")
        const t0 = Array.from(container.querySelectorAll('section[aria-labelledby="kinetics-heading"] dt')).find((dt) => dt.textContent === "T0")
        expect(t0?.nextElementSibling?.textContent).toContain("298.15")
        expect(t0?.nextElementSibling?.textContent).toContain("K")
    })

    it("shows no T0 row when the reference temperature is 1 K", async () => {
        const record = kineticsRecordFixture(undefined)
        record.parameters = { A: 1e10, A_units: "cm3_mol_s", n: 2, Ea_kj_mol: 0, T0_k: 1 } as never
        handleFull(mockFull({ kinetics: [record] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(kineticsLabels(container)).toContain("Ea")
        expect(kineticsLabels(container)).not.toContain("T0")
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
// (The old "Formula" and "SMILES" columns were later consolidated into
// one "Species" column -- SMILES-leads-formula-in-brackets -- but the Ref
// column this describe block covers is unaffected: it still carries only
// the ref and its copy button, never folded back into the identity cell.)
describe("ReactionEntryPage -- Participants table: the reference has its own column, not the Species cell", () => {
    it("the Species cell carries only the linked SMILES/formula -- no ref, no copy button", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const speciesCells = Array.from(container.querySelectorAll('td[data-label="Species"]'))
        expect(speciesCells.length).toBeGreaterThan(0)
        for (const cell of speciesCells) {
            expect(cell.querySelector(".copy-button")).toBeNull()
        }
        // The species cell still links to the participant's species-entry page.
        const waterCell = speciesCells.find((cell) => cell.textContent?.includes("H2O") || cell.textContent === "H₂O")
        expect(waterCell?.querySelector("a")).not.toBeNull()
    })

    // MUTATION GUARD (mandatory mutation table item a): putting the ref
    // back into the Species cell must turn this red.
    it("MUTATION GUARD: the participant's own species_entry_ref never appears inside a Species cell", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const speciesCells = Array.from(container.querySelectorAll('td[data-label="Species"]'))
        for (const ref of ["spe_water", "spe_ch3", "spe_ch4", "spe_oh"]) {
            for (const cell of speciesCells) {
                expect(cell.textContent).not.toContain(ref)
            }
        }
    })

    it("carries a Ref column header, between Species and Review, carrying the ref and its copy button", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const table = container.querySelector('table[aria-label="Reactant participants"]') as HTMLElement
        const headers = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent)
        expect(headers).toEqual(["Species", "Ref", "Review"])

        const refCell = table.querySelector('td[data-label="Ref"]') as HTMLElement
        expect(refCell).not.toBeNull()
        expect(refCell.querySelector("code.data")?.textContent).toBe("spe_water")
        expect(refCell.querySelector(".copy-button")).not.toBeNull()
    })

    // SMILES-leads-formula-in-brackets consolidation (owner ruling):
    // the Species cell renders "O (H2O)", not a bare formula that two
    // different structures sharing a formula could render identically.
    it("the Species cell leads with SMILES and brackets the formula after it", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const link = container.querySelector('td[data-label="Species"] a[href="/species-entries/spe_water"]')
        expect(link?.textContent).toBe("O (H2O)")
    })

    it("tells a Z-to-E isomerisation apart from the structured facets, and never from the joined species_entry_label", async () => {
        const base = mockFull()
        const facets = { species_entry_kind: "minimum", electronic_state_kind: "ground", isotope_key: null, stereo_resolution: "resolved" }
        handleFull({
            ...base,
            reaction_entry: { ...base.reaction_entry, equation: "CC=CC -> CC=CC", reversible: false },
            species: {
                reactants: [{
                    species_entry_ref: "spe_z", species_entry_label: "Z", ...facets, stereo_label: "Z", stereo_descriptor: "Z",
                    smiles: "CC=CC", formula: "C4H8", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" },
                }],
                products: [{
                    species_entry_ref: "spe_e", species_entry_label: "E", ...facets, stereo_label: "E", stereo_descriptor: "E",
                    smiles: "CC=CC", formula: "C4H8", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" },
                }],
            },
        })
        const { container } = page()
        await screen.findByText("kin_test1")
        const fact = Array.from(container.querySelectorAll(".record-identity-header dt")).find((el) => el.textContent === "Equation (as deposited)")
        expect(fact?.nextElementSibling?.textContent).toBe("CC=CC [Z isomer] -> CC=CC [E isomer]")
        const reactantCell = container.querySelector('table[aria-label="Reactant participants"] td[data-label="Species"]')
        expect(reactantCell?.textContent).toContain("Z isomer")
        const productCell = container.querySelector('table[aria-label="Product participants"] td[data-label="Species"]')
        expect(productCell?.textContent).toContain("E isomer")
    })

    it("builds no stereo wording from a joined label whose R was typed as an electronic state label", async () => {
        const base = mockFull()
        handleFull({
            ...base,
            species: {
                reactants: [{
                    species_entry_ref: "spe_typed", species_entry_label: "R", species_entry_kind: "minimum", electronic_state_kind: "ground",
                    stereo_label: null, stereo_descriptor: null, smiles: "[CH3]", formula: "CH3", stoichiometry: 1, participant_index: 0,
                    review: { status: "not_reviewed" },
                }],
                products: base.species.products,
            },
        })
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(container.textContent).not.toContain("enantiomer")
    })

    function factText(container: HTMLElement): string | null | undefined {
        const fact = Array.from(container.querySelectorAll(".record-identity-header dt")).find((el) => el.textContent === "Equation (as deposited)")
        return fact?.nextElementSibling?.textContent
    }

    // `/full` serves each distinct entry once per side with its own occurrence count (`H + H -> H2` is ONE H row,
    // stoichiometry 2). Both the hero equation and the as-deposited line read the coefficient from that row.
    it("H + H -> H2 shows 2 H in both the hero equation and the as-deposited line", async () => {
        const base = mockFull()
        handleFull({
            ...base,
            reaction_entry: { ...base.reaction_entry, equation: "[H] + [H] -> [H][H]", reversible: false },
            species: {
                reactants: [{ species_entry_ref: "spe_h", smiles: "[H]", formula: "H", stoichiometry: 2, participant_index: 1, review: { status: "not_reviewed" } }],
                products: [{ species_entry_ref: "spe_h2", smiles: "[H][H]", formula: "H2", stoichiometry: 1, participant_index: 1, review: { status: "not_reviewed" } }],
            },
        })
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(factText(container)).toBe("2 [H] -> [H][H]")
        const hero = container.querySelector("h1")?.textContent ?? ""
        expect(hero).toMatch(/^2 /)
        expect(hero).not.toMatch(/4/)
    })

    it("a coefficient-one reaction shows no coefficients", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        expect(factText(container)).toBe("O + [CH3] <=> C + [OH]")
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

describe("ReactionEntryPage -- the References disclosure names every kinetics record's provenance", () => {
    /** The default kinetics record, with its own kinetics ref and its own level / software / workflow-tool refs. */
    function kineticsWithRefs(n: number) {
        const base = (mockFull().kinetics as Array<{ provenance: Record<string, unknown> } & Record<string, unknown>>)[0]
        return {
            ...base,
            kinetics_ref: `kin_multi${n}`,
            provenance: {
                ...base.provenance,
                primary_level_of_theory: { method: "m", basis: "b", level_of_theory_ref: `lot_multi${n}` },
                primary_software: { software: "Gaussian", version: "16", software_release_ref: `swr_primary${n}` },
                software_release: { software: "Arkane", version: null, software_release_ref: `swr_fit${n}` },
                workflow_tool_release: { workflow_tool: "ARC", version: "1.1.0", workflow_tool_release_ref: `wtr_multi${n}` },
            },
        }
    }

    it("lists the level, software and workflow-tool refs of every record, not only the first", async () => {
        handleFull(mockFull({ kinetics: [kineticsWithRefs(1), kineticsWithRefs(2)] }))
        const { container } = page()
        await screen.findByText("kin_multi1")

        const refs = container.querySelector(".refs-disclosure")?.textContent ?? ""
        for (const n of [1, 2]) {
            for (const ref of [`lot_multi${n}`, `swr_primary${n}`, `swr_fit${n}`, `wtr_multi${n}`]) {
                expect(refs, `${ref} (record ${n}) should be in the References disclosure`).toContain(ref)
            }
        }
    })

    it("names a ref shared by two records once", async () => {
        const first = kineticsWithRefs(1)
        const second = { ...kineticsWithRefs(2), provenance: { ...kineticsWithRefs(2).provenance, primary_level_of_theory: first.provenance.primary_level_of_theory } }
        handleFull(mockFull({ kinetics: [first, second] }))
        const { container } = page()
        await screen.findByText("kin_multi1")

        const refs = container.querySelector(".refs-disclosure")?.textContent ?? ""
        expect(refs.split("lot_multi1").length - 1).toBe(1)
    })
})

describe("ReactionEntryPage -- no depositor-typed text (owner decision 2026-11-03)", () => {
    it("prints none of the network name, the participants' joined labels, or atom-map and kinetics notes", async () => {
        const base = mockFull()
        const planted = plantSentinels(mockFull({
            species: {
                reactants: base.species.reactants.map((p) => ({ ...p, species_entry_label: "x" })) as unknown[],
                products: base.species.products.map((p) => ({ ...p, species_entry_label: "x" })) as unknown[],
            },
            networks: [{
                network_ref: "net_test1", name: "x", solve_temperature_min_k: 300, solve_temperature_max_k: 2000,
                solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100, channel_count: 21, review: { status: "not_reviewed" },
            }],
            kinetics: [{ ...base.kinetics[0], note: "x" }],
        }), ["name", "species_entry_label", "note"])
        expectPlanted(planted)
        const { value } = planted
        setAt(value, "species.reactants.0.species_entry_label", `${ADVERSARIAL.stereoMark} ${ADVERSARIAL.smilesWord}`)
        handleFull(value)
        page()
        // The fixture reached the page: the kinetics record, a participant ref and the network row rendered.
        await screen.findByText("kin_test1")
        expect(await screen.findByText("net_test1")).toBeVisible()
        expect(screen.getAllByText("spe_water").length).toBeGreaterThan(0)

        expectNoDepositorText()
        expectNoAdversarialText()
    })
})

describe("ReactionEntryPage -- kinetics literature (depositor text guard)", () => {
    async function mount(literature: Record<string, unknown>) {
        const payload = mockFull()
        ;(payload.kinetics as Array<{ provenance: Record<string, unknown> }>)[0].provenance.literature = literature
        handleFull(payload)
        page()
        await screen.findByText("kin_test1")
        // The fixture reached the page: the Literature row rendered.
        return screen.getByText("Literature", { selector: "dt" }).nextElementSibling?.textContent
    }

    it("withholds a typed title and a spam DOI; prints the public ref and says the title is not verified", async () => {
        const row = await mount({
            literature_ref: "lit_kin1", title: "DEPOSITOR_SENTINEL_literature_title", title_source: "depositor_supplied",
            doi: "10.1/ DEPOSITOR_SENTINEL_literature_doi spam.example",
        })
        expect(row).toBe("lit_kin1 · Title not verified")
        expectNoDepositorText()
        expect(document.body.innerHTML).not.toContain("spam.example")
    })

    it("shows a registry-resolved title (the positive control)", async () => {
        const row = await mount({ literature_ref: "lit_kin1", title: "RESOLVED_TITLE", title_source: "resolved_metadata" })
        expect(row).toBe("RESOLVED_TITLE")
    })
})

describe("ReactionEntryPage -- electronic-state labels and term symbols", () => {
    const withState = (facets: Record<string, unknown>) => {
        const base = mockFull()
        const [first, ...rest] = base.species.reactants
        return mockFull({
            species: {
                reactants: [{ ...first, species_entry_kind: "minimum", electronic_state_kind: "excited", ...facets }, ...rest],
                products: base.species.products,
            },
        })
    }

    it("shows a grammar-valid state label and term symbol beside the participant (T1, X ²Π)", async () => {
        handleFull(withState({ electronic_state_label: "T1", term_symbol: "X ²Π" }))
        page()
        await screen.findByText("kin_test1")
        const row = screen.getAllByText("spe_water").map((node) => node.closest("tr")).find(Boolean) as HTMLElement
        expect(row).toHaveTextContent("excited state, state T1, X ²Π")
        // The word `state` keeps a bare state letter from reading as a stereo descriptor.
        expect(document.body.textContent).not.toMatch(/\bT1 (isomer|enantiomer)/)
    })

    it("hides a label or term symbol outside the grammar, including a typed stereo letter (the hide-invalid path)", async () => {
        handleFull(withState({ electronic_state_label: "R", term_symbol: "DEPOSITOR_SENTINEL_term" }))
        page()
        await screen.findByText("kin_test1")
        const row = screen.getAllByText("spe_water").map((node) => node.closest("tr")).find(Boolean) as HTMLElement
        expect(row).toHaveTextContent("excited state")
        expect(row.textContent).not.toMatch(/state R|enantiomer/)
        expectNoDepositorText()
    })
})

describe("ReactionEntryPage -- the energy hill, the scheme and the numbers", () => {
    const DEFINITION_WITH_RANKING_WORD = "Each molecule's energy at this level from this program: its lowest single-point energy. Electronic energy, no zero-point correction."
    const calcPoint = (species: string, calc: string, hartree: number) => ({
        species_entry_ref: species, count: 1, calculation_ref: calc, energy_type: "single_point", electronic_energy_hartree: hartree,
        software: { program: "Gaussian", version: "16" }, review_status: "not_reviewed", convergence_unrecorded: false,
    })
    const profileRead = {
        request: { hidden_by_default: null, hidden_by_default_by_scope: {} },
        reaction_entry_ref: ENTRY_REF,
        profiles: [{
            transition_state_entry_ref: "tse_test1",
            transition_state_entry_review_status: "not_reviewed",
            level_of_theory: { level_of_theory_ref: "lot_x", method: "b3lyp", basis: "def2tzvp" },
            program: "Gaussian",
            energy_types: ["single_point"],
            versions: ["16"],
            energy_kind: "electronic",
            definition: DEFINITION_WITH_RANKING_WORD,
            reactants: [calcPoint("spe_water", "calc_w", -76.4), calcPoint("spe_ch3", "calc_m", -39.8)],
            transition_state: {
                transition_state_entry_ref: "tse_test1", calculation_ref: "calc_sp1", energy_type: "single_point", electronic_energy_hartree: -116.2,
                software: { program: "Gaussian", version: "16" }, review_status: "approved", convergence_unrecorded: false,
            },
            products: [calcPoint("spe_ch4", "calc_c", -40.5), calcPoint("spe_oh", "calc_o", -75.7)],
            energy_hartree: { reactants: -116.2, transition_state: -116.19, products: -116.2 },
            relative_kj_mol: { reactants: 0, transition_state: 26.3, products: 0 },
        }],
        unavailable: [],
        cited_by_kinetics: [],
    }

    it("shows the profile section, with the points linked to their calculations, between the participants and the evidence", async () => {
        handleFull(mockFull())
        handleSideReads(profileRead)
        const { container } = page()
        await screen.findByText("kin_test1")
        const section = await waitFor(() => {
            const found = container.querySelector('section[aria-labelledby="energy-heading"]') as HTMLElement
            expect(within(found).getByRole("group", { name: /Energy relative to the reactants/ })).toBeInTheDocument()
            return found
        })
        expect(section.querySelector('a[href="/calculations/calc_sp1"]')).not.toBeNull()
        const order = Array.from(container.querySelectorAll("h2")).map((heading) => heading.id)
        expect(order.indexOf("participants-heading")).toBeLessThan(order.indexOf("energy-heading"))
        expect(order.indexOf("energy-heading")).toBeLessThan(order.indexOf("evidence-heading"))
    })

    it("states the numbers' order, words the record codes and gives each record a source line", async () => {
        handleFull(mockFull())
        const { container } = page()
        await screen.findByText("kin_test1")
        const section = container.querySelector('section[aria-labelledby="kinetics-heading"]') as HTMLElement
        expect(within(section).getByRole("heading", { name: "Numbers for this entry" })).toBeInTheDocument()
        expect(section).toHaveTextContent("Listed approved first, then newest deposited first. This order is not a recommendation.")
        expect(within(section).getAllByRole("link").filter((anchor) => /3025\.44|3\.11242|39\.9711/.test(anchor.textContent ?? ""))).toHaveLength(0)
        expect(section.querySelector(".value-source")).toHaveTextContent("Source kin_test1")
        expect(section.querySelector(".value-source")).toHaveTextContent("Review of this record: not reviewed")
    })

    it("spells the equation out for a screen reader and links each molecule of the scheme", async () => {
        handleFull(mockFull())
        page()
        await screen.findByText("kin_test1")
        const scheme = screen.getByRole("group", { name: "H2O plus CH3 reacts reversibly with CH4 plus HO" })
        expect(within(scheme).getAllByRole("link")).toHaveLength(4)
    })

    it("has breadcrumbs through the reaction, a review word and a lede built from codes", async () => {
        handleFull(mockFull())
        page()
        await screen.findByText("kin_test1")
        const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(crumbs).getByRole("link", { name: "Reactions" })).toHaveAttribute("href", "/reactions")
        expect(crumbs.querySelector('a[href="/reactions/rxn_test1"]')).not.toBeNull()
        expect(document.querySelector(".record-lede")).toHaveTextContent("One deposited version of this reaction: reversible, family H Abstraction, 1 rate record and 1 transition-state entry.")
        expect(screen.getAllByText("not reviewed").length).toBeGreaterThan(0)
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    })

    it("uses no ranking word outside the server's own definition, and no literal double hyphen", async () => {
        handleFull(mockFull())
        handleSideReads(profileRead)
        const { container } = page()
        await screen.findByText("kin_test1")
        await waitFor(() => expect(container.querySelector(".energy-profile-definition")).not.toBeNull())
        const clone = container.cloneNode(true) as HTMLElement
        expect(rankingWords(textOf(clone))).toEqual(["lowest"])
        const definition = clone.querySelector(".energy-profile-definition")?.textContent ?? ""
        expect(definition).toContain("Electronic energy, no zero-point correction.")
        expect(definition).toContain("Each molecule's energy at this level from this program: its lowest single-point energy.")
        clone.querySelector(".energy-profile-definition")?.remove()
        expect(rankingWords(textOf(clone))).toEqual([])
        expect(textOf(container)).not.toContain("--")
    })

    const tseRead = (extra: Record<string, unknown>) => ({
        record: {
            transition_state_entry: { transition_state_entry_ref: "tse_test1", charge: 0, multiplicity: 2, status: "optimized", created_at: "2026-01-01T00:00:00Z", review: { status: "not_reviewed" }, ...extra },
            transition_state: { transition_state_ref: "ts_test1", label: "DEPOSITOR_SENTINEL_ts_label", note: "DEPOSITOR_SENTINEL_ts_note", created_at: "2026-01-01T00:00:00Z", review: { status: "not_reviewed" } },
            reaction: { reaction_ref: "rxn_test1", equation: "DEPOSITOR_SENTINEL_equation" },
            evidence_summary: { calculation_count: 1, has_opt: true, has_freq: false, has_sp: false, has_irc: false, has_path_search: false, has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {} },
            validation: { irc: "absent" },
            available_sections: { has_entries: true, has_calculations: false, has_geometries: true, has_review: false, has_validation_evidence: false },
            geometries: [{ geometry_ref: "geom_ts1", role: "final", natoms: 2 }],
        },
    })
    const geometry = {
        geometry_ref: "geom_ts1", natoms: 2, geom_hash: "h", format: "cartesian", coordinate_units: "angstrom", created_at: "2026-01-01T00:00:00Z",
        atoms: [{ atom_index: 1, element: "C", x: 0, y: 0, z: 0 }, { atom_index: 2, element: "O", x: 1.4, y: 0, z: 0 }], xyz_text: null,
    }

    it("draws the transition state's picture from a geometries-only read, naming the entry, and leaks no depositor text from that read", async () => {
        const requested: string[] = []
        const base = mockFull()
        const planted = plantSentinels(mockFull({ transition_states: [{ ...base.transition_states[0], label: "x", note: "x" }] }), ["label", "note"])
        expectPlanted(planted)
        handleFull(planted.value)
        handleSideReads(profileRead)
        server.use(
            http.get("/api/v1/scientific/transition-state-entries/:ref", ({ request }) => {
                requested.push(new URL(request.url).search)
                return HttpResponse.json(tseRead({}))
            }),
            http.get("/api/v1/scientific/geometries/geom_ts1", () => HttpResponse.json(geometry)),
        )
        page()
        await screen.findByText("kin_test1")
        const picture = await screen.findByRole("img", { name: /Ball-and-stick picture of transition state tse_test1/ })
        expect(picture).toBeInTheDocument()
        expect(requested).toEqual(["?include=geometries"])
        await waitFor(() => expect(document.querySelector(".energy-profile-definition")).not.toBeNull())
        expectDepositorTextOnlyInNotes()
        expectNoDepositorText()
    })

    it("words the model kind, origin and tunnelling model, and shows an unknown code as code", async () => {
        const base = mockFull()
        handleFull(mockFull({ kinetics: [{ ...base.kinetics[0], model_kind: "troe", scientific_origin: "estimated", tunneling_model: "sct" }, { ...base.kinetics[0], kinetics_ref: "kin_test2", model_kind: "new_kind" }] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const section = container.querySelector('section[aria-labelledby="kinetics-heading"]') as HTMLElement
        const dd = (label: string) => Array.from(section.querySelectorAll("dt")).filter((dt) => dt.textContent === label).map((dt) => dt.nextElementSibling)
        expect(dd("Model kind")[0]).toHaveTextContent("Troe falloff")
        expect(dd("Origin")[0]).toHaveTextContent("estimated")
        expect(dd("Tunnelling model")[0]).toHaveTextContent("small-curvature")
        expect(dd("Model kind")[1]?.querySelector("code")).toHaveTextContent("new_kind")
        expect(section.textContent).not.toContain("modified_arrhenius")
    })

    it("reads the title as formulas when they tell the participants apart", async () => {
        handleFull(mockFull())
        page()
        const h1 = await screen.findByRole("heading", { level: 1 })
        await screen.findByText("kin_test1")
        expect(h1.textContent).not.toMatch(/\[CH3\]|\[OH\]/)
    })

    it("adds SMILES to the title when two entries share a formula", async () => {
        const base = mockFull()
        handleFull(mockFull({ species: {
            reactants: [{ ...base.species.reactants[0], species_entry_ref: "spe_z", smiles: "C/C=C\\C", formula: "C4H8" }],
            products: [{ ...base.species.products[0], species_entry_ref: "spe_e", smiles: "C/C=C/C", formula: "C4H8" }],
        } }))
        page()
        await screen.findByText("kin_test1")
        const ambiguous = screen.getByRole("heading", { level: 1 })
        expect(ambiguous.textContent).toContain("C/C=C\\C")
        expect(ambiguous.textContent).toContain("C/C=C/C")
    })

    it("shows a rejected transition state as the word, not as a step", async () => {
        const base = mockFull()
        handleFull(mockFull({ transition_states: [{ ...base.transition_states[0], status: "rejected" }] }))
        const { container } = page()
        await screen.findByText("kin_test1")
        const stages = container.querySelector(".reaction-ts-stages") as HTMLElement
        expect(stages).toHaveTextContent("rejected")
        expect(stages.querySelectorAll("li")).toHaveLength(0)
    })
})

describe("ReactionEntryPage -- the compare link", () => {
    it("offers the reaction's compare page from two rate records", async () => {
        const one = (mockFull().kinetics as Array<Record<string, unknown>>)[0]
        handleFull(mockFull({ kinetics: [one, { ...one, kinetics_ref: "kin_test2" }] }))
        page()
        expect(await screen.findByRole("link", { name: "Compare these values" })).toHaveAttribute("href", "/reactions/rxn_test1/compare")
    })

    it("offers no compare link from one rate record", async () => {
        handleFull(mockFull())
        page()
        await screen.findByRole("heading", { name: "Numbers for this entry" })
        expect(screen.queryByRole("link", { name: "Compare these values" })).not.toBeInTheDocument()
    })
})
