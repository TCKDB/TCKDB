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
// -- checking only the first row would let that bug through.
// ---------------------------------------------------------------------------

function speciesRecord(overrides: {
    speciesRef: string
    formula: string | null
    smiles: string
    charge: number
    multiplicity: number
    entryRef: string
}) {
    return {
        species_ref: overrides.speciesRef,
        canonical_smiles: overrides.smiles,
        inchi_key: `${overrides.speciesRef.toUpperCase()}-KEY`,
        formula: overrides.formula,
        charge: overrides.charge,
        multiplicity: overrides.multiplicity,
        stereo_kind: "achiral",
        entries: [{
            species_entry_ref: overrides.entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
            availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: 4 },
        }],
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
    equation: string
    family: string
    label: string
    hasIrc: boolean
}) {
    return {
        transition_state_entry: {
            transition_state_entry_ref: overrides.tsEntryRef,
            charge: 0, multiplicity: 2, status: "optimized", unmapped_smiles: null,
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
            has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
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
    speciesRecord({ speciesRef: "spc_benzene", formula: "C6H6", smiles: "c1ccccc1", charge: 0, multiplicity: 1, entryRef: "spe_benzene" }),
    speciesRecord({ speciesRef: "spc_methyl", formula: "CH3", smiles: "[CH3]", charge: 0, multiplicity: 2, entryRef: "spe_methyl" }),
]

const twoTs = [
    tsRecord({ tsEntryRef: "tse_one", reactionRef: "rxn_one", equation: "A <=> B", family: "R_Addition_MultipleBond", label: "TS0", hasIrc: true }),
    tsRecord({ tsEntryRef: "tse_two", reactionRef: "rxn_two", equation: "C <=> D + [H]", family: "H_Abstraction", label: "TS1", hasIrc: false }),
]

type HandlerOptions = {
    speciesRecords?: ReturnType<typeof speciesRecord>[]
    tsRecords?: ReturnType<typeof tsRecord>[]
    speciesStatus?: number
    captureSpeciesUrl?: (url: URL) => void
    captureTsUrl?: (url: URL) => void
}

function handlers(options: HandlerOptions = {}) {
    const speciesAll = options.speciesRecords ?? twoSpecies
    const tsAll = options.tsRecords ?? twoTs
    return [
        http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const url = new URL(request.url)
            options.captureSpeciesUrl?.(url)
            if (options.speciesStatus) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.speciesStatus })
            // The live vdW filter genuinely returns zero rows today (measured:
            // 60 minimum entries, 0 vdw_complex) -- mirrored here rather than
            // invented, so the fixture matches the real archive shape this
            // page is built against.
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

    it("a Transition-state-only filter (method) does not survive switching back to Species", async () => {
        const user = userEvent.setup()
        let lastSpeciesUrl: URL | undefined
        server.use(...handlers({ captureSpeciesUrl: (url) => { lastSpeciesUrl = url } }))
        renderAt("/species?kind=transition_state")
        await screen.findByText("A <=> B")

        await user.type(screen.getByLabelText("Method"), "b3lyp")
        await waitFor(() => expect(screen.getByLabelText("Method")).toHaveValue("b3lyp"))

        await user.click(screen.getByRole("radio", { name: "Species" }))
        await waitFor(() => expect(lastSpeciesUrl?.searchParams.has("species_entry_kind")).toBe(true))
        expect(lastSpeciesUrl?.searchParams.has("method")).toBe(false)
        expect(screen.queryByLabelText("Method")).not.toBeInTheDocument()
    })
})

