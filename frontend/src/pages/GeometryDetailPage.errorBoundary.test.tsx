import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"

// vitest hoists `vi.mock` calls to the top of the file at transform time
// regardless of where they're written, so `GeometryDetailPage` above
// receives a `GeometryViewer` that throws on every render — simulating a
// bug in the 3D viewer (a malformed atom row, a bad prop) without needing
// to actually construct one or a real WebGL context. Kept in its own
// file, separate from `GeometryDetailPage.test.tsx`, because this mock
// would otherwise break every other test in that file that expects a
// real viewer to render.
vi.mock("../components/GeometryViewer", () => ({
    GeometryViewer: () => {
        throw new Error("viewer boom")
    },
}))

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
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

function mockRecord() {
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
        provenance: {
            produced_by: [{ calculation_ref: "calc_opt_one", calculation_type: "opt", role: "final" }],
            used_as_input_by: [{ calculation_ref: "calc_freq_one", calculation_type: "freq", role: null }],
        },
    }
}

describe("GeometryDetailPage — a broken structure view", () => {
    it("leaves the coordinate table, raw XYZ block and both provenance tables standing", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        // The error boundary's own componentDidCatch logs to console.error;
        // that is expected here and not itself under test.
        const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {})

        page()
        await screen.findByRole("heading", { name: "CH geometry" })

        const viewerSection = screen.getByRole("heading", { name: "Structure view" }).closest("section") as HTMLElement
        expect(within(viewerSection).getByRole("alert")).toHaveTextContent(/could not be drawn/)
        expect(viewerSection.querySelector("svg")).toBeNull()

        // Before the SectionErrorBoundary existed, GeometryViewer throwing
        // unmounted the entire route — every one of the following would
        // have failed alongside it.
        const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
        expect(within(table).getByText("C")).toBeVisible()
        expect(within(table).getByText("H")).toBeVisible()

        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
        expect(within(xyzSection).getByText(/H 0\.000000 0\.000000 1\.090000/)).toBeVisible()

        // Two sections legitimately link "calc_opt_one" (the produced-by
        // table and the validation coverage-card note), so this scopes to
        // the produced-by table specifically rather than asserting a
        // single page-wide match.
        const producedSection = screen.getByRole("heading", { name: "Produced by" }).closest("section") as HTMLElement
        expect(within(producedSection).getByRole("link", { name: "calc_opt_one" }))
            .toHaveAttribute("href", "/calculations/calc_opt_one")
        const consumedSection = screen.getByRole("heading", { name: "Used as input by" }).closest("section") as HTMLElement
        expect(within(consumedSection).getByRole("link", { name: "calc_freq_one" }))
            .toHaveAttribute("href", "/calculations/calc_freq_one")

        consoleSpy.mockRestore()
    })
})
