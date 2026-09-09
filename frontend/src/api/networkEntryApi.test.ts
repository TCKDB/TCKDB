import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadNetworkEntry } from "./networkEntryApi"

const NETWORK_REF = "net_test1"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function stateFixture(hash: string, participants: { ref: string; smiles: string }[]) {
    return {
        composition_hash: hash,
        kind: participants.length > 1 ? "bimolecular" : "well",
        label: null,
        participant_count: participants.length,
        composition: {
            participants: participants.map((p) => ({ species_entry_ref: p.ref, species_ref: `sp_${p.ref}`, canonical_smiles: p.smiles, stoichiometry: 1 })),
            participant_count_total: participants.length,
            participants_truncated: false,
            state_label: participants.map((p) => p.smiles).join(" + "),
        },
    }
}

function networkDetailFixture(overrides: Record<string, unknown> = {}) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
        record: {
            network: { network_ref: NETWORK_REF, name: "test network", description: null, solve_temperature_min_k: 300, solve_temperature_max_k: 2000, solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100, review: { status: "not_reviewed" } },
            software_release: null,
            workflow_tool_release: { workflow_tool_release_ref: "wfr_test1", workflow_tool: "ARC", version: "1.0" },
            literature: null,
            evidence_summary: {
                species_count: 3, reaction_count: 1, state_count: 2, channel_count: 1, solve_count: 1,
                kinetics_count: 2, source_calculation_count: 4, has_chebyshev: true, has_plog: false, has_point_kinetics: false,
            },
            states: [
                stateFixture("hash_a", [{ ref: "spe_a", smiles: "A" }]),
                stateFixture("hash_b", [{ ref: "spe_a", smiles: "A" }, { ref: "spe_b", smiles: "B" }]),
            ],
            channels: [
                {
                    channel_key: "channel_1", kind: "association", mechanism: "elementary",
                    source_state_composition_hash: "hash_b", sink_state_composition_hash: "hash_a", has_kinetics: true,
                    microreactions: [{ reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", path_kind: "saddle_point" }],
                },
            ],
            solves: [{ network_solve_ref: "nsolve_test1", kind: "computed", me_method: "modified strong collision", interpolation_model: "chebyshev", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" } }],
            ...overrides,
        },
    }
}

function handleNetworkDetail(payload: object, captureUrl?: (url: string) => void) {
    server.use(http.get(`/api/v1/scientific/networks/${NETWORK_REF}`, ({ request }) => {
        captureUrl?.(request.url)
        return HttpResponse.json(payload)
    }))
}

function handleSolveDetail(ref: string, payload: object, captureUrl?: (url: string) => void) {
    server.use(http.get(`/api/v1/scientific/network-solves/${ref}`, ({ request }) => {
        captureUrl?.(request.url)
        return HttpResponse.json(payload)
    }))
}

function thermoListPayload(entryRef: string, count: number) {
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 0, under_review: 0, not_reviewed: count, deprecated: 0, rejected: 0, total: count },
        records: Array.from({ length: count }, (_unused, i) => ({
            thermo_ref: `thermo_${entryRef}_${i}`,
            scientific_origin: "computed",
            model_kind: "nasa",
            review: { status: "not_reviewed" },
        })),
        pagination: { offset: 0, limit: 50, returned: count, total: count, post_collapse_total: count },
    }
}

function handleThermo(refs: Record<string, number>) {
    for (const [ref, count] of Object.entries(refs)) {
        server.use(http.get(`/api/v1/scientific/species-entries/${ref}/thermo`, () => HttpResponse.json(thermoListPayload(ref, count))))
    }
}

function handleReactionEntry(ref: string, equation: string) {
    server.use(http.get(`/api/v1/scientific/reaction-entries/${ref}/full`, () => HttpResponse.json({
        reaction_entry: { reaction_entry_ref: ref, reaction_ref: `rxn_${ref}`, equation, reversible: true, review: { status: "not_reviewed" }, atom_maps: [] },
        review_summary: { total: 1 },
    })))
}