describe("browse page: the three empty/error states are distinguishable", () => {
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

    it("reads 'no records match these filters' when a filter excludes every species row", async () => {
        const user = userEvent.setup()
        server.use(...handlers({ speciesRecords: [] }))
        renderAt("/species")
        await user.type(screen.getByLabelText("Formula"), "Xx999")
        expect(await screen.findByText(/No species records match these filters/)).toBeVisible()
        expect(screen.queryByText(/have been deposited in this archive yet/)).not.toBeInTheDocument()
    })

    it("reads a failure message, with role=alert, when the request errors -- distinct from either empty state", async () => {
        server.use(...handlers({ speciesStatus: 503 }))
        renderAt("/species")
        expect(await screen.findByRole("alert")).toHaveTextContent("The archive service could not load this listing. Try again later.")
        expect(screen.queryByText(/deposited in this archive yet/)).not.toBeInTheDocument()
        expect(screen.queryByText(/match these filters/)).not.toBeInTheDocument()
    })

    // Mutation-shaped regression: collapsing the archive-empty and
    // filtered-empty branches into one shared message would make these two
    // strings identical -- guard that they are not.
    it("MUTATION CHECK: the archive-empty and filtered-empty messages are not the same string", async () => {
        const user = userEvent.setup()
        server.use(...handlers({ speciesRecords: [] }))
        renderAt("/species")
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

describe("browse page: a species row and a TS row each render their OWN fields", () => {
    it("renders both species rows with their own distinct SMILES and charge/spin -- not the first row's data repeated", async () => {
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

        expect(within(rowTwo).getByText("[CH3]")).toBeVisible()
        expect(within(rowTwo).getByText(/charge 0 · spin doublet/)).toBeVisible()
        expect(within(rowTwo).queryByText("c1ccccc1")).not.toBeInTheDocument()
    })

    it("renders both TS rows with their own distinct equation/family/evidence -- not the first row's data repeated", async () => {
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
        expect(within(rowOne).getByText(/Evidence:.*irc/)).toBeVisible()
        expect(within(rowOne).queryByText("C <=> D + [H]")).not.toBeInTheDocument()

        expect(within(rowTwo).getByText("C <=> D + [H]")).toBeVisible()
        expect(within(rowTwo).getByText(/H Abstraction/)).toBeVisible()
        expect(within(rowTwo).getByText(/Evidence: opt · freq · sp \(4 calculations\)/)).toBeVisible()
        expect(within(rowTwo).queryByText("A <=> B")).not.toBeInTheDocument()
    })
})

describe("browse page: pagination moves the window and pages do not overlap", () => {
    it("Next advances the offset and shows a disjoint page; Previous returns to the first page", async () => {
        const user = userEvent.setup()
        const page1 = [speciesRecord({ speciesRef: "spc_p1", formula: "PP", smiles: "PageOneSmiles", charge: 0, multiplicity: 1, entryRef: "spe_p1" })]
        const page2 = [speciesRecord({ speciesRef: "spc_p2", formula: "QQ", smiles: "PageTwoSmiles", charge: 0, multiplicity: 1, entryRef: "spe_p2" })]
        // `total: 21` (bigger than one page) is what makes the Next button
        // enabled on page one -- see `BrowseResults`'s `hasNextPage`.
        server.use(http.get("/api/v1/scientific/species/browse", ({ request }) => {
            const offset = Number(new URL(request.url).searchParams.get("offset") ?? "0")
            if (offset === 0) {
                return HttpResponse.json({
                    ...speciesEnvelope(0, 20, page1),
                    pagination: { offset: 0, limit: 20, returned: 1, total: 21, post_collapse_total: 21 },
                })
            }
            return HttpResponse.json({
                ...speciesEnvelope(0, 20, page2),
                pagination: { offset: 20, limit: 20, returned: 1, total: 21, post_collapse_total: 21 },
            })
        }))
        renderAt("/species")
        expect(await screen.findByText("PageOneSmiles")).toBeVisible()
        expect(screen.queryByText("PageTwoSmiles")).not.toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()

        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText("PageTwoSmiles")).toBeVisible()
        expect(screen.queryByText("PageOneSmiles")).not.toBeInTheDocument() // pages do not overlap

        await user.click(screen.getByRole("button", { name: "Previous" }))
        expect(await screen.findByText("PageOneSmiles")).toBeVisible()
        expect(screen.queryByText("PageTwoSmiles")).not.toBeInTheDocument()
    })
})
