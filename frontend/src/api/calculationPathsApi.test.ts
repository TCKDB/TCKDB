import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadPathSearchPoints } from "./calculationPathsApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/calculations/calc_ps/path-search"

const SUMMARY = {
    method: "gsm",
    is_double_ended: true,
    converged: null,
    n_points: 3,
    selected_ts_point_index: 1,
    stored_point_count: 3,
    ts_guess_count: 1,
    climbing_image_count: 0,
}

const point = (index: number) => ({
    point_index: index,
    path_coordinate: index,
    electronic_energy_hartree: null,
    is_ts_guess: index === 1,
    is_climbing_image: false,
    geometry_ref: `geom_${index}`,
    geometry_link: { geometry_ref: `geom_${index}`, natoms: 9, geom_hash: "x" },
})

describe("loadPathSearchPoints", () => {
    it("asks for geometries and follows the cursor until the last page", async () => {
        const seen: URLSearchParams[] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            const query = new URL(request.url).searchParams
            seen.push(query)
            const cursor = query.get("cursor")
            if (!cursor) return HttpResponse.json({ path_search: SUMMARY, points: [point(0), point(1)], pagination: { returned: 2, total: 3 }, next_cursor: "page2" })
            return HttpResponse.json({ path_search: SUMMARY, points: [point(2)], pagination: { returned: 1, total: 3 }, next_cursor: null })
        }))
        const read = await loadPathSearchPoints("calc_ps")
        expect(read.points.map((p) => p.point_index)).toEqual([0, 1, 2])
        expect(read.summary.selected_ts_point_index).toBe(1)
        expect(seen).toHaveLength(2)
        expect(seen[0].get("include_geometries")).toBe("true")
        expect(seen[0].get("limit")).toBe("200")
        expect(seen[1].get("cursor")).toBe("page2")
    })

    it("pages by offset when the read gives no cursor", async () => {
        const offsets: (string | null)[] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            const offset = new URL(request.url).searchParams.get("offset")
            offsets.push(offset)
            const points = offset === "2" ? [point(2)] : [point(0), point(1)]
            return HttpResponse.json({ path_search: SUMMARY, points, pagination: { returned: points.length, total: 3 }, next_cursor: null })
        }))
        const read = await loadPathSearchPoints("calc_ps")
        expect(read.points).toHaveLength(3)
        expect(offsets).toEqual([null, "2"])
    })
})
