import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { seedFiltersFromUrl } from "../api/browseApi"

// ---------------------------------------------------------------------------
// Fixtures. Two DISTINCT rows per kind, each with different chemistry/
// evidence, so a component that reads `records[0]` for every row (the exact
// mutation the design brief calls out) fails on the SECOND row's assertions
// -- checking only the first row would let that bug through. `twoSpecies`
// additionally gives its first record a SECOND entry with a different
// electronic state and review status, and `twoTs` gives its two records
// different charge/multiplicity and independently-flipped evidence flags --
// otherwise every field a row renders would be identical across fixtures,
// and a hardcoded literal or a swapped flag label would stay green.
// ---------------------------------------------------------------------------

function speciesRecord(overrides: {
    speciesRef: string
    formula: string | null
    smiles: string
    charge: number
    multiplicity: number
    entryRef: string
    entries?: { entryRef: string; electronicState?: string; reviewStatus?: string }[]
}) {
    const entries = overrides.entries ?? [{ entryRef: overrides.entryRef }]
    return {
        species_ref: overrides.speciesRef,
        canonical_smiles: overrides.smiles,
        inchi_key: `${overrides.speciesRef.toUpperCase()}-KEY`,
        formula: overrides.formula,
        charge: overrides.charge,
        multiplicity: overrides.multiplicity,
        stereo_kind: "achiral",
        entries: entries.map((entry) => ({
            species_entry_ref: entry.entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: entry.electronicState ?? "ground",
            review: { status: entry.reviewStatus ?? "not_reviewed", reviewed_at: null, reviewer_kind: null },
            availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: 4 },
        })),
    }
}

function speciesEnvelope(offset: number, limit: number, allRecords: ReturnType<typeof speciesRecord>[]) {
    const page = allRecords.slice(offset, offset + limit)
    return {
        request: { profile: "exploratory", profile_recommendation: "none", profile_release_ref: null, filter: {}, sort: "review_rank,has_entries,created_at,id", collapse: "all", include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: allRecords.length, deprecated: 0, rejected: 0, total: allRecords.length },
        records: page,
        pagination: { offset, limit, returned: page.length, total: allRecords.length, post_collapse_total: allRecords.length },
    }
}

function tsRecord(overrides: {
    tsEntryRef: string
    reactionRef: string
    equation: string | null
    family: string | null
    label: string
    hasIrc: boolean
    charge?: number
    multiplicity?: number
    hasGeometryValidation?: boolean
    hasScfStability?: boolean
}) {
    return {
        transition_state_entry: {
            transition_state_entry_ref: overrides.tsEntryRef,
            charge: overrides.charge ?? 0, multiplicity: overrides.multiplicity ?? 2, status: "optimized", unmapped_smiles: null,
            created_at: "2026-08-05T14:04:16.914780",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        transition_state: {
            transition_state_ref: `ts_${overrides.tsEntryRef}`, label: overrides.label, note: null,
            created_at: "2026-08-05T14:04:16.914780",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        reaction: {
            reaction_ref: overrides.reactionRef, reaction_entry_ref: `rxe_${overrides.reactionRef}`,
            equation: overrides.equation, reversible: true, family: overrides.family,
        },
        evidence_summary: {
            calculation_count: 4, has_opt: true, has_freq: true, has_sp: true, has_irc: overrides.hasIrc,
            has_path_search: false,
            has_geometry_validation: overrides.hasGeometryValidation ?? false,
            has_scf_stability: overrides.hasScfStability ?? false,
            levels_of_theory: {},
        },
        validation: { irc: "absent" },
        available_sections: { has_entries: true, has_calculations: true, has_geometries: true, has_review: true, has_validation_evidence: false },
    }
}

function tsEnvelope(offset: number, limit: number, allRecords: ReturnType<typeof tsRecord>[]) {
    const page = allRecords.slice(offset, offset + limit)
    return {
        request: { profile: "exploratory", profile_recommendation: "none", profile_release_ref: null, filter: {}, sort: "review_rank,created_at,id", include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: allRecords.length, deprecated: 0, rejected: 0, total: allRecords.length },
        records: page,
        pagination: { offset, limit, returned: page.length, total: allRecords.length, post_collapse_total: allRecords.length },
    }
}

const twoSpecies = [
    speciesRecord({
        speciesRef: "spc_benzene", formula: "C6H6", smiles: "c1ccccc1", charge: 0, multiplicity: 1, entryRef: "spe_benzene",
        entries: [
            { entryRef: "spe_benzene_ground", electronicState: "ground", reviewStatus: "not_reviewed" },
            { entryRef: "spe_benzene_excited", electronicState: "excited", reviewStatus: "approved" },
        ],
    }),
    speciesRecord({ speciesRef: "spc_methyl", formula: "CH3", smiles: "[CH3]", charge: 0, multiplicity: 2, entryRef: "spe_methyl" }),
]

const twoTs = [
    tsRecord({
        tsEntryRef: "tse_one", reactionRef: "rxn_one", equation: "A <=> B", family: "R_Addition_MultipleBond", label: "TS0",
        hasIrc: true, charge: 0, multiplicity: 2, hasGeometryValidation: true, hasScfStability: false,
    }),
    tsRecord({
        tsEntryRef: "tse_two", reactionRef: "rxn_two", equation: "C <=> D + [H]", family: "H_Abstraction", label: "TS1",
        hasIrc: false, charge: -1, multiplicity: 1, hasGeometryValidation: false, hasScfStability: true,
    }),
]

function manySpeciesRecords(count: number) {
    return Array.from({ length: count }, (_, index) =>
        speciesRecord({ speciesRef: `spc_p${index}`, formula: `P${index}`, smiles: `Page${index}Smiles`, charge: 0, multiplicity: 1, entryRef: `spe_p${index}` }))
}

type HandlerOptions = {
    speciesRecords?: ReturnType<typeof speciesRecord>[]
    tsRecords?: ReturnType<typeof tsRecord>[]
    speciesStatus?: number
    speciesErrorBody?: unknown
    captureSpeciesUrl?: (url: URL) => void
    captureTsUrl?: (url: URL) => void
}

// This page's OWN tests don't exercise the vocabulary dropdowns
// (`BrowseFilterForm.test.tsx` does) -- but switching to "Transition
// state" mounts `EvidenceFields`, which fires the four unscoped `/meta/*`
// fetches unconditionally. Without a registered handler, this file's
// `onUnhandledRequest: "error"` server would fail every one of the
// several existing tests that click the "Transition state" radio, for a
// reason that has nothing to do with what those tests assert. Empty
// results are enough -- they only need to not blow up the listing.
function emptyVocabHandlers() {
    return ["methods", "basis-sets", "software", "workflow-tools", "software-versions", "workflow-tool-versions"].map((path) =>
        http.get(`/api/v1/scientific/meta/${path}`, () => HttpResponse.json({ results: [] })))
}

function handlers(options: HandlerOptions = {}) {
    const speciesAll = options.speciesRecords ?? twoSpecies
    const tsAll = options.tsRecords ?? twoTs
    return [
        ...emptyVocabHandlers(),
        http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const url = new URL(request.url)
            options.captureSpeciesUrl?.(url)
            if (options.speciesStatus) {
                return HttpResponse.json(options.speciesErrorBody ?? { detail: "archive unavailable" }, { status: options.speciesStatus })
            }
            // The live vdW filter genuinely returns zero rows today (measured:
            // 60 minimum entries, 0 vdw_complex) -- mirrored here rather than
            // invented, so the fixture matches the real archive shape this
            // page is built against. Zero regardless of the widening flags
            // (include_rejected/include_deprecated): the archive genuinely
            // holds none, so those flags cannot change that.
            if (url.searchParams.get("species_entry_kind") === "vdw_complex") {
                return HttpResponse.json(speciesEnvelope(0, 50, []))
            }
            const offset = Number(url.searchParams.get("offset") ?? "0")
            const limit = Number(url.searchParams.get("limit") ?? "20")
            return HttpResponse.json(speciesEnvelope(offset, limit, speciesAll))
        }),
        http.get("/api/v1/scientific/transition-states/browse", ({ request }) => {
            const url = new URL(request.url)
            options.captureTsUrl?.(url)
            const offset = Number(url.searchParams.get("offset") ?? "0")
            const limit = Number(url.searchParams.get("limit") ?? "20")
            return HttpResponse.json(tsEnvelope(offset, limit, tsAll))
        }),
    ]
}

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/"); vi.useRealTimers() })
afterAll(() => server.close())

function renderAt(path: string) {
    window.history.replaceState({}, "", path)
    render(<App />)
}

