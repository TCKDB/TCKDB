import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadCalculation } from "./calculationApi"
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
    // #384 (merged, `bc3fd2d2`): `source` is the backend's
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
