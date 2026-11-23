import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadEntryTransportWithSources, loadTransportDepositWarnings } from "./transportApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/transport`

function minimalRecord(overrides: Record<string, unknown> = {}) {
    return {
        transport: { transport_ref: "tr_one", scientific_origin: "computed", created_at: "2026-07-21T12:14:32", review: { status: "not_reviewed" } },
        species: { species_ref: "sp_1", species_entry_ref: entryRef },
        evidence_summary: {
            source_calculation_count: 0, has_source_calculations: false, has_lj_parameters: true, has_dipole_moment: false,
            has_polarizability: false, has_rotational_relaxation: false, has_literature_source: false,
        },
        available_sections: { has_source_calculations: false, has_review: false },
        ...overrides,
    }
}

function mockResponse(records: unknown[]) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length },
    }
}

describe("trust and assessments on the transport list", () => {
    it("asks for include=trust and include=assessments and parses both on the record", async () => {
        let includes: string[] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            includes = new URL(request.url).searchParams.getAll("include")
            return HttpResponse.json(mockResponse([minimalRecord({
                trust: { review_status: "not_reviewed", trust_status: "sparse", evidence: { passed_count: 1, possible_count: 5, checks: { epsilon_present: "passed" } } },
                assessments: { reproducibility: { state: "current", grade: "auditable", made_under: { state: "inputs_unchecked" } } },
            })]))
        }))
        const response = await loadEntryTransportWithSources(entryRef)
        expect(includes).toEqual(expect.arrayContaining(["trust", "assessments", "source_calculations"]))
        expect(response.records[0].trust?.trust_status).toBe("sparse")
        expect(response.records[0].assessments?.reproducibility?.grade).toBe("auditable")
    })

    it("loadTransportDepositWarnings reads the by-ref record's warnings", async () => {
        server.use(http.get("/api/v1/scientific/transport/tr_one", () => HttpResponse.json({
            record: minimalRecord(),
            record_warnings: [{ record_ref: "tr_one", status: "recorded", items: [{ code: "c", message: "m", field: "f", register_entry: 3, check_version: 1 }] }],
        })))
        const warnings = await loadTransportDepositWarnings("tr_one")
        expect(warnings.record_warnings[0].items[0].register_entry).toBe(3)
    })
})