// `seedFiltersFromUrl` is the ONE place initial-URL params become filter
// state -- unit-tested directly here (not just through the end-to-end
// tests below) so a broken kind branch or a `.get` regression (silently
// keeping only the first of a repeated param) is caught at the smallest
// possible unit, before it ever reaches a rendered page.
describe("seedFiltersFromUrl: reads the initial URL into filter state, per kind", () => {
    it("transition_state: reads participant_smiles", () => {
        expect(seedFiltersFromUrl("transition_state", new URLSearchParams("participant_smiles=CCO"))).toEqual({
            participantSmiles: "CCO",
        })
    })

    it("transition_state: no participant_smiles in the URL seeds an empty string, not undefined", () => {
        expect(seedFiltersFromUrl("transition_state", new URLSearchParams())).toEqual({ participantSmiles: "" })
    })

    // Gap found post-hoc (front-page reaction search's "See all N reactions
    // involving X" link): a REPEATED param must seed ALL values, comma-
    // joined into this filter's own on-the-wire shape -- `.get` alone would
    // silently keep only the first and understate what the link promised.
    //
    // Every reaction-kind case below also carries `direction: ""` in its
    // expected shape (PR #418's own follow-up): a link that carries
    // structures but no `direction` must seed the backend's own default
    // ("" -- omitted on the next request, per `buildReactionBrowseQuery`),
    // not silently drop the field from the seeded object.
    it("reaction: a single reactant_smiles/product_smiles seeds one token each, and no direction", () => {
        expect(seedFiltersFromUrl("reaction", new URLSearchParams("reactant_smiles=NN&product_smiles=C"))).toEqual({
            reactantSmiles: "NN", productSmiles: "C", direction: "",
        })
    })

    it("reaction: a REPEATED reactant_smiles seeds BOTH values, comma-joined -- not just the first", () => {
        const params = new URLSearchParams()
        params.append("reactant_smiles", "NN")
        params.append("reactant_smiles", "[H]")
        expect(seedFiltersFromUrl("reaction", params)).toEqual({ reactantSmiles: "NN,[H]", productSmiles: "", direction: "" })
    })

    it("reaction: a repeated product_smiles seeds both values too, independently of reactant_smiles", () => {
        const params = new URLSearchParams()
        params.append("product_smiles", "C")
        params.append("product_smiles", "[OH]")
        expect(seedFiltersFromUrl("reaction", params)).toEqual({ reactantSmiles: "", productSmiles: "C,[OH]", direction: "" })
    })

    it("reaction: an empty-value param (?reactant_smiles=) seeds nothing -- 'present but blank' means unfiltered, same as the backend's own contract", () => {
        expect(seedFiltersFromUrl("reaction", new URLSearchParams("reactant_smiles="))).toEqual({
            reactantSmiles: "", productSmiles: "", direction: "",
        })
    })

    it("reaction: no reactant_smiles/product_smiles at all seeds empty strings for both, and no direction", () => {
        expect(seedFiltersFromUrl("reaction", new URLSearchParams())).toEqual({
            reactantSmiles: "", productSmiles: "", direction: "",
        })
    })

    // PR #418 follow-up: a link built with an explicit `direction` (e.g.
    // `?reactant_smiles=NN&direction=either`, the shape a future "reactions
    // involving X, either side" linker would use) must seed that value into
    // the form -- a shared link that carries structures but silently loses
    // `direction` would return a DIFFERENT result set from the one the
    // sender saw, which is exactly the gap this branch closes.
    it("reaction: an explicit direction param is seeded verbatim", () => {
        expect(seedFiltersFromUrl("reaction", new URLSearchParams("reactant_smiles=NN&direction=either"))).toEqual({
            reactantSmiles: "NN", productSmiles: "", direction: "either",
        })
    })

    it("reaction: species/vdw/transition_state never see a direction param leak in", () => {
        const params = new URLSearchParams("participant_smiles=CCO&direction=either")
        expect(seedFiltersFromUrl("species", params)).toEqual({})
        expect(seedFiltersFromUrl("vdw", params)).toEqual({})
        expect(seedFiltersFromUrl("transition_state", params)).not.toHaveProperty("direction")
    })

    // A reaction-kind linker's params must not leak into an unrelated kind,
    // and vice versa (mirrors the pre-existing `participant_smiles`
    // species-kind test, end to end, below).
    it("species/vdw: seeds nothing, even if the URL happens to carry a transition_state/reaction param", () => {
        const params = new URLSearchParams("participant_smiles=CCO&reactant_smiles=NN")
        expect(seedFiltersFromUrl("species", params)).toEqual({})
        expect(seedFiltersFromUrl("vdw", params)).toEqual({})
    })

    it("reaction: does not seed participant_smiles even if present in the URL -- that param belongs to transition_state only", () => {
        const seed = seedFiltersFromUrl("reaction", new URLSearchParams("participant_smiles=CCO"))
        expect(seed).not.toHaveProperty("participantSmiles")
    })
})

// The kind switcher (`BrowseKindSelector.tsx`, demoted from a radiogroup to
// a plain link list) shares its own kind's label with the top nav's
// "Species"/"Reactions" links -- `screen.getByRole("link", { name:
// "Species" })` throws "found multiple elements" whenever the page also
// shows the switcher's own "Species" link. Scoped to
// `nav[aria-label="Browse a different kind"]` so every kind-switch click in
// this file resolves the SWITCHER's link, not the top nav's, even on the
// one kind (species) where the two labels collide.
async function clickKindLink(user: ReturnType<typeof userEvent.setup>, name: string) {
    const switcher = screen.getByRole("navigation", { name: "Browse a different kind" })
    await user.click(within(switcher).getByRole("link", { name }))
}

