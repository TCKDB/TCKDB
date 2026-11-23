import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadCalculation, loadCalculationSection } from "./calculationApi"
import { ScientificApiError } from "./scientificTransport"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/calculations/calc_demo"

/**
 * A minimally valid `calculationRecordSchema` payload -- every field
 * `calculationRecordSchema`/`availableSectionsSchema`/`provenanceSchema`
 * requires (no `.nullable().optional()` or `.default()`), so a test can
 * override just the one field under test (here, an input-geometry link's
 * `source`) without every other required field also failing validation.
 */
function minimalRecord(overrides: Record<string, unknown> = {}) {
    return {
        calculation: {
            calculation_ref: "calc_demo",
            type: "opt",
            quality: "raw",
            created_at: "2026-07-21T12:06:50.748258",
            review: { status: "not_reviewed" },
        },
        owner: { kind: "species_entry" },
        provenance: {
            has_result: true,
            geometry_validation_status: "not_present",
            scf_stability_status: "not_present",
        },
        available_sections: {
            has_results: true,
            has_dependencies: true,
            has_parameters: true,
            has_constraints: true,
            has_artifacts: true,
            has_input_geometries: true,
            has_output_geometries: true,
            has_geometry_validation: true,
            has_scf_stability: true,
            has_wavefunction_diagnostic: true,
            has_spin_diagnostic: true,
            has_freq_modes: true,
            has_hessian: true,
            has_scan: false,
            has_irc: false,
            has_path_search: false,
            has_execution_environment: false,
            has_energy_corrections: true,
        },
        ...overrides,
    }
}

describe("geometryLinkSchema's source field", () => {
    // `source` is the backend's
    // `CalculationInputGeometrySource` enum (`"deposited"` /
    // `"extracted_from_artifact"`), not a bare string -- an unexpected
    // third value must fail validation visibly (surfacing as the
    // archive's own "malformed data" error) rather than silently
    // matching neither branch `CalculationDetailPage.tsx` checks it
    // against.
    it("rejects an input geometry link whose source is not one of the two known enum values", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: minimalRecord({
                input_geometries: [{ geometry_ref: "geom_x", source: "not_a_real_source" }],
            }),
        })))
        await expect(loadCalculation("calc_demo")).rejects.toEqual(
            expect.objectContaining<Partial<ScientificApiError>>({ status: 200, message: "Archive returned malformed calculation data." }),
        )
    })

    it("accepts both known source values, and accepts an input geometry link with no source at all", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: minimalRecord({
                input_geometries: [
                    { geometry_ref: "geom_deposited", source: "deposited" },
                    { geometry_ref: "geom_extracted", source: "extracted_from_artifact" },
                    { geometry_ref: "geom_unset" },
                ],
            }),
        })))
        const record = await loadCalculation("calc_demo")
        expect(record.input_geometries?.map((link) => link.source)).toEqual(["deposited", "extracted_from_artifact", undefined])
    })
})

const WARNING_ITEM = {
    code: "missing_irc_for_ts",
    message: "This transition state has no IRC calculation.",
    field: "calculations[*]",
    register_entry: 19,
    context: { calculation_type: "irc" },
    check_version: 1,
}

describe("deposit warnings and trust on the calculation read", () => {
    it("exposes the envelope's record_warnings as typed depositWarnings", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: minimalRecord(),
            record_warnings: [{ record_ref: "calc_demo", status: "recorded", items: [WARNING_ITEM] }],
            deposit_warnings: [{ items: [{ ...WARNING_ITEM, code: "whole_deposit", register_entry: null }] }],
        })))
        const record = await loadCalculation("calc_demo")
        expect(record.depositWarnings.record_warnings[0].status).toBe("recorded")
        expect(record.depositWarnings.record_warnings[0].items[0].register_entry).toBe(19)
        expect(record.depositWarnings.deposit_warnings[0].items[0].register_entry).toBeNull()
    })

    it("defaults to empty warnings when the envelope carries none", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ record: minimalRecord() })))
        const record = await loadCalculation("calc_demo")
        expect(record.depositWarnings).toEqual({ record_warnings: [], deposit_warnings: [] })
    })

    it("asks for include=trust on the eager read and parses the verdict", async () => {
        let includes: string[] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            includes = new URL(request.url).searchParams.getAll("include")
            return HttpResponse.json({ record: minimalRecord({ trust: {
                review_status: "not_reviewed", trust_status: "hard_failed",
                evidence: { passed_count: 1, possible_count: 4, checks: { artifacts_present: "missing" }, hard_fail_reason: "calculation_rejected" },
            } }) })
        }))
        const record = await loadCalculation("calc_demo")
        expect(includes).toContain("trust")
        expect(record.trust?.evidence.hard_fail_reason).toBe("calculation_rejected")
    })

    it("returns warnings from an on-demand section read too", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            record: minimalRecord(),
            record_warnings: [{ record_ref: "calc_demo", status: "not_recorded", items: [WARNING_ITEM] }],
        })))
        const record = await loadCalculationSection("calc_demo", "artifacts")
        expect(record.depositWarnings.record_warnings[0].status).toBe("not_recorded")
    })
})
