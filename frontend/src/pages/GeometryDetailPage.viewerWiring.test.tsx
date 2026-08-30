import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"

/**
 * A post-review fix: `GeometryDetailPage.test.tsx` had a test titled
 * "forwards this geometry's own xyz_text to the viewer section" that
 * actually only checked the separate Raw XYZ block — passing
 * `xyzText={null}` to `<GeometryViewer>` left every test in that file
 * green. This file closes that seam directly, by mocking
 * `../components/GeometryViewer` to record the exact props it receives
 * (mirroring the pattern `GeometryDetailPage.errorBoundary.test.tsx`
 * already uses, and split into its own file for the same reason: `vi.mock`
 * is hoisted, and would otherwise replace the real, unmocked
 * `GeometryViewer` every other test in `GeometryDetailPage.test.tsx`
 * relies on).
 */
type CapturedProps = { atoms: unknown; formula: unknown; xyzText: unknown }
let captured: CapturedProps[] = []

vi.mock("../components/GeometryViewer", () => ({
    GeometryViewer: (props: CapturedProps) => {
        captured.push(props)
        return null
    },
}))

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => {
    captured = []
})
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/geometries/geom_ch4_one"

function page() {
    return render(
        <MemoryRouter initialEntries={["/geometries/geom_ch4_one"]}>
            <Routes>
                <Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        geometry_ref: "geom_ch4_one",
        natoms: 2,
        geom_hash: "ab12cd34".repeat(8),
        format: "cartesian",
        coordinate_units: "angstrom",
        symbols: ["C", "H"],
        coords: [[0, 0, 0], [0, 0, 1.09]],
        atoms: [
            { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
            { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
        ],
        xyz_text: "2\n\nC 0.000000 0.000000 0.000000\nH 0.000000 0.000000 1.090000",
        created_at: "2026-07-21T12:06:50.748258",
        provenance: {},
        ...overrides,
    }
}

describe("GeometryDetailPage — GeometryViewer prop wiring", () => {
    it("passes this geometry's own xyz_text through unmodified — not null, not the raw XYZ block's rendered text, the API field itself", async () => {
        const record = mockRecord()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
        page()
        await screen.findByRole("heading", { name: "CH geometry" })
        expect(captured).toHaveLength(1)
        expect(captured[0].xyzText).toBe(record.xyz_text)
        expect(captured[0].atoms).toEqual(record.atoms)
        expect(captured[0].formula).toBe("CH")
    })

    it("passes null (not undefined, not an empty string) when the archive did not return xyz_text", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ xyz_text: null }))))
        page()
        await screen.findByRole("heading", { name: "CH geometry" })
        expect(captured).toHaveLength(1)
        expect(captured[0].xyzText).toBeNull()
    })
})