describe("browse page: kind selection queries the right endpoint with the right parameters", () => {
    it("defaults to species at /species, hitting /species/browse with species_entry_kind=minimum", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { capturedUrl = url } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        expect(capturedUrl?.searchParams.get("species_entry_kind")).toBe("minimum")
        expect(window.location.pathname).toBe("/species")
        // The kind switcher no longer marks a "checked" option (it demoted
        // from a radiogroup to a plain link list, `BrowseKindSelector.tsx`)
        // -- the page's own heading is the honest way to assert which kind
        // is showing now (`BROWSE_KIND_CONTENT`, `api/browseApi.ts`).
        expect(screen.getByRole("heading", { name: "Browse species" })).toBeVisible()
        // And the switcher itself must not link back to the kind you are
        // already on -- scoped to `nav[aria-label="Browse a different
        // kind"]` since the top nav also carries an unrelated "Species" link.
        expect(within(screen.getByRole("navigation", { name: "Browse a different kind" })).queryByRole("link", { name: "Species" })).not.toBeInTheDocument()
    })

    it("selecting 'Van der Waals complex' NAVIGATES to /vdw-complexes and queries /species/browse with species_entry_kind=vdw_complex, not the TS endpoint", async () => {
        const user = userEvent.setup()
        let speciesCalls = 0
        let tsCalls = 0
        let lastKindParam: string | null = null
        server.use(...handlers({
            captureSpeciesUrl: (url) => { speciesCalls += 1; lastKindParam = url.searchParams.get("species_entry_kind") },
            captureTsUrl: () => { tsCalls += 1 },
        }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Van der Waals complex")
        await waitFor(() => expect(lastKindParam).toBe("vdw_complex"))
        expect(speciesCalls).toBeGreaterThan(0)
        expect(tsCalls).toBe(0)
        await waitFor(() => expect(window.location.pathname).toBe("/vdw-complexes"))
    })

    it("selecting 'Transition state' NAVIGATES to /transition-states and queries /transition-states/browse, never /species/browse again", async () => {
        const user = userEvent.setup()
        let tsCalls = 0
        let speciesCallsAfterSwitch = 0
        server.use(...handlers({ captureTsUrl: () => { tsCalls += 1 } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        server.use(http.get("/api/v1/scientific/species/browse", () => { speciesCallsAfterSwitch += 1; return HttpResponse.json(speciesEnvelope(0, 20, twoSpecies)) }))
        await clickKindLink(user, "Transition state")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        await waitFor(() => expect(tsCalls).toBeGreaterThan(0))
        expect(speciesCallsAfterSwitch).toBe(0)
        await waitFor(() => expect(window.location.pathname).toBe("/transition-states"))
    })

    // Since `/vdw-complexes` and `/transition-states` have no dedicated top
    // nav link, this is the only place their DIRECT navigation (not via the
    // kind selector) is exercised end to end -- a stale bookmark, a shared
    // link, or a browser reload on one of these paths.
    it("loading /vdw-complexes directly renders the vdW kind and hits /species/browse with species_entry_kind=vdw_complex", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { capturedUrl = url } }))
        renderAt("/vdw-complexes")
        await waitFor(() => expect(capturedUrl?.searchParams.get("species_entry_kind")).toBe("vdw_complex"))
        expect(screen.getByRole("heading", { name: "Browse van der Waals complexes" })).toBeVisible()
        expect(window.location.pathname).toBe("/vdw-complexes")
    })

    it("loading /transition-states directly renders the transition-state kind and hits /transition-states/browse", async () => {
        server.use(...handlers())
        renderAt("/transition-states")
        expect(await screen.findByRole("heading", { name: "Browse transition states" })).toBeVisible()
        expect(await screen.findByText("A <=> B")).toBeVisible()
        expect(window.location.pathname).toBe("/transition-states")
    })

    // An unrecognised `?kind=` on `/species` is the one case that keeps
    // TODAY's pre-per-path fallback: `/species` is already the default
    // kind's own path, so there is nothing to redirect TO -- the page just
    // renders species, and the stray query string is left alone (unlike a
    // RECOGNISED non-default kind, which redirects away entirely; see the
    // "legacy ?kind= redirect" describe block below).
    it("an unrecognised ?kind= on /species renders species without redirecting anywhere", async () => {
        server.use(...handlers())
        renderAt("/species?kind=nonsense")
        await screen.findByText(/records · showing/)
        expect(screen.getByRole("heading", { name: "Browse species" })).toBeVisible()
        expect(window.location.pathname).toBe("/species")
    })

    // Item 5's deep link (`SpeciesEntrySummary.tsx`'s "Transition states for
    // reactions of this species") relies on this: `?participant_smiles=`
    // must be seeded into `filters` on the initial mount, not silently
    // dropped the way every OTHER filter field already is. The link itself
    // now points straight at the canonical `/transition-states` path (not
    // the legacy `/species?kind=transition_state`), so that is what this
    // test exercises. Asserted two ways: the SMILES field actually shows
    // the seeded value (proves the FORM state was seeded, not just the
    // outgoing request), and the outgoing `/transition-states/browse`
    // request carries `participant_smiles` (proves the seed actually
    // reaches the filter, not just the input's local display).
    it("?participant_smiles= on the initial URL seeds the SMILES filter field and the outgoing request", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureTsUrl: (url) => { capturedUrl = url } }))
        renderAt("/transition-states?participant_smiles=CCO")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        expect(screen.getByLabelText("SMILES")).toHaveValue("CCO")
        await waitFor(() => expect(capturedUrl?.searchParams.get("participant_smiles")).toBe("CCO"))
    })

    it("omits participant_smiles from the outgoing request when the URL carries none (no accidental seeding)", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureTsUrl: (url) => { capturedUrl = url } }))
        renderAt("/transition-states")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        expect(screen.getByLabelText("SMILES")).toHaveValue("")
        await waitFor(() => expect(capturedUrl).toBeDefined())
        expect(capturedUrl?.searchParams.has("participant_smiles")).toBe(false)
    })

    it("does NOT seed participant_smiles when the initial kind is species (the param is transition-state-only)", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        renderAt("/species?participant_smiles=CCO")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Transition state")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        // Switching to transition_state AFTER mount does not retroactively
        // apply a species-kind URL's participant_smiles -- the seed is
        // read once, on mount, for whatever kind was resolved then.
        expect(screen.getByLabelText("SMILES")).toHaveValue("")
    })

    // Mutation-shaped regression: if BOTH kinds pointed at the same endpoint,
    // the vdW selection above would still render *something*, but the
    // species_entry_kind assertion would fail -- this is that exact check,
    // isolated.
    it("MUTATION CHECK: species and vdW selections hit the SAME endpoint with DIFFERENT species_entry_kind values", async () => {
        const user = userEvent.setup()
        const kinds: (string | null)[] = []
        server.use(...handlers({ captureSpeciesUrl: (url) => kinds.push(url.searchParams.get("species_entry_kind")) }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Van der Waals complex")
        await waitFor(() => expect(kinds).toContain("vdw_complex"))
        expect(kinds).toContain("minimum")
    })

    it("sends collapse=all explicitly on the species/vdW query rather than relying on the server default", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { capturedUrl = url } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        expect(capturedUrl?.searchParams.get("collapse")).toBe("all")
    })
})

// Every browse link before this change pointed at `/species?kind=...` --
// `/species` is the one path that still interprets that legacy query
// parameter, so an old bookmark/shared link keeps landing on the right
// content. Each case redirects (REPLACE, not push -- the Back button must
// not bounce through the stale `/species?kind=...` URL) to the resolved
// kind's own path, carrying every OTHER query parameter forward untouched.
describe("browse page: legacy /species?kind= redirects to the kind's own path", () => {
    it("?kind=vdw redirects (replace) to /vdw-complexes, preserving two other filter params, with no extra history entry", async () => {
        server.use(...handlers())
        const historyLengthBefore = window.history.length
        renderAt("/species?kind=vdw&formula=C6H6&charge=0")
        await waitFor(() => expect(window.location.pathname).toBe("/vdw-complexes"))
        const search = new URLSearchParams(window.location.search)
        expect(search.get("formula")).toBe("C6H6")
        expect(search.get("charge")).toBe("0")
        expect(search.has("kind")).toBe(false)
        // `replace` navigation must not grow history -- a `push` here would
        // let Back bounce the reader through the dead `?kind=` URL.
        expect(window.history.length).toBe(historyLengthBefore)
    })

    it("?kind=transition_state redirects (replace) to /transition-states, preserving participant_smiles", async () => {
        server.use(...handlers())
        const historyLengthBefore = window.history.length
        renderAt("/species?kind=transition_state&participant_smiles=CCO")
        await waitFor(() => expect(window.location.pathname).toBe("/transition-states"))
        expect(new URLSearchParams(window.location.search).get("participant_smiles")).toBe("CCO")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        expect(screen.getByLabelText("SMILES")).toHaveValue("CCO")
        expect(window.history.length).toBe(historyLengthBefore)
    })

    it("?kind=reaction redirects (replace) to /reactions, preserving the family filter", async () => {
        server.use(...handlers(), reactionHandler(), reactionFamilyVocabHandler())
        const historyLengthBefore = window.history.length
        renderAt("/species?kind=reaction&family=H_Abstraction")
        await waitFor(() => expect(window.location.pathname).toBe("/reactions"))
        expect(new URLSearchParams(window.location.search).get("family")).toBe("H_Abstraction")
        expect(window.history.length).toBe(historyLengthBefore)
    })

    it("?kind=species (already the default) does not redirect anywhere", async () => {
        server.use(...handlers())
        renderAt("/species?kind=species&formula=C6H6")
        await screen.findByText(/records · showing/)
        expect(window.location.pathname).toBe("/species")
        expect(new URLSearchParams(window.location.search).get("formula")).toBe("C6H6")
    })

    it("no ?kind= at all does not redirect anywhere", async () => {
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        expect(window.location.pathname).toBe("/species")
    })
})

