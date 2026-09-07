import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadEntryThermo } from "./thermoApi"
import { ScientificApiError } from "./scientificTransport"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/thermo`

/** A minimally valid `thermoRecordSchema` payload -- every field it
 *  requires (no `.nullable().optional()`), so a test can override just
 *  the one field under test (here, `levels`) without every other
 *  required field also failing validation. */
function minimalRecord(overrides: Record<string, unknown> = {}) {
    return {
        thermo_ref: "thm_one",
        scientific_origin: "computed",
        model_kind: "nasa",
        review: { status: "not_reviewed" },
        ...overrides,
    }
}

function mockResponse(records: unknown[]) {
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

const geomLot = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_1" }
const energyLot = { method: "ccsd(t)", basis: "cc-pvtz", display: "ccsd(t)/cc-pvtz", level_of_theory_ref: "lot_2" }

describe("thermoRecordSchema's `levels` field", () => {
    it("parses a record with no `levels` key at all (older API) -- `record.levels` is undefined, not an error", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord()]))))
        const response = await loadEntryThermo(entryRef)
        expect(response.records[0].levels).toBeUndefined()
    })

    it("parses `levels: null` and preserves it as null", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: null })]))))
        const response = await loadEntryThermo(entryRef)
        expect(response.records[0].levels).toBeNull()
    })

    it("parses a full `levels` object with an unrecognised `energy_source` value ('ambiguous') and preserves it verbatim", async () => {
        const levels = { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "ambiguous" }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels })]))))
        const response = await loadEntryThermo(entryRef)
        expect(response.records[0].levels).toEqual(levels)
    })

    it("parses `levels` with every field missing (an empty object) as all-undefined, not a validation error", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: {} })]))))
        const response = await loadEntryThermo(entryRef)
        expect(response.records[0].levels).toEqual({})
    })

    /**
     * The behavioural proof that the `levels:` schema line in
     * `thermoRecordSchema` is doing real validation work, not sitting
     * inert under the record schema's own `.passthrough()` -- see
     * `statmechApi.test.ts`'s identical pair for the full rationale.
     * Revert `levels: productLevelsSchema.nullable().optional(),` in
     * `thermoApi.ts` and these two go red.
     */
    it("rejects a malformed `levels.geometry` (missing the required `method` field) rather than silently passing it through", async () => {
        const malformed = { geometry: { basis: "def2tzvp" } } // no `method`
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: malformed })]))))
        await expect(loadEntryThermo(entryRef)).rejects.toThrow(ScientificApiError)
    })

    it("rejects a malformed `levels.energy_source` (a number, not a string) rather than silently passing it through", async () => {
        const malformed = { energy_source: 42 }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: malformed })]))))
        await expect(loadEntryThermo(entryRef)).rejects.toThrow(ScientificApiError)
    })
})
