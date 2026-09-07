import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { loadReactionEntry, loadReactionEntryNetworksFallback } from "./reactionEntryApi"

const ENTRY_REF = "rxe_ed66mj3ohtyien5rm2x3sb3rdu"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const baseReactionEntry = {
    reaction_entry_ref: ENTRY_REF,
    reaction_ref: "rxn_zicx5swji2nqkg2v263rnwxn4m",
    equation: "O + [CH3] <=> C + [OH]",
    reversible: true,
    family: "H_Abstraction",
    review: { status: "not_reviewed" },
    atom_maps: [],
}

describe("loadReactionEntry", () => {
    it("requests /full with the five-token include list, once", async () => {
        let requestedUrl = ""
        server.use(
            http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/full`, ({ request }) => {
                requestedUrl = request.url
                return HttpResponse.json({
                    reaction_entry: baseReactionEntry,
                    review_summary: { total: 7, not_reviewed: 7, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
                    species: { reactants: [], products: [] },
                    kinetics: [],
                    transition_states: [],
                    calculations: [],
                    networks: [],
                })
            }),
        )
        await loadReactionEntry(ENTRY_REF)
        const includeValues = new URL(requestedUrl).searchParams.getAll("include")
        expect(includeValues).toEqual(["species", "kinetics", "transition_states", "calculations", "networks"])
    })

    // Pre-deployment (PR 1, `feat/reaction-full-additions`, not yet live):
    // `formula`/`stoichiometry` on participants, `levels` on kinetics, and
    // the whole `networks` key are all absent from a real response today.
    // This schema must still parse it, with every §3-additive field
    // resolving to `undefined`/a documented default rather than a parse
    // failure.
    it("parses a pre-deployment response with every §3-additive field absent", async () => {
        server.use(
            http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/full`, () => HttpResponse.json({
                reaction_entry: baseReactionEntry,
                review_summary: { total: 2, not_reviewed: 2, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
                species: {
                    reactants: [{
                        species_entry_ref: "spe_a",
                        smiles: "O",
                        participant_index: 0,
                        review: { status: "not_reviewed" },
                        // no formula, no stoichiometry
                    }],
                    products: [],
                },
                kinetics: [{
                    kinetics_ref: "kin_a",
                    scientific_origin: "computed",
                    model_kind: "modified_arrhenius",
                    review: { status: "not_reviewed" },
                    parameters: { A: 1.0, A_units: "cm3_mol_s", n: 1.0, Ea_kj_mol: 1.0 },
                    uncertainty: {},
                    evidence_completeness: { score: 0, max: 9, checklist: {} },
                    provenance: {},
                    // no `levels` key
                }],
                transition_states: [],
                calculations: [],
                // no `networks` key at all
            })),
        )
        const record = await loadReactionEntry(ENTRY_REF)
        expect(record.species!.reactants[0].formula).toBeUndefined()
        expect(record.species!.reactants[0].stoichiometry).toBe(1)
        expect(record.kinetics![0].levels).toBeUndefined()
        expect(record.networks).toBeUndefined()
    })

    it("parses a full post-deployment response with every §3-additive field populated", async () => {
        server.use(
            http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/full`, () => HttpResponse.json({
                reaction_entry: baseReactionEntry,
                review_summary: { total: 1, not_reviewed: 1, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
                species: {
                    reactants: [{
                        species_entry_ref: "spe_a",
                        smiles: "O",
                        formula: "H2O",
                        stoichiometry: 2,
                        participant_index: 0,
                        review: { status: "not_reviewed" },
                    }],
                    products: [],
                },
                kinetics: [{
                    kinetics_ref: "kin_a",
                    scientific_origin: "computed",
                    model_kind: "modified_arrhenius",
                    review: { status: "not_reviewed" },
                    parameters: { A: 1.0, A_units: "cm3_mol_s", n: 1.0, Ea_kj_mol: 1.0 },
                    uncertainty: {},
                    evidence_completeness: { score: 0, max: 9, checklist: {} },
                    provenance: {},
                    levels: { geometry: { method: "b3lyp", basis: "def2tzvp" }, frequency: null, energy: null, energy_source: null },
                }],
                transition_states: [],
                calculations: [],
                networks: [{
                    network_ref: "net_a",
                    name: "hydrazine",
                    solve_temperature_min_k: 300,
                    solve_temperature_max_k: 2000,
                    solve_pressure_min_bar: 0.01,
                    solve_pressure_max_bar: 100,
                    channel_count: 21,
                    review: { status: "not_reviewed" },
                }],
            })),
        )
        const record = await loadReactionEntry(ENTRY_REF)
        expect(record.species!.reactants[0].formula).toBe("H2O")
        expect(record.species!.reactants[0].stoichiometry).toBe(2)
        expect(record.kinetics![0].levels?.geometry?.method).toBe("b3lyp")
        expect(record.networks).toHaveLength(1)
        expect(record.networks![0].network_ref).toBe("net_a")
    })

    // MEASURED against the real archive (this PR's own headless-Chrome
    // verification pass, 2026-09-07): the live, pre-deployment API does
    // NOT silently omit an unrecognised `include` token -- it rejects the
    // WHOLE request with HTTP 422 `unknown_include_token`. Without this
    // retry, the entry page 500s outright against today's archive; this
    // test is the regression guard for that finding.
    //
    // `vi.resetModules()` + a fresh dynamic import isolates this test's
    // own copy of the module-level `networksIncludeSupported` cache from
    // every other test in this file (which would otherwise see whichever
    // value the LAST-run test left behind, since ES modules are
    // singletons within one test file).
    it("retries once without `networks` when the archive rejects it as an unknown include token, then remembers not to ask again", async () => {
        vi.resetModules()
        const { loadReactionEntry: freshLoadReactionEntry } = await import("./reactionEntryApi")

        let requestCount = 0
        server.use(http.get(`/api/v1/scientific/reaction-entries/${ENTRY_REF}/full`, ({ request }) => {
            requestCount += 1
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("networks")) {
                return HttpResponse.json(
                    { code: "unknown_include_token", detail: "unknown_include_token: token(s) ['networks'] not legal", context: {} },
                    { status: 422 },
                )
            }
            return HttpResponse.json({
                reaction_entry: baseReactionEntry,
                review_summary: { total: 0, not_reviewed: 0, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
                species: { reactants: [], products: [] },
                kinetics: [], transition_states: [], calculations: [],
            })
        }))

        const record = await freshLoadReactionEntry(ENTRY_REF)
        expect(requestCount).toBe(2) // one rejected attempt, one successful retry
        expect(record.networks).toBeUndefined()

        // A second navigation (a different ref, so the request cache does
        // not shortcut it) no longer wastes a request finding out the same
        // fact -- exactly one request, already excluding `networks`.
        server.use(http.get("/api/v1/scientific/reaction-entries/rxe_other/full", ({ request }) => {
            requestCount += 1
            const includes = new URL(request.url).searchParams.getAll("include")
            expect(includes).not.toContain("networks")
            return HttpResponse.json({
                reaction_entry: { ...baseReactionEntry, reaction_entry_ref: "rxe_other" },
                review_summary: { total: 0, not_reviewed: 0, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
                species: { reactants: [], products: [] },
                kinetics: [], transition_states: [], calculations: [],
            })
        }))
        const before = requestCount
        await freshLoadReactionEntry("rxe_other")
        expect(requestCount).toBe(before + 1)
    })
})

describe("loadReactionEntryNetworksFallback", () => {
    it("requests networks/search?reaction_entry_ref=...&include=reactions and maps records to NetworkMembership", async () => {
        let requestedUrl = ""
        server.use(
            http.get("/api/v1/scientific/networks/search", ({ request }) => {
                requestedUrl = request.url
                return HttpResponse.json({
                    records: [{
                        network: {
                            network_ref: "net_o6bt63kjeyvhvxx26w6kdi433a",
                            name: "hydrazine",
                            solve_temperature_min_k: 300,
                            solve_temperature_max_k: 2000,
                            solve_pressure_min_bar: 0.01,
                            solve_pressure_max_bar: 100,
                            review: { status: "not_reviewed" },
                        },
                        evidence_summary: { channel_count: 21 },
                    }],
                })
            }),
        )
        const networks = await loadReactionEntryNetworksFallback("rxe_gw4unjmagt7lzc6dmpjfm5t6xu")
        const url = new URL(requestedUrl)
        expect(url.searchParams.get("reaction_entry_ref")).toBe("rxe_gw4unjmagt7lzc6dmpjfm5t6xu")
        expect(url.searchParams.getAll("include")).toEqual(["reactions"])
        expect(networks).toEqual([{
            network_ref: "net_o6bt63kjeyvhvxx26w6kdi433a",
            name: "hydrazine",
            solve_temperature_min_k: 300,
            solve_temperature_max_k: 2000,
            solve_pressure_min_bar: 0.01,
            solve_pressure_max_bar: 100,
            channel_count: 21,
            review: { status: "not_reviewed" },
        }])
    })
})