describe("browse page: switching kinds preserves shared filters and drops inapplicable ones", () => {
    it("charge (shared) survives a switch to Transition state; formula (species-only) is dropped from both the request and the form", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        let lastTsUrl: URL | undefined
        server.use(...handlers({
            captureSpeciesUrl: (url) => { lastSpeciesUrl = url },
            captureTsUrl: (url) => { lastTsUrl = url },
        }))
        renderAt("/species")
        await screen.findByText(/records · showing/)

        await user.type(screen.getByLabelText("Charge"), "0")
        await user.type(screen.getByLabelText("Formula"), "C6H6")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.get("formula")).toBe("C6H6"))
        expect(lastSpeciesUrl?.searchParams.get("charge")).toBe("0")

        await clickKindLink(user, "Transition state")
        await waitFor(() => expect(lastTsUrl).toBeDefined())
        expect(lastTsUrl?.searchParams.get("charge")).toBe("0") // shared filter carried over
        expect(lastTsUrl?.searchParams.has("formula")).toBe(false) // inapplicable filter dropped from the request
        expect(screen.queryByLabelText("Formula")).not.toBeInTheDocument() // and from the form itself
        expect(screen.getByLabelText("Charge")).toHaveValue("0")
    })

    // A genuinely transition-state-only field (`status` -- `/species/browse`
    // accepts no such param, see `EvidenceFields`'s doc comment) does NOT
    // survive switching back to Species: both from the request and from
    // the form itself.
    it("a Transition-state-only filter (Status) does not survive switching back to Species", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { lastSpeciesUrl = url } }))
        renderAt("/transition-states")
        await screen.findByText("A <=> B")

        await user.selectOptions(screen.getByLabelText("Status"), "optimized")
        await waitFor(() => expect(screen.getByLabelText("Status")).toHaveValue("optimized"))

        await clickKindLink(user, "Species")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.has("species_entry_kind")).toBe(true))
        expect(lastSpeciesUrl?.searchParams.has("status")).toBe(false)
        expect(screen.queryByLabelText("Status")).not.toBeInTheDocument()
    })

    // Method is one of the six PROVENANCE fields, which apply to every
    // kind (unlike Status/`has_*` above) -- so it is the opposite case:
    // it must survive a kind switch, in BOTH directions, staying both in
    // the form's own state (asserted via the select's value, since the
    // field also stays mounted on both kinds) and on the outgoing request.
    it("a shared provenance filter (Method) SURVIVES switching kinds, in both directions", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        let lastTsUrl: URL | undefined
        server.use(
            http.get("/api/v1/scientific/meta/methods", () => HttpResponse.json({ results: [{ value: "b3lyp", count: 5 }] })),
            ...handlers({
                captureSpeciesUrl: (url) => { lastSpeciesUrl = url },
                captureTsUrl: (url) => { lastTsUrl = url },
            }),
        )
        renderAt("/transition-states")
        await screen.findByText("A <=> B")

        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(2))
        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        await waitFor(() => expect(lastTsUrl?.searchParams.get("method")).toBe("b3lyp"))

        await clickKindLink(user, "Species")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.get("method")).toBe("b3lyp"))
        expect(screen.getByLabelText("Method")).toHaveValue("b3lyp")

        await clickKindLink(user, "Transition state")
        await waitFor(() => expect(lastTsUrl?.searchParams.get("method")).toBe("b3lyp"))
        expect(screen.getByLabelText("Method")).toHaveValue("b3lyp")
    })

    // The species->TS direction was the only one previously asserted, and
    // three params (include_rejected, include_deprecated, min_review_status)
    // were never checked on the wire at all in EITHER direction -- deleting
    // the first two, or misnaming the third, was invisible to the suite.
    it("every shared filter, including include_rejected/include_deprecated/min_review_status, is sent on the wire and survives BOTH switch directions", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        let lastTsUrl: URL | undefined
        server.use(...handlers({
            captureSpeciesUrl: (url) => { lastSpeciesUrl = url },
            captureTsUrl: (url) => { lastTsUrl = url },
        }))
        renderAt("/transition-states")
        await screen.findByText("A <=> B")

        await user.type(screen.getByLabelText("Charge"), "0")
        await user.type(screen.getByLabelText("Multiplicity"), "2")
        await user.selectOptions(screen.getByLabelText("Minimum review status"), "approved")
        await user.click(screen.getByLabelText("Include rejected"))
        await user.click(screen.getByLabelText("Include deprecated"))
        await waitFor(() => expect(lastTsUrl?.searchParams.get("include_deprecated")).toBe("true"))
        expect(lastTsUrl?.searchParams.get("charge")).toBe("0")
        expect(lastTsUrl?.searchParams.get("multiplicity")).toBe("2")
        expect(lastTsUrl?.searchParams.get("min_review_status")).toBe("approved")
        expect(lastTsUrl?.searchParams.get("include_rejected")).toBe("true")

        // TS -> species direction: every shared value above must survive too.
        await clickKindLink(user, "Species")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.get("include_deprecated")).toBe("true"))
        expect(lastSpeciesUrl?.searchParams.get("charge")).toBe("0")
        expect(lastSpeciesUrl?.searchParams.get("multiplicity")).toBe("2")
        expect(lastSpeciesUrl?.searchParams.get("min_review_status")).toBe("approved")
        expect(lastSpeciesUrl?.searchParams.get("include_rejected")).toBe("true")
    })
})

describe("browse page: the four empty/failure states are distinguishable", () => {
    it("reads 'no records deposited' for van der Waals complexes with no filters applied -- an archive fact, not a broken search", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Van der Waals complex")
        expect(await screen.findByText(/No van der Waals complexes have been deposited in this archive yet/)).toBeVisible()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    // Reproduces the review's exact finding: a WIDENING toggle (Include
    // rejected) must never flip an archive-empty listing into a
    // filtered-empty one -- nothing was filtered, the archive holds zero.
    it("ticking a widening filter (Include rejected) on an empty kind keeps the archive-empty message, not the filtered-empty one", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Van der Waals complex")
        expect(await screen.findByText(/No van der Waals complexes have been deposited in this archive yet/)).toBeVisible()

        await user.click(screen.getByLabelText("Include rejected"))
        expect(await screen.findByText(/No van der Waals complexes have been deposited in this archive yet/)).toBeVisible()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
    })

    it("reads 'no records match these filters' when a filter genuinely narrows a nonzero corpus to zero", async () => {
        const user = userEvent.setup()
        // Unlike a handler that is unconditionally empty, this one holds
        // real records until `formula` is set -- so this test exercises an
        // actual narrowing, the gap the original fixture (`speciesRecords:
        // []`, empty regardless of the filter) let through.
        server.use(http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const url = new URL(request.url)
            const offset = Number(url.searchParams.get("offset") ?? "0")
            const limit = Number(url.searchParams.get("limit") ?? "20")
            const rows = url.searchParams.get("formula") ? [] : twoSpecies
            return HttpResponse.json(speciesEnvelope(offset, limit, rows))
        }), http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(tsEnvelope(0, 20, twoTs))))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await user.type(screen.getByLabelText("Formula"), "Xx999")
        expect(await screen.findByText(/No species records match these filters/)).toBeVisible()
        expect(screen.queryByText(/have been deposited in this archive yet/)).not.toBeInTheDocument()
    })

    it("reads a failure message, with role=alert, when the request errors with a 5xx -- distinct from either empty state", async () => {
        server.use(...handlers({ speciesStatus: 503 }))
        renderAt("/species")
        expect(await screen.findByRole("alert")).toHaveTextContent("The archive service could not load this listing. Try again later.")
        expect(screen.queryByText(/deposited in this archive yet/)).not.toBeInTheDocument()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
    })

    // Review follow-up (SHOULD-FIX #2): `useBrowse` used to collapse a
    // double 429 (both automatic-retry attempts exhausted) into the same
    // generic "archive service could not load this listing" as a real
    // 5xx. Distinct state, plain-language wording -- same as every other
    // rate-limited surface.
    it("reads a distinct, plain-language message for a double 429, not the generic 5xx wording", async () => {
        vi.useFakeTimers()
        server.use(http.get("/api/v1/scientific/species/browse", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "15" } })
        )))
        renderAt("/species")
        await act(async () => { await vi.advanceTimersByTimeAsync(200) }) // useBrowse's own request debounce
        await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // first (429) attempt lands
        await act(async () => { await vi.advanceTimersByTimeAsync(15_000) }) // the retry, also 429

        const message = screen.getByRole("alert")
        expect(message).toHaveTextContent(
            "The archive is receiving too many requests right now. Wait about 15 seconds and reload the page.",
        )
        expect(message.textContent).not.toMatch(/\d+s\b/)
        expect(message.textContent).not.toMatch(/could not load this listing/)
    })

    // Reproduces the review's second finding under item 1: a nonzero total
    // with zero returned records (paging past the end) is neither
    // "nothing deposited" nor "filters excluded everything".
    it("reads its own message, not archive-empty or filtered-empty, when a page comes back empty with a positive pagination.total", async () => {
        const user = userEvent.setup()
        server.use(http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const offset = Number(new URL(request.url).searchParams.get("offset") ?? "0")
            if (offset === 0) {
                return HttpResponse.json({
                    ...speciesEnvelope(0, 20, twoSpecies),
                    pagination: { offset: 0, limit: 20, returned: 2, total: 25, post_collapse_total: 25 },
                })
            }
            return HttpResponse.json({
                ...speciesEnvelope(offset, 20, []),
                pagination: { offset, limit: 20, returned: 0, total: 25, post_collapse_total: 25 },
            })
        }), http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(tsEnvelope(0, 20, twoTs))))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText(/That is past the end of the species records this listing has/)).toBeVisible()
        expect(screen.queryByText(/have been deposited in this archive yet/)).not.toBeInTheDocument()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
    })

    // Mutation-shaped regression: collapsing the archive-empty and
    // filtered-empty branches into one shared message would make these two
    // strings identical -- guard that they are not.
    it("MUTATION CHECK: the archive-empty and filtered-empty messages are not the same string", async () => {
        const user = userEvent.setup()
        server.use(http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const url = new URL(request.url)
            const offset = Number(url.searchParams.get("offset") ?? "0")
            const limit = Number(url.searchParams.get("limit") ?? "20")
            const rows = url.searchParams.get("formula") ? [] : twoSpecies
            return HttpResponse.json(speciesEnvelope(offset, limit, rows))
        }), http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(tsEnvelope(0, 20, twoTs))))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await user.type(screen.getByLabelText("Formula"), "Xx999")
        const filteredMessage = (await screen.findByText(/No species records match these filters/)).textContent
        cleanup()
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Van der Waals complex")
        const archiveMessage = (await screen.findByText(/No van der Waals complexes have been deposited/)).textContent
        expect(filteredMessage).not.toBe(archiveMessage)
    })
})

