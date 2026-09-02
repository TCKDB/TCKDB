import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"

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
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

function renderAt(path: string) {
    window.history.replaceState({}, "", path)
    render(<App />)
}

describe("browse page: kind selection queries the right endpoint with the right parameters", () => {
    it("defaults to species, hits /species/browse with species_entry_kind=minimum, and writes ?kind=species into the URL", async () => {
        let capturedUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { capturedUrl = url } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        expect(capturedUrl?.searchParams.get("species_entry_kind")).toBe("minimum")
        await waitFor(() => expect(new URLSearchParams(window.location.search).get("kind")).toBe("species"))
    })

    it("selecting 'Van der Waals complex' queries /species/browse with species_entry_kind=vdw_complex, not the TS endpoint", async () => {
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
        await user.click(screen.getByRole("radio", { name: "Van der Waals complex" }))
        await waitFor(() => expect(lastKindParam).toBe("vdw_complex"))
        expect(speciesCalls).toBeGreaterThan(0)
        expect(tsCalls).toBe(0)
        await waitFor(() => expect(new URLSearchParams(window.location.search).get("kind")).toBe("vdw"))
    })

    it("selecting 'Transition state' queries /transition-states/browse, never /species/browse again", async () => {
        const user = userEvent.setup()
        let tsCalls = 0
        let speciesCallsAfterSwitch = 0
        server.use(...handlers({ captureTsUrl: () => { tsCalls += 1 } }))
        renderAt("/species")
        await screen.findByText(/records · showing/)
        server.use(http.get("/api/v1/scientific/species/browse", () => { speciesCallsAfterSwitch += 1; return HttpResponse.json(speciesEnvelope(0, 20, twoSpecies)) }))
        await user.click(screen.getByRole("radio", { name: "Transition state" }))
        expect(await screen.findByText("A <=> B")).toBeVisible()
        await waitFor(() => expect(tsCalls).toBeGreaterThan(0))
        expect(speciesCallsAfterSwitch).toBe(0)
        await waitFor(() => expect(new URLSearchParams(window.location.search).get("kind")).toBe("transition_state"))
    })

    it("an unrecognised ?kind= self-heals to species in the address bar", async () => {
        server.use(...handlers())
        renderAt("/species?kind=nonsense")
        await screen.findByText(/records · showing/)
        await waitFor(() => expect(new URLSearchParams(window.location.search).get("kind")).toBe("species"))
        expect(screen.getByRole("radio", { name: "Species" })).toBeChecked()
    })

    it("?kind=transition_state survives a direct load (round-trip)", async () => {
        server.use(...handlers())
        renderAt("/species?kind=transition_state")
        expect(await screen.findByRole("radio", { name: "Transition state" })).toBeChecked()
        expect(await screen.findByText("A <=> B")).toBeVisible()
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
        await user.click(screen.getByRole("radio", { name: "Van der Waals complex" }))
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

        await user.click(screen.getByRole("radio", { name: "Transition state" }))
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
        renderAt("/species?kind=transition_state")
        await screen.findByText("A <=> B")

        await user.selectOptions(screen.getByLabelText("Status"), "optimized")
        await waitFor(() => expect(screen.getByLabelText("Status")).toHaveValue("optimized"))

        await user.click(screen.getByRole("radio", { name: "Species" }))
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
        renderAt("/species?kind=transition_state")
        await screen.findByText("A <=> B")

        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(2))
        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        await waitFor(() => expect(lastTsUrl?.searchParams.get("method")).toBe("b3lyp"))

        await user.click(screen.getByRole("radio", { name: "Species" }))
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.get("method")).toBe("b3lyp"))
        expect(screen.getByLabelText("Method")).toHaveValue("b3lyp")

        await user.click(screen.getByRole("radio", { name: "Transition state" }))
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
        renderAt("/species?kind=transition_state")
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
        await user.click(screen.getByRole("radio", { name: "Species" }))
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
        await user.click(screen.getByRole("radio", { name: "Van der Waals complex" }))
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
        await user.click(screen.getByRole("radio", { name: "Van der Waals complex" }))
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
        await user.click(screen.getByRole("radio", { name: "Van der Waals complex" }))
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
        await user.click(screen.getByRole("radio", { name: "Transition state" }))
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
        await user.click(screen.getByRole("radio", { name: "Transition state" }))
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
        renderAt("/species?kind=transition_state")
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
        renderAt("/species?kind=transition_state")
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
