import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadSpeciesSinglePointEnergies } from "./speciesCalculationsApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function record(index: number) {
    return {
        calculation: { calculation_ref: `calc_sp_${index}`, calculation_type: "sp" },
        energy: { energy_hartree: -76 - index / 1000, energy_kind: "electronic_energy" },
    }
}

describe("loadSpeciesSinglePointEnergies", () => {
    it("reads every page, so an energy past the first page is not silently missing", async () => {
        const total = 450
        const seen: string[] = []
        server.use(http.get("/api/v1/scientific/species-calculations/search", ({ request }) => {
            const params = new URL(request.url).searchParams
            const offset = Number(params.get("offset") ?? 0)
            const limit = Number(params.get("limit") ?? 50)
            seen.push(`${offset}:${limit}`)
            const records = Array.from({ length: total }, (_, index) => record(index)).slice(offset, offset + limit)
            return HttpResponse.json({ records, pagination: { offset, limit, returned: records.length, total } })
        }))
        const { records, capped } = await loadSpeciesSinglePointEnergies("spe_x")
        expect(capped).toBe(false)
        expect(records).toHaveLength(total)
        expect(records.at(-1)?.calculation.calculation_ref).toBe("calc_sp_449")
        expect(seen).toEqual(["0:200", "200:200", "400:200"])
    })

    it("a single page makes a single request", async () => {
        let requests = 0
        server.use(http.get("/api/v1/scientific/species-calculations/search", () => {
            requests += 1
            return HttpResponse.json({ records: [record(1)], pagination: { offset: 0, limit: 200, returned: 1, total: 1 } })
        }))
        await expect(loadSpeciesSinglePointEnergies("spe_x")).resolves.toMatchObject({ capped: false, records: [expect.anything()] })
        expect(requests).toBe(1)
    })

    it("still resolves to an empty list on a failure, as before", async () => {
        server.use(http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({ detail: "down" }, { status: 503 })))
        await expect(loadSpeciesSinglePointEnergies("spe_x")).resolves.toEqual({ records: [], capped: false })
    })

    it("says it stopped when the page cap is reached, instead of presenting the unread energies as absent", async () => {
        let requests = 0
        server.use(http.get("/api/v1/scientific/species-calculations/search", ({ request }) => {
            requests += 1
            const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0)
            return HttpResponse.json({ records: [record(offset)], pagination: { offset, limit: 200, returned: 200, total: 100000 } })
        }))
        const result = await loadSpeciesSinglePointEnergies("spe_x")
        expect(result.capped).toBe(true)
        expect(requests).toBe(50)
    })
})
