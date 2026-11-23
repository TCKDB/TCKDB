import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadReactionEntryKineticsAssessments } from "./reactionKineticsApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ENTRY = "rxe_demo"

function kinetics(ref: string, extra: Record<string, unknown> = {}) {
    return {
        kinetics_ref: ref, scientific_origin: "computed", model_kind: "modified_arrhenius", review: { status: "not_reviewed" },
        parameters: { A: 1, A_units: "cm3_mol_s", n: 0, Ea_kj_mol: 10 }, is_third_body: false, uncertainty: {},
        evidence_completeness: { score: 0, max: 9, checklist: {} }, provenance: {}, ...extra,
    }
}

describe("loadReactionEntryKineticsAssessments", () => {
    it("sends include=trust, include=assessments and limit=200, maps by kinetics_ref and carries the warnings", async () => {
        let url: URL | null = null
        server.use(http.get(`/api/v1/scientific/reaction-entries/${ENTRY}/kinetics`, ({ request }) => {
            url = new URL(request.url)
            return HttpResponse.json({
                records: [
                    kinetics("kin_a", {
                        trust: { review_status: "not_reviewed", trust_status: "partial", evidence: { passed_count: 1, possible_count: 2, checks: { arrhenius_parameters_complete: "passed" } } },
                        assessments: { reproducibility: { state: "current", grade: "described", made_under: { state: "inputs_unchecked" } } },
                    }),
                    kinetics("kin_b"),
                ],
                record_warnings: [{ record_ref: "kin_a", status: "recorded", items: [{ code: "c", message: "m", field: "f", register_entry: 5, check_version: 1 }] }],
            })
        }))
        const result = await loadReactionEntryKineticsAssessments(ENTRY)
        expect(url!.searchParams.getAll("include")).toEqual(["trust", "assessments"])
        expect(url!.searchParams.get("limit")).toBe("200")
        expect(result.byKineticsRef.get("kin_a")?.trust?.trust_status).toBe("partial")
        expect(result.byKineticsRef.get("kin_a")?.assessments?.reproducibility?.grade).toBe("described")
        expect(result.byKineticsRef.get("kin_b")?.trust).toBeUndefined()
        expect(result.byKineticsRef.has("kin_c")).toBe(false)
        expect(result.depositWarnings.record_warnings[0].items[0].register_entry).toBe(5)
    })
})
