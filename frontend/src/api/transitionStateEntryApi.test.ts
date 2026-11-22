import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadTransitionStateEntry } from "./transitionStateEntryApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const REF = "tse_demo"
const ENDPOINT = `/api/v1/scientific/transition-state-entries/${REF}`

function record(overrides: Record<string, unknown> = {}) {
    return {
        transition_state_entry: { transition_state_entry_ref: REF, charge: 0, multiplicity: 2, status: "optimized", created_at: "2026-08-05T14:04:16", review: { status: "not_reviewed" } },
        transition_state: { transition_state_ref: "ts_demo", label: "TS0", note: null, created_at: "2026-08-05T14:04:16", review: { status: "not_reviewed" } },
        reaction: { reaction_ref: "rxn_demo", reaction_entry_ref: "rxe_demo", equation: "A <=> B", reversible: true, family: null },
        evidence_summary: { calculation_count: 1, has_opt: true, has_freq: true, has_sp: false, has_irc: false, has_path_search: false, has_geometry_validation: false, has_scf_stability: false, levels_of_theory: {} },
        validation: { irc: "absent" },
        available_sections: { has_entries: true, has_calculations: true, has_geometries: true, has_review: true, has_validation_evidence: false },
        ...overrides,
    }
}

describe("loadTransitionStateEntry", () => {
    it("exposes record_warnings as typed depositWarnings", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: record(),
            record_warnings: [{ record_ref: REF, status: "recorded", items: [{ code: "missing_irc_for_ts", message: "No IRC.", field: "calculations[*]", register_entry: 19, context: null, check_version: 1 }] }],
        })))
        const loaded = await loadTransitionStateEntry(REF)
        expect(loaded.depositWarnings.record_warnings[0].items[0].register_entry).toBe(19)
    })

    it("still parses the structure assessment and the full trust fragment", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: record({ trust: {
            review_status: "not_reviewed", trust_status: "partial",
            evidence: { passed_count: 3, possible_count: 6, checks: { irc_evidence_present: "missing" }, hard_fail_reason: null },
            structure_assessment: { status: "assessed", outcome: "qualified_evidence" },
        } }) })))
        const loaded = await loadTransitionStateEntry(REF)
        expect(loaded.trust?.structure_assessment?.status).toBe("assessed")
        expect(loaded.trust?.review_status).toBe("not_reviewed")
        expect(loaded.depositWarnings).toEqual({ record_warnings: [], deposit_warnings: [] })
    })
})