describe("browse page: the coded error contract is respected, not collapsed into one outage message", () => {
    it("a 422 surfaces the archive's own validation reason, including the list form of `detail`, instead of the generic outage copy", async () => {
        server.use(...handlers({
            speciesStatus: 422,
            speciesErrorBody: {
                code: "request_validation_error",
                detail: [{ type: "int_parsing", loc: ["query", "charge"], msg: "Input should be a valid integer, unable to parse string as an integer", input: "abc" }],
            },
        }))
        renderAt("/species")
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent("charge: Input should be a valid integer")
        expect(alert).not.toHaveTextContent("Try again later")
    })

    it("a 200 response that fails schema validation gets its own 'malformed' copy, not the outage copy", async () => {
        server.use(
            http.get("/api/v1/scientific/species/browse", () => HttpResponse.json({ this: "does not match the schema" })),
            http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(tsEnvelope(0, 20, twoTs))),
        )
        renderAt("/species")
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent("could not be validated")
        expect(alert).not.toHaveTextContent("Try again later")
    })

    // Reproduces the review's exact repro: "-" is the first keystroke of
    // any anion charge, and is not yet a valid integer. It must never reach
    // the wire (which would 422) or be reported as an outage.
    it("typing '-' into Charge never sends an incomplete integer, and completing it to '-1' does", async () => {
        const user = userEvent.setup()
        let lastUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { lastUrl = url } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)

        await user.type(screen.getByLabelText("Charge"), "-")
        await waitFor(() => expect(screen.getByLabelText("Charge")).toHaveValue("-"))
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
        expect(lastUrl?.searchParams.has("charge")).toBe(false)

        await user.type(screen.getByLabelText("Charge"), "1")
        await waitFor(() => expect(lastUrl?.searchParams.get("charge")).toBe("-1"))
    })
})

describe("browse page: a species row and a TS row each render their OWN fields", () => {
    it("renders both species rows with their own distinct SMILES, charge/spin, and per-entry state/review -- not the first row's data repeated", async () => {
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        const rows = document.querySelectorAll(".species-browse-row")
        expect(rows).toHaveLength(2)
        const rowOne = rows[0] as HTMLElement
        const rowTwo = rows[1] as HTMLElement

        expect(within(rowOne).getByText("c1ccccc1")).toBeVisible()
        expect(within(rowOne).getByText(/charge 0 · spin singlet/)).toBeVisible()
        expect(within(rowOne).queryByText("[CH3]")).not.toBeInTheDocument()
        // Benzene's two entries differ in BOTH electronic state and review
        // status -- a component that hardcoded "minimum · ground" and
        // "not reviewed" for every entry chip would fail on the second one.
        expect(within(rowOne).getByText("minimum · ground")).toBeVisible()
        expect(within(rowOne).getByText("not reviewed")).toBeVisible()
        expect(within(rowOne).getByText("minimum · excited")).toBeVisible()
        expect(within(rowOne).getByText("approved")).toBeVisible()

        expect(within(rowTwo).getByText("[CH3]")).toBeVisible()
        expect(within(rowTwo).getByText(/charge 0 · spin doublet/)).toBeVisible()
        expect(within(rowTwo).queryByText("c1ccccc1")).not.toBeInTheDocument()
    })

    it("renders review status in a SEPARATE pill from the kind/state classification, never sharing one box", async () => {
        // The owner's report, reproduced: "NOT REVIEWED is part of the same
        // pill as minimum.ground which should not be so." Both facts must
        // still appear exactly once each, but the review-status pill must
        // NOT be a descendant of the same `.value-pill` element the
        // kind/state pill is.
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        const rowOne = document.querySelectorAll(".species-browse-row")[0] as HTMLElement

        const kindStatePill = within(rowOne).getByText("minimum · ground").closest(".value-pill") as HTMLElement
        expect(kindStatePill).toBeTruthy()
        const reviewPill = within(rowOne).getByText("not reviewed").closest(".value-pill") as HTMLElement
        expect(reviewPill).toBeTruthy()

        // The DOM relationship under test: review status's own pill is not
        // an ancestor OR descendant of the kind/state pill -- two sibling
        // elements, not one shared box.
        expect(kindStatePill).not.toBe(reviewPill)
        expect(kindStatePill.contains(reviewPill)).toBe(false)
        expect(reviewPill.contains(kindStatePill)).toBe(false)

        // Both values still appear EXACTLY once on this chip.
        expect(within(rowOne).getAllByText("minimum · ground")).toHaveLength(1)
        expect(within(rowOne).getAllByText("not reviewed")).toHaveLength(1)
    })

    it("falls back to the canonical SMILES headline, never the public ref, when a species has no computed formula", async () => {
        server.use(...handlers({
            speciesRecords: [speciesRecord({
                speciesRef: "spc_nullformula", formula: null, smiles: "NullFormulaSmiles",
                charge: 0, multiplicity: 1, entryRef: "spe_nullformula",
            })],
        }))
        renderAt("/species")
        const headline = await screen.findByRole("link", { name: "NullFormulaSmiles" })
        expect(headline).toHaveAttribute("href", "/species/spc_nullformula")
    })

    it("renders both TS rows with their own distinct equation/family/charge-spin/evidence -- not the first row's data repeated, and no flag labels swapped between rows", async () => {
        const user = userEvent.setup()
        server.use(...handlers())
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Transition state")
        await screen.findByText("A <=> B")
        const rows = document.querySelectorAll(".ts-browse-row")
        expect(rows).toHaveLength(2)
        const rowOne = rows[0] as HTMLElement
        const rowTwo = rows[1] as HTMLElement

        expect(within(rowOne).getByText("A <=> B")).toBeVisible()
        expect(within(rowOne).getByText(/R Addition MultipleBond/)).toBeVisible()
        expect(within(rowOne).getByText(/charge 0 · spin doublet/)).toBeVisible()
        expect(within(rowOne).getByText(/Evidence: opt · freq · sp · irc · geometry validation \(4 calculations\)/)).toBeVisible()
        expect(within(rowOne).queryByText(/scf stability/)).not.toBeInTheDocument()
        expect(within(rowOne).queryByText("C <=> D + [H]")).not.toBeInTheDocument()

        expect(within(rowTwo).getByText("C <=> D + [H]")).toBeVisible()
        expect(within(rowTwo).getByText(/H Abstraction/)).toBeVisible()
        expect(within(rowTwo).getByText(/charge −1 · spin singlet/)).toBeVisible()
        expect(within(rowTwo).getByText(/Evidence: opt · freq · sp · scf stability \(4 calculations\)/)).toBeVisible()
        expect(within(rowTwo).queryByText(/geometry validation/)).not.toBeInTheDocument()
        expect(within(rowTwo).queryByText(/· irc/)).not.toBeInTheDocument()
        expect(within(rowTwo).queryByText("A <=> B")).not.toBeInTheDocument()
    })

    it("renders 'family not recorded' and 'Equation not recorded' fallbacks -- the live archive has 16/34 TS records with family: null", async () => {
        const user = userEvent.setup()
        const nullFamily = tsRecord({ tsEntryRef: "tse_nullfamily", reactionRef: "rxn_nullfamily", equation: "E <=> F", family: null, label: "TS2", hasIrc: false })
        const nullEquation = tsRecord({ tsEntryRef: "tse_nullequation", reactionRef: "rxn_nullequation", equation: null, family: "Disproportionation", label: "TS3", hasIrc: false })
        server.use(...handlers({ tsRecords: [nullFamily, nullEquation] }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await clickKindLink(user, "Transition state")
        await screen.findByText("E <=> F")
        const rows = document.querySelectorAll(".ts-browse-row")
        expect(rows).toHaveLength(2)
        expect(within(rows[0] as HTMLElement).getByText(/family not recorded/)).toBeVisible()
        expect(within(rows[1] as HTMLElement).getByText("Equation not recorded")).toBeVisible()
    })
})

describe("browse page: pagination moves the window and pages do not overlap", () => {
    it("Next/Previous move through three REAL pages (sliced by the actual offset sent), with the exact range text on each and Next disabled only on the last page", async () => {
        const user = userEvent.setup()
        const allRecords = manySpeciesRecords(41) // PAGE_SIZE=20 -> pages of 20, 20, 1
        server.use(...handlers({ speciesRecords: allRecords }))

        renderAt("/species")
        expect(await screen.findByText("Page0Smiles")).toBeVisible()
        expect(screen.getByText("41 records · showing 1–20")).toBeVisible()
        expect(screen.queryByText("Page20Smiles")).not.toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()

        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText("Page20Smiles")).toBeVisible()
        expect(screen.getByText("41 records · showing 21–40")).toBeVisible()
        expect(screen.queryByText("Page0Smiles")).not.toBeInTheDocument()
        expect(screen.queryByText("Page19Smiles")).not.toBeInTheDocument() // page 1's last record does not leak into page 2 (catches a PAGE_SIZE-1 step)
        expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled()
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()

        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText("Page40Smiles")).toBeVisible()
        expect(screen.getByText("41 records · showing 41–41")).toBeVisible()
        expect(screen.queryByText("Page20Smiles")).not.toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled() // last page: no live Next past the end (catches `<=` for `<`)
        expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled()

        await user.click(screen.getByRole("button", { name: "Previous" }))
        expect(await screen.findByText("Page20Smiles")).toBeVisible()
        expect(screen.getByText("41 records · showing 21–40")).toBeVisible()
    })
})

