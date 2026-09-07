import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadEntryStatmech } from "./statmechApi"
import { ScientificApiError } from "./scientificTransport"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/statmech`

/**
 * A minimally valid `statmechRecordSchema` payload -- every field
 * `statmechCoreSchema`/`evidenceSummarySchema`/`availableSectionsSchema`
 * requires (no `.nullable().optional()`), so a test can override just the
 * one field under test (here, `levels`) without every other required
 * field also failing validation.
 */
function minimalRecord(overrides: Record<string, unknown> = {}) {
    return {
        statmech: {
            statmech_ref: "sm_one",
            scientific_origin: "computed",
            created_at: "2026-07-21T12:14:32.845900",
            review: { status: "not_reviewed" },
        },
        evidence_summary: {
            source_calculation_count: 0,
            has_opt_calculation: false,
            has_freq_calculation: false,
            has_sp_calculation: false,
            has_rotor_scans: false,
            torsion_count: 0,
            has_frequency_scale_factor: false,
            has_conformer_context: false,
        },
        available_sections: {
            has_source_calculations: false,
            has_torsions: false,
            has_electronic_levels: false,
            has_frequencies: false,
            has_conformers: false,
            has_review: false,
        },
        ...overrides,
    }
}

function mockResponse(records: unknown[]) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

const geomLot = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_1" }
const energyLot = { method: "ccsd(t)", basis: "cc-pvtz", display: "ccsd(t)/cc-pvtz", level_of_theory_ref: "lot_2" }

describe("statmechRecordSchema's `levels` field", () => {
    it("parses a record with no `levels` key at all (older API) -- `record.levels` is undefined, not an error", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord()]))))
        const response = await loadEntryStatmech(entryRef)
        expect(response.records[0].levels).toBeUndefined()
    })

    it("parses `levels: null` and preserves it as null", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: null })]))))
        const response = await loadEntryStatmech(entryRef)
        expect(response.records[0].levels).toBeNull()
    })

    it("parses a full `levels` object with an unrecognised `energy_source` value ('ambiguous') and preserves it verbatim", async () => {
        const levels = { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "ambiguous" }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels })]))))
        const response = await loadEntryStatmech(entryRef)
        expect(response.records[0].levels).toEqual(levels)
    })

    it("parses `levels` with every field missing (an empty object) as all-undefined, not a validation error", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: {} })]))))
        const response = await loadEntryStatmech(entryRef)
        expect(response.records[0].levels).toEqual({})
    })

    /**
     * The behavioural proof that the `levels:` schema line in
     * `statmechRecordSchema` is doing real validation work, not sitting
     * inert under the record schema's own `.passthrough()`: a malformed
     * `levels.geometry` (missing `productLevelsSchema`'s required
     * `LevelOfTheory.method`) is REJECTED here. `.passthrough()` only
     * preserves UNKNOWN keys as-is -- it does not weaken validation of a
     * key the schema explicitly declares, so this still fails with the
     * line in place. Revert `levels: productLevelsSchema.nullable()
     * .optional(),` in `statmechApi.ts` and this test goes red: without a
     * declared schema for `levels`, passthrough lets ANY shape through
     * unchecked, and `loadEntryStatmech` no longer throws here.
     */
    it("rejects a malformed `levels.geometry` (missing the required `method` field) rather than silently passing it through", async () => {
        const malformed = { geometry: { basis: "def2tzvp" } } // no `method`
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: malformed })]))))
        await expect(loadEntryStatmech(entryRef)).rejects.toThrow(ScientificApiError)
    })

    it("rejects a malformed `levels.energy_source` (a number, not a string) rather than silently passing it through", async () => {
        const malformed = { energy_source: 42 }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([minimalRecord({ levels: malformed })]))))
        await expect(loadEntryStatmech(entryRef)).rejects.toThrow(ScientificApiError)
    })
})
