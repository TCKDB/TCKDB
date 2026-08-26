import { http, HttpResponse } from "msw"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { setupServer } from "msw/node"
import { searchSpeciesExact, ScientificApiError } from "./scientificApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe("searchSpeciesExact", () => {
    it("uses the scientific species endpoint for formula", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            expect(new URL(request.url).searchParams.get("formula")).toBe("H2O")
            return HttpResponse.json({ records: [{ species_ref: "spec_water", entries: [{ species_entry_ref: "se_water" }] }] })
        }))
        await expect(searchSpeciesExact({ kind: "formula", value: "H2O" })).resolves.toEqual([{ speciesRef: "spec_water", entryRef: "se_water" }])
    })

    it("uses exact structure search for InChIKey", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            const query = new URL(request.url).searchParams
            expect(query.get("query_inchi_key")).toBe("XLYOFNOQVPJJNP-UHFFFAOYSA-N")
            expect(query.get("mode")).toBe("exact")
            return HttpResponse.json({ records: [{ species_ref: "spec_water", species_entry_ref: "se_water" }] })
        }))
        await expect(searchSpeciesExact({ kind: "inchi-key", value: "XLYOFNOQVPJJNP-UHFFFAOYSA-N" })).resolves.toHaveLength(1)
    })

    it("exposes non-success responses as useful API errors", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ detail: "unsupported_filter" }, { status: 422 })))
        await expect(searchSpeciesExact({ kind: "formula", value: "H2O" })).rejects.toEqual(expect.objectContaining<Partial<ScientificApiError>>({ status: 422, message: "unsupported_filter" }))
    })
})