// ---------------------------------------------------------------------------
// Vocabulary-backed evidence filters (Method/Basis/Software/Workflow tool
// and their two dependent version selects). Per-select behaviour (own
// endpoint, dependent refetch+clear, three-state copy, verbatim rendering)
// is covered at the component level in `BrowseFilterForm.test.tsx` -- this
// file only needs the two claims that require the FULL page: a failed
// vocabulary fetch must not take the listing down, and a filter selected
// through the dropdown must actually reach the real outgoing browse query
// (not just the form's own local state).
// ---------------------------------------------------------------------------

describe("browse page: vocabulary-backed filters reach the real browse query, and a failed vocabulary fetch never blocks the listing", () => {
    it("a 500 from /meta/methods still lets the TS records render, with the Method select degraded on its own", async () => {
        // The methods override must be registered BEFORE `handlers()` --
        // msw matches handlers in registration order, and `handlers()`
        // already registers its own (empty-results) `/meta/methods`
        // handler, which would otherwise shadow this one.
        server.use(
            http.get("/api/v1/scientific/meta/methods", () => HttpResponse.json({ detail: "unavailable" }, { status: 500 })),
            ...handlers(),
        )
        renderAt("/transition-states")
        expect(await screen.findByText("A <=> B")).toBeVisible()
        expect(await screen.findByText("Could not load method list.")).toBeVisible()
        // The listing behind it is unaffected -- both TS rows are still there.
        expect(screen.getByText("C <=> D + [H]")).toBeVisible()
    })

    it("selecting a Method value, then switching back to Any, adds then removes method= on the outgoing /transition-states/browse request", async () => {
        const user = userEvent.setup()
        let lastTsUrl: URL | undefined
        server.use(
            http.get("/api/v1/scientific/meta/methods", () => HttpResponse.json({ results: [{ value: "b3lyp", count: 5 }] })),
            ...handlers({ captureTsUrl: (url) => { lastTsUrl = url } }),
        )
        renderAt("/transition-states")
        await screen.findByText("A <=> B")
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(2))

        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        await waitFor(() => expect(lastTsUrl?.searchParams.get("method")).toBe("b3lyp"))

        await user.selectOptions(screen.getByLabelText("Method"), "")
        await waitFor(() => expect(lastTsUrl?.searchParams.has("method")).toBe(false))
    })
})

// `/species/browse` (species_browse.py) accepts the same six provenance
// parameters (method/basis/software(+version)/workflow_tool(+version)) as
// `/transition-states/browse` -- but the six selects used to be mounted
// ONLY for kind="transition_state", and `buildSpeciesBrowseQuery` used to
// drop all six params on the floor even if a caller supplied them. This is
// the full-page counterpart to `browseApi.test.ts`'s unit coverage: it
// exercises the real dropdown -> real outgoing request path, not just the
// pure query builder in isolation.
describe("browse page: the six provenance selects work on species, not just transition state", () => {
    it("selecting a Method value on the default (species) kind adds method= to the outgoing /species/browse request -- asserts the URL, not just that something rendered", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        server.use(
            http.get("/api/v1/scientific/meta/methods", () => HttpResponse.json({ results: [{ value: "b3lyp", count: 5 }] })),
            ...handlers({ captureSpeciesUrl: (url) => { lastSpeciesUrl = url } }),
        )
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(2))

        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.get("method")).toBe("b3lyp"))
        expect(lastSpeciesUrl?.searchParams.get("species_entry_kind")).toBe("minimum") // still the species request, not a different endpoint

        await user.selectOptions(screen.getByLabelText("Method"), "")
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.has("method")).toBe(false))
    })

    // Reproduces the review's exact concern for this branch: `hasActiveFilters`
    // used to only look at the six provenance fields when kind==="transition_state",
    // so a species query narrowed to zero rows by `method=` alone would report
    // "no filters active" and render the ARCHIVE-empty message ("nothing of this
    // kind has been deposited") instead of the FILTERED-empty one ("filters
    // excluded everything") -- exactly backwards, and the mirror image of the
    // widening-toggle bug fixed earlier on this same page.
    it("a species query narrowed to zero rows by Method alone reads 'filters excluded everything', never 'nothing deposited'", async () => {
        const user = userEvent.setup()
        server.use(
            http.get("/api/v1/scientific/meta/methods", () => HttpResponse.json({ results: [{ value: "b3lyp", count: 5 }] })),
            http.get("/api/v1/scientific/species/browse", ({ request }) => {
                const url = new URL(request.url)
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                // A real narrowing: nonzero without `method=`, zero once it is set --
                // not an unconditionally-empty fixture, which cannot tell "the filter
                // did nothing" apart from "the filter genuinely excluded everything".
                const rows = url.searchParams.get("method") ? [] : twoSpecies
                return HttpResponse.json(speciesEnvelope(offset, limit, rows))
            }),
            // Registered AFTER the /meta/methods override above -- msw matches in
            // registration order, so this generic (empty-results) /meta/methods
            // handler never actually wins; it only backstops the other five
            // /meta/* endpoints ProvenanceFields also fetches.
            ...emptyVocabHandlers(),
            http.get("/api/v1/scientific/transition-states/browse", () => HttpResponse.json(tsEnvelope(0, 20, twoTs))),
        )
        renderAt("/species")
        await screen.findByText(/records · showing/)
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(2))

        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        expect(await screen.findByText(/No species records match these filters/)).toBeVisible()
        expect(screen.queryByText(/have been deposited in this archive yet/)).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// PR 4b: the "reaction" browse kind, end to end through the real page --
// kind selection, query building, row rendering (two DISTINCT rows, same
// "records[0] hardcode" guard the species/TS fixtures above use), the
// family vocabulary dropdown, and this kind's own empty-state wording.
// ---------------------------------------------------------------------------

function reactionRecord(overrides: {
    reactionEntryRef: string
    reactionRef: string
    equation: string
    reversible: boolean
    family: string | null
    reviewStatus?: string
    hasKinetics: boolean
    hasTransitionState: boolean
    reactants: { ref: string; smiles: string; formula: string | null }[]
    products: { ref: string; smiles: string; formula: string | null }[]
    matchedDirection?: string
}) {
    return {
        reaction_ref: overrides.reactionRef,
        reaction_entry_ref: overrides.reactionEntryRef,
        equation: overrides.equation,
        matched_direction: overrides.matchedDirection ?? "forward",
        reversible: overrides.reversible,
        family: overrides.family,
        review: { status: overrides.reviewStatus ?? "not_reviewed", reviewed_at: null, reviewer_kind: null },
        reactants: overrides.reactants.map((p, index) => (
            { species_entry_ref: p.ref, species_entry_label: null, smiles: p.smiles, formula: p.formula, stoichiometry: 1, participant_index: index + 1 }
        )),
        products: overrides.products.map((p, index) => (
            { species_entry_ref: p.ref, species_entry_label: null, smiles: p.smiles, formula: p.formula, stoichiometry: 1, participant_index: index + 1 }
        )),
        availability: {
            has_kinetics: overrides.hasKinetics, has_transition_state: overrides.hasTransitionState,
            has_path_search: false, has_atom_map: false, kinetics_count: overrides.hasKinetics ? 1 : 0,
        },
    }
}

function reactionEnvelope(offset: number, limit: number, allRecords: ReturnType<typeof reactionRecord>[]) {
    const page = allRecords.slice(offset, offset + limit)
    return {
        request: { profile: "exploratory", profile_recommendation: "none", profile_release_ref: null, filter: {}, sort: "review_rank,has_kinetics,has_transition_state,created_at,id", collapse: "all", include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: allRecords.length, deprecated: 0, rejected: 0, total: allRecords.length },
        records: page,
        pagination: { offset, limit, returned: page.length, total: allRecords.length, post_collapse_total: allRecords.length },
    }
}

const twoReactions = [
    reactionRecord({
        reactionEntryRef: "rxe_one", reactionRef: "rxn_one", equation: "O + [CH3] <=> C + [OH]", reversible: true,
        family: "R_Addition_MultipleBond", hasKinetics: true, hasTransitionState: true,
        reactants: [{ ref: "spe_o", smiles: "[O]", formula: "O" }, { ref: "spe_ch3", smiles: "[CH3]", formula: "CH3" }],
        products: [{ ref: "spe_ch4", smiles: "C", formula: "CH4" }, { ref: "spe_oh", smiles: "[OH]", formula: "OH" }],
    }),
    reactionRecord({
        reactionEntryRef: "rxe_two", reactionRef: "rxn_two", equation: "N=N <=> [NH2] + [NH2]", reversible: false,
        family: null, hasKinetics: false, hasTransitionState: false, reviewStatus: "approved",
        reactants: [{ ref: "spe_nn", smiles: "N=N", formula: "H2N2" }],
        products: [{ ref: "spe_nh2a", smiles: "[NH2]", formula: "H2N" }, { ref: "spe_nh2b", smiles: "[NH2]", formula: "H2N" }],
    }),
]

function reactionHandler(options: { records?: ReturnType<typeof reactionRecord>[]; captureUrl?: (url: URL) => void } = {}) {
    const allRecords = options.records ?? twoReactions
    return http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
        const url = new URL(request.url)
        options.captureUrl?.(url)
        const offset = Number(url.searchParams.get("offset") ?? "0")
        const limit = Number(url.searchParams.get("limit") ?? "20")
        return HttpResponse.json(reactionEnvelope(offset, limit, allRecords))
    })
}