describe("loadNetworkEntry", () => {
    it("requests the network detail with the three include tokens this page needs", async () => {
        let requestedUrl = ""
        handleNetworkDetail(networkDetailFixture(), (url) => { requestedUrl = url })
        handleSolveDetail("nsolve_test1", { record: { network_solve: { network_solve_ref: "nsolve_test1" }, state_energies: [], channel_barriers: [] } })
        handleThermo({ spe_a: 0, spe_b: 0 })
        handleReactionEntry("rxe_test1", "A <=> B")

        await loadNetworkEntry(NETWORK_REF)
        const includeValues = new URL(requestedUrl).searchParams.getAll("include")
        expect(includeValues).toEqual(["states", "channels", "solves"])
    })

    it("fetches thermo for every UNIQUE participant species entry across all states, once each", async () => {
        const calls: string[] = []
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1", { record: { network_solve: { network_solve_ref: "nsolve_test1" }, state_energies: [], channel_barriers: [] } })
        server.use(
            http.get("/api/v1/scientific/species-entries/spe_a/thermo", () => {
                calls.push("spe_a")
                return HttpResponse.json(thermoListPayload("spe_a", 0))
            }),
            http.get("/api/v1/scientific/species-entries/spe_b/thermo", () => {
                calls.push("spe_b")
                return HttpResponse.json(thermoListPayload("spe_b", 1))
            }),
        )
        handleReactionEntry("rxe_test1", "A <=> B")

        const record = await loadNetworkEntry(NETWORK_REF)
        // `spe_a` participates in BOTH states -- fetched once, not twice.
        expect(calls.filter((c) => c === "spe_a")).toHaveLength(1)
        expect(calls.filter((c) => c === "spe_b")).toHaveLength(1)
        expect(record.speciesEntryRefs.sort()).toEqual(["spe_a", "spe_b"])
        expect(record.speciesThermoPresence).toEqual({ spe_a: false, spe_b: true })
    })

    it("fetches the equation for every unique reaction entry a channel's microreaction names", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1", { record: { network_solve: { network_solve_ref: "nsolve_test1" }, state_energies: [], channel_barriers: [] } })
        handleThermo({ spe_a: 0, spe_b: 0 })
        handleReactionEntry("rxe_test1", "A <=> B")

        const record = await loadNetworkEntry(NETWORK_REF)
        expect(record.reactionEntries.rxe_test1.equation).toBe("A <=> B")
        expect(record.reactionEntries.rxe_test1.review.status).toBe("not_reviewed")
    })

    it("fetches the first solve's state_energies/channel_barriers include tokens", async () => {
        let solveUrl = ""
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail(
            "nsolve_test1",
            { record: { network_solve: { network_solve_ref: "nsolve_test1" }, state_energies: [{ state_composition_hash: "hash_a", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" }], channel_barriers: [] } },
            (url) => { solveUrl = url },
        )
        handleThermo({ spe_a: 0, spe_b: 0 })
        handleReactionEntry("rxe_test1", "A <=> B")

        const record = await loadNetworkEntry(NETWORK_REF)
        const includeValues = new URL(solveUrl).searchParams.getAll("include")
        expect(includeValues).toEqual(["state_energies", "channel_barriers"])
        expect(record.stateEnergies).toHaveLength(1)
    })

    it("skips the solve-detail fetch entirely when the network has no deposited solve", async () => {
        handleNetworkDetail(networkDetailFixture({ solves: [] }))
        handleThermo({ spe_a: 0, spe_b: 0 })
        handleReactionEntry("rxe_test1", "A <=> B")
        server.use(http.get("/api/v1/scientific/network-solves/:ref", () => {
            throw new Error("should not be called when there is no solve")
        }))

        const record = await loadNetworkEntry(NETWORK_REF)
        expect(record.stateEnergies).toBeNull()
        expect(record.channelBarriers).toBeNull()
    })
})