function reactionFamilyVocabHandler() {
    return http.get("/api/v1/scientific/meta/reaction-families", () => HttpResponse.json({
        results: [
            { value: "R_Addition_MultipleBond", display_name: "Radical Addition Multiple Bond", count: 5 },
            { value: "H_Abstraction", display_name: "Hydrogen Abstraction", count: 3 },
        ],
    }))
}

describe("browse page: the 'reaction' kind, end to end", () => {
    it("selecting 'Reaction' NAVIGATES to /reactions and queries /reactions/browse, never /species/browse or /transition-states/browse again", async () => {
        const user = userEvent.setup()
        let speciesCalls = 0
        let tsCalls = 0
        let reactionCalls = 0
        server.use(
            ...handlers({ captureSpeciesUrl: () => { speciesCalls += 1 }, captureTsUrl: () => { tsCalls += 1 } }),
            reactionHandler({ captureUrl: () => { reactionCalls += 1 } }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/species")
        await screen.findByText(/records · showing/)
        const speciesCallsBeforeSwitch = speciesCalls

        await clickKindLink(user, "Reaction")
        await waitFor(() => expect(reactionCalls).toBeGreaterThan(0))
        expect(speciesCalls).toBe(speciesCallsBeforeSwitch) // no further species calls after the switch
        expect(tsCalls).toBe(0)
        await waitFor(() => expect(window.location.pathname).toBe("/reactions"))
    })

    it("loading /reactions directly renders the reaction kind and hits /reactions/browse", async () => {
        server.use(...handlers(), reactionHandler(), reactionFamilyVocabHandler())
        renderAt("/reactions")
        expect(await screen.findByRole("heading", { name: "Browse reactions" })).toBeVisible()
        await screen.findByText(/records · showing/)
        expect(window.location.pathname).toBe("/reactions")
    })

    // Cross-branch gap, found post-hoc: the front-page reaction search's
    // "See all N reactions involving X" link points at
    // `/reactions?reactant_smiles=X` and PROMISES a filtered count. Without
    // this seeding, that link would land on an unfiltered `/reactions`
    // showing every reaction in the archive under a heading that claimed a
    // specific count for a specific structure -- a wrong answer presented
    // as a right one. Asserted on the RESULT COUNT, not just the field's
    // displayed text -- a test that only checked the input's value would
    // stay green even if the seed never reached the outgoing request at
    // all (see `seedFiltersFromUrl`'s own unit tests above for the
    // narrower, request-shape-only coverage).
    it("?reactant_smiles= on the initial URL seeds the field AND filters the listing, not just displays the value", async () => {
        let capturedUrl: URL | undefined
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                capturedUrl = url
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const values = url.searchParams.getAll("reactant_smiles")
                const rows = values.length === 0 ? twoReactions : [twoReactions[0]]
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions?reactant_smiles=NN")
        expect(await screen.findByLabelText("Reactant structures")).toHaveValue("NN")
        await waitFor(() => expect(capturedUrl?.searchParams.getAll("reactant_smiles")).toEqual(["NN"]))
        // The rendered count reflects the FILTERED corpus (1 of 2), not the
        // full unfiltered archive.
        expect(await screen.findByText("1 record · showing 1–1")).toBeVisible()
    })

    // Same gap, the REPEATED-param shape: `?reactant_smiles=A&reactant_
    // smiles=B` must populate BOTH structures into the one field (its own
    // comma-separated shape), not just the first.
    it("a REPEATED ?reactant_smiles= on the initial URL seeds BOTH structures into the field, and the listing reflects the two-structure AND filter", async () => {
        let capturedUrl: URL | undefined
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                capturedUrl = url
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const values = url.searchParams.getAll("reactant_smiles")
                const rows = values.length >= 2 ? [] : twoReactions
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions?reactant_smiles=NN&reactant_smiles=%5BH%5D")
        expect(await screen.findByLabelText("Reactant structures")).toHaveValue("NN,[H]")
        await waitFor(() => expect(capturedUrl?.searchParams.getAll("reactant_smiles")).toEqual(["NN", "[H]"]))
        expect(await screen.findByText(/No reaction entries match these filters/)).toBeVisible()
    })

    it("?product_smiles= seeds the OTHER field the same way", async () => {
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const values = url.searchParams.getAll("product_smiles")
                const rows = values.length === 0 ? twoReactions : [twoReactions[0]]
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions?product_smiles=C")
        expect(await screen.findByLabelText("Product structures")).toHaveValue("C")
        expect(await screen.findByText("1 record · showing 1–1")).toBeVisible()
    })

    // MUTATION CHECK (the coordinator's own addition): reverting
    // `seedFiltersFromUrl`'s reaction branch back to seeding nothing (the
    // pre-fix state) must fail on the RESULT COUNT, not merely on the
    // field's displayed value -- landing this test file with the seed
    // reverted is the mutation-table entry for this fix.
    it("MUTATION-SHAPED: without the seed, the field would be empty and the count would be the FULL unfiltered total -- this is what the fix prevents", async () => {
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const values = url.searchParams.getAll("reactant_smiles")
                const rows = values.length === 0 ? twoReactions : [twoReactions[0]]
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions?reactant_smiles=NN")
        // With the seed working, this is 1 of 2 -- NOT "2 records · showing
        // 1–2" (the unfiltered total a broken/reverted seed would show).
        expect(await screen.findByText("1 record · showing 1–1")).toBeVisible()
        expect(screen.queryByText(/^2 records/)).not.toBeInTheDocument()
    })

    it("renders two DISTINCT reaction rows with their own equation, family, review, and availability pills -- not the first row's data repeated", async () => {
        server.use(...handlers(), reactionHandler(), reactionFamilyVocabHandler())
        renderAt("/reactions")
        await screen.findByText(/records · showing/)

        const rows = document.querySelectorAll(".reaction-browse-row")
        expect(rows).toHaveLength(2)

        const [first, second] = [...rows] as HTMLElement[]
        // The footer shows the REACTION (identity) ref now, not the entry
        // ref -- the entry ref is still reachable, as the "View this
        // deposit" link's href (see the dedicated link-target test below).
        expect(within(first).getByText("rxn_one")).toBeVisible()
        expect(within(first).getByRole("link", { name: "View this deposit" })).toHaveAttribute("href", "/reaction-entries/rxe_one")
        // The row renders its OWN served `family` string, token-formatted
        // (the same naive underscore-to-space convention
        // `TransitionStateBrowseRow` already uses) -- not the vocab
        // dropdown's `display_name`, which only labels the FILTER select.
        expect(within(first).getByText("R Addition MultipleBond")).toBeVisible()
        expect(within(first).getByText("not reviewed")).toBeVisible()
        expect(within(first).getByText("has kinetics")).toBeVisible()
        expect(within(first).getByText("has transition state")).toBeVisible()

        expect(within(second).getByText("rxn_two")).toBeVisible()
        expect(within(second).getByRole("link", { name: "View this deposit" })).toHaveAttribute("href", "/reaction-entries/rxe_two")
        expect(within(second).getByText("family not recorded")).toBeVisible()
        expect(within(second).getByText("approved")).toBeVisible()
        expect(within(second).getByText("no kinetics deposited")).toBeVisible()
        expect(within(second).getByText("no transition state deposited")).toBeVisible()

        // The second row must NOT show the first row's values.
        expect(within(second).queryByText("R Addition MultipleBond")).not.toBeInTheDocument()
        expect(within(second).queryByText("has kinetics")).not.toBeInTheDocument()
    })

    it("each row links to its OWN /reaction-entries/:ref -- not the first row's ref repeated on the second", async () => {
        server.use(...handlers(), reactionHandler(), reactionFamilyVocabHandler())
        renderAt("/reactions")
        await screen.findByText(/records · showing/)
        const links = screen.getAllByRole("link").map((el) => el.getAttribute("href"))
        expect(links).toContain("/reaction-entries/rxe_one")
        expect(links).toContain("/reaction-entries/rxe_two")
    })

    it("the family filter reaches the outgoing /reactions/browse request", async () => {
        const user = userEvent.setup()
        let capturedUrl: URL | undefined
        server.use(...handlers(), reactionHandler({ captureUrl: (url) => { capturedUrl = url } }), reactionFamilyVocabHandler())
        renderAt("/reactions")
        await screen.findByText(/records · showing/)
        await waitFor(() => expect(screen.getByLabelText("Family").querySelectorAll("option")).toHaveLength(3))

        await user.selectOptions(screen.getByLabelText("Family"), "Hydrogen Abstraction")
        await waitFor(() => expect(capturedUrl?.searchParams.get("family")).toBe("H_Abstraction"))
    })

    it("the two structure filters reach the outgoing request as SEPARATE params", async () => {
        const user = userEvent.setup()
        let capturedUrl: URL | undefined
        server.use(...handlers(), reactionHandler({ captureUrl: (url) => { capturedUrl = url } }), reactionFamilyVocabHandler())
        renderAt("/reactions")
        await screen.findByText(/records · showing/)

        await user.type(screen.getByLabelText("Reactant structures"), "CCO")
        await waitFor(() => expect(capturedUrl?.searchParams.get("reactant_smiles")).toBe("CCO"))
        expect(capturedUrl?.searchParams.has("product_smiles")).toBe(false)

        await user.type(screen.getByLabelText("Product structures"), "CC=O")
        await waitFor(() => expect(capturedUrl?.searchParams.get("product_smiles")).toBe("CC=O"))
        expect(capturedUrl?.searchParams.get("reactant_smiles")).toBe("CCO") // unchanged by the product field
    })

    // Item 3's multi-structure search, end to end through the real page:
    // typing a COMMA-separated value into "Reactant structures" must
    // reach the wire as TWO repeated `reactant_smiles` params, verified
    // live -- `?reactant_smiles=NN` -> 20 reactions, `?reactant_smiles=NN&
    // reactant_smiles=[H]` -> 0. `fireEvent.change` (not `user.type`) sets
    // the field's value directly, since `[` and `]` are userEvent's own
    // special-character delimiters for `.type()` and NN/[H] genuinely need
    // literal brackets.
    it("typing two comma-separated SMILES into 'Reactant structures' sends TWO repeated reactant_smiles params, and the result count changes", async () => {
        let capturedUrl: URL | undefined
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                capturedUrl = url
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                // Mirrors the live archive's own measured behaviour: a
                // single structure narrows the corpus, a SECOND one on the
                // same side (that never co-occurs with the first) empties
                // it entirely.
                const values = url.searchParams.getAll("reactant_smiles")
                const rows = values.length >= 2 ? [] : values.length === 1 ? [twoReactions[0]] : twoReactions
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions")
        await screen.findByText(/records · showing/)

        const field = screen.getByLabelText("Reactant structures")
        fireEvent.change(field, { target: { value: "NN" } })
        await waitFor(() => expect(capturedUrl?.searchParams.getAll("reactant_smiles")).toEqual(["NN"]))
        await screen.findByText(/1 record · showing/)

        fireEvent.change(field, { target: { value: "NN,[H]" } })
        await waitFor(() => expect(capturedUrl?.searchParams.getAll("reactant_smiles")).toEqual(["NN", "[H]"]))
        expect(await screen.findByText(/No reaction entries match these filters/)).toBeVisible()
    })

    it("an UNSET filter is never sent -- the initial request carries no family/reactant_smiles/product_smiles/has_kinetics/has_transition_state at all", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers(), reactionHandler({ captureUrl: (url) => { capturedUrl = url } }), reactionFamilyVocabHandler())
        renderAt("/reactions")
        await screen.findByText(/records · showing/)
        for (const param of ["family", "reactant_smiles", "product_smiles", "has_kinetics", "has_transition_state"]) {
            expect(capturedUrl?.searchParams.has(param)).toBe(false)
        }
    })

    it("archive-empty wording for 'reaction' when the archive holds none, distinct from the filtered-empty wording", async () => {
        const user = userEvent.setup()
        server.use(...handlers(), reactionHandler({ records: [] }), reactionFamilyVocabHandler())
        renderAt("/reactions")
        expect(await screen.findByText(/No reaction entries have been deposited in this archive yet/)).toBeVisible()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()

        // A widening toggle must not flip this into the filtered-empty message.
        await user.click(screen.getByLabelText("Include rejected"))
        expect(await screen.findByText(/No reaction entries have been deposited in this archive yet/)).toBeVisible()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
    })

    it("filtered-empty wording for 'reaction' when a filter genuinely narrows a nonzero corpus to zero", async () => {
        const user = userEvent.setup()
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const rows = url.searchParams.get("reactant_smiles") ? [] : twoReactions
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions")
        await screen.findByText(/records · showing/)
        await user.type(screen.getByLabelText("Reactant structures"), "Xx999")
        expect(await screen.findByText(/No reaction entries match these filters/)).toBeVisible()
        expect(screen.queryByText(/have been deposited in this archive yet/)).not.toBeInTheDocument()
    })

    // Review follow-up (round 2), reproducing the live finding exactly: a
    // `product_smiles=O` search against the real archive returns
    // `rxe_ed66mj3ohtyien5rm2x3sb3rdu` ("O + [CH3] <=> C + [OH]", water on
    // the REACTANT side) with `matched_direction: "reverse"` -- without
    // this note, a reader searching Product SMILES "O" sees water on the
    // wrong side with no explanation. End to end through the real page
    // (typed filter -> outgoing request -> served row -> rendered note),
    // not just the component-level fixture in `ReactionBrowseRow.test.tsx`.
    it("a product-SMILES search that matches the REVERSE direction renders the note on that row, through the real page", async () => {
        const user = userEvent.setup()
        const reverseMatch = reactionRecord({
            reactionEntryRef: "rxe_ed66mj3ohtyien5rm2x3sb3rdu", reactionRef: "rxn_zicx5swji2nqkg2v263rnwxn4m",
            equation: "O + [CH3] <=> C + [OH]", reversible: true, family: "H_Abstraction",
            hasKinetics: true, hasTransitionState: true, matchedDirection: "reverse",
            reactants: [{ ref: "spe_o", smiles: "O", formula: "H2O" }, { ref: "spe_ch3", smiles: "[CH3]", formula: "CH3" }],
            products: [{ ref: "spe_ch4", smiles: "C", formula: "CH4" }, { ref: "spe_oh", smiles: "[OH]", formula: "OH" }],
        })
        server.use(
            ...handlers(),
            http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
                const url = new URL(request.url)
                const offset = Number(url.searchParams.get("offset") ?? "0")
                const limit = Number(url.searchParams.get("limit") ?? "20")
                const rows = url.searchParams.get("product_smiles") === "O" ? [reverseMatch] : twoReactions
                return HttpResponse.json(reactionEnvelope(offset, limit, rows))
            }),
            reactionFamilyVocabHandler(),
        )
        renderAt("/reactions")
        await screen.findByText(/records · showing/)
        await user.type(screen.getByLabelText("Product structures"), "O")
        await screen.findByText("Matched on the reverse direction")

        const rows = document.querySelectorAll(".reaction-browse-row")
        expect(rows).toHaveLength(1)
        expect(within(rows[0] as HTMLElement).getByText("Matched on the reverse direction")).toBeVisible()

        // The other (forward-matched) fixture never carries the note.
        const forwardRows = twoReactions.map((r) => r.reaction_entry_ref)
        expect(forwardRows).not.toContain("rxe_ed66mj3ohtyien5rm2x3sb3rdu")
    })
})
