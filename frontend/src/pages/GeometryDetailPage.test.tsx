import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"

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

/**
 * A 5-atom CH4 geometry (modelled on the shape of the live
 * geom_qcnisbgb4abax5oxym3dtjxu34 / geom_or52ifyemdi3eewsjym2fuvo3a
 * fixtures measured against https://tckdb.homecalvin.com). Provenance is
 * deliberately built with THREE producers and THREE consumers, in an
 * order that is not alphabetical by ref and not identical between the
 * two lists — a single-edge fixture cannot distinguish "read this row's
 * own fields" from "always show the first row", and a fixture whose two
 * lists happen to share an order cannot catch a client-side re-sort. One
 * calculation (`calc_opt_two`) is deliberately present in BOTH lists —
 * measured live behaviour (calc_htgb7s5nakuw52eqhcxpvilpoq on
 * geom_or52ifyemdi3eewsjym2fuvo3a), not a hypothetical — so a mutation
 * that merges, sums, or conflates the two lists is observable.
 */
function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        geometry_ref: "geom_ch4_one",
        natoms: 5,
        geom_hash: "hash_ch4",
        format: "cartesian",
        coordinate_units: "angstrom",
        symbols: ["C", "H", "H", "H", "H"],
        coords: [[0, 0, 0], [0.11, 0.22, 0.33], [-0.63, -0.63, 0.63], [-0.63, 0.63, -0.63], [0.63, -0.63, -0.63]],
        atoms: [
            { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
            { atom_index: 2, element: "H", x: 0.11, y: 0.22, z: 0.33 },
            { atom_index: 3, element: "H", x: -0.63, y: -0.63, z: 0.63 },
            { atom_index: 4, element: "H", x: -0.63, y: 0.63, z: -0.63 },
            { atom_index: 5, element: "H", x: 0.63, y: -0.63, z: -0.63 },
        ],
        xyz_text: "5\n\nC 0.000000 0.000000 0.000000\nH 0.110000 0.220000 0.330000\nH -0.630000 -0.630000 0.630000\nH -0.630000 0.630000 -0.630000\nH 0.630000 -0.630000 -0.630000",
        created_at: "2026-07-21T12:06:50.748258",
        provenance: {
            produced_by: [
                { calculation_ref: "calc_opt_two", calculation_type: "opt", role: "final" },
                { calculation_ref: "calc_opt_one", calculation_type: "opt", role: "coarse" },
            ],
            used_as_input_by: [
                { calculation_ref: "calc_freq_one", calculation_type: "freq", role: null },
                { calculation_ref: "calc_opt_two", calculation_type: "opt", role: null },
                { calculation_ref: "calc_sp_one", calculation_type: "sp", role: null },
            ],
        },
        ...overrides,
    }
}

describe("GeometryDetailPage", () => {
    it("requests include=provenance and renders a Hill-order formula heading", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            expect(new URL(request.url).searchParams.getAll("include")).toEqual(["provenance"])
            return HttpResponse.json(mockRecord())
        }))
        page()
        expect(await screen.findByRole("heading", { name: "CH4 geometry" })).toBeVisible()
    })

    it("renders a single-level breadcrumb, since this endpoint carries no owner/species-entry link", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(breadcrumb).getByRole("link", { name: "TCKDB" })).toHaveAttribute("href", "/")
        expect(within(breadcrumb).getByText("Geometry")).toBeVisible()
        expect(within(breadcrumb).queryByRole("link", { name: /species/i })).not.toBeInTheDocument()
    })

    it("shows the geometry ref, hash, format and units — never label-only", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const context = screen.getByText("Geometry ref").closest(".basin-context") as HTMLElement
        expect(within(context).getByText("geom_ch4_one")).toBeVisible()
        expect(within(context).getByText("hash_ch4")).toBeVisible()
        expect(within(context).getByText("cartesian")).toBeVisible()
        expect(within(context).getByText("angstrom")).toBeVisible()
    })

    it("says validation is not recorded on this endpoint, rather than fabricating a verdict", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")
        expect(within(summary).getByText("Not recorded on this endpoint")).toBeVisible()
        expect(within(summary).queryByText(/passed/i)).not.toBeInTheDocument()
        expect(within(summary).queryByText(/failed/i)).not.toBeInTheDocument()
    })

    it("renders every atom row in the coordinate table, in payload order", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
        const rows = within(table).getAllByRole("row").slice(1)
        expect(rows).toHaveLength(5)
        const firstDataRow = rows[0]
        expect(within(firstDataRow).getByText("1")).toBeVisible()
        expect(within(firstDataRow).getByText("C")).toBeVisible()
        // Distinct x/y/z values per column, scoped to their own cell — a
        // mutation that swapped which coordinate lands in which column
        // (or read one atom's coordinate for another's row) is observable
        // here, unlike a fixture whose x/y/z happened to share one value.
        const secondDataRow = rows[1]
        expect(within(secondDataRow).getByText("0.11", { selector: "[data-label='x']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.22", { selector: "[data-label='y']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.33", { selector: "[data-label='z']" })).toBeVisible()
    })

    it("renders the raw XYZ text verbatim in a selectable block", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
        expect(within(xyzSection).getByText(/H 0\.110000 0\.220000 0\.330000/)).toBeVisible()
    })

    it("renders the projection with one atom mark per atom row", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const viewerSection = screen.getByRole("heading", { name: "Structure projection" }).closest("section") as HTMLElement
        const svg = viewerSection.querySelector("svg.viewer-svg") as SVGElement
        expect(svg).not.toBeNull()
        expect(svg.querySelectorAll("circle")).toHaveLength(5)
        expect(within(viewerSection).getByText(/not an interactive 3D molecular viewer/)).toBeVisible()
    })

    it("keeps the projection's atom count stable across rotation", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const viewerSection = screen.getByRole("heading", { name: "Structure projection" }).closest("section") as HTMLElement
        fireEvent.click(within(viewerSection).getByRole("button", { name: "Rotate right" }))
        fireEvent.click(within(viewerSection).getByRole("button", { name: "Rotate up" }))
        const svg = viewerSection.querySelector("svg.viewer-svg") as SVGElement
        expect(svg.querySelectorAll("circle")).toHaveLength(5)
    })

    it("binds each produced-by row's own type and role — not the first row's", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Produced by" }).closest("section") as HTMLElement

        const rowOne = within(section).getByRole("link", { name: "calc_opt_two" }).closest("tr") as HTMLElement
        expect(within(rowOne).getByText("final")).toBeVisible()
        expect(within(rowOne).queryByText("coarse")).not.toBeInTheDocument()

        const rowTwo = within(section).getByRole("link", { name: "calc_opt_one" }).closest("tr") as HTMLElement
        expect(within(rowTwo).getByText("coarse")).toBeVisible()
        expect(within(rowTwo).queryByText("final")).not.toBeInTheDocument()

        // Row order is read verbatim from the payload, not re-sorted.
        const rows = within(section).getAllByRole("row").slice(1)
        const refs = rows.map((row) => within(row).getByRole("link").textContent)
        expect(refs).toEqual(["calc_opt_two", "calc_opt_one"])
    })

    it("never merges producer and consumer rows, even for a calculation that appears in both lists", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })

        const producedSection = screen.getByRole("heading", { name: "Produced by" }).closest("section") as HTMLElement
        const consumedSection = screen.getByRole("heading", { name: "Used as input by" }).closest("section") as HTMLElement

        // calc_opt_two is genuinely in both lists — each section shows it
        // exactly once, with that section's own role semantics.
        expect(within(producedSection).getAllByRole("link", { name: "calc_opt_two" })).toHaveLength(1)
        expect(within(consumedSection).getAllByRole("link", { name: "calc_opt_two" })).toHaveLength(1)
        const producedRow = within(producedSection).getByRole("link", { name: "calc_opt_two" }).closest("tr") as HTMLElement
        expect(within(producedRow).getByText("final")).toBeVisible()

        // calc_opt_one only produced it — never listed as a consumer.
        expect(within(producedSection).getByRole("link", { name: "calc_opt_one" })).toBeVisible()
        expect(within(consumedSection).queryByRole("link", { name: "calc_opt_one" })).not.toBeInTheDocument()

        // calc_freq_one and calc_sp_one only consumed it — never listed as producers.
        expect(within(consumedSection).getByRole("link", { name: "calc_freq_one" })).toBeVisible()
        expect(within(consumedSection).getByRole("link", { name: "calc_sp_one" })).toBeVisible()
        expect(within(producedSection).queryByRole("link", { name: "calc_freq_one" })).not.toBeInTheDocument()
        expect(within(producedSection).queryByRole("link", { name: "calc_sp_one" })).not.toBeInTheDocument()

        // No role column at all on the consumer table — CalculationInputGeometry
        // has no role column (it is always null on every input-link row), so
        // this page does not render a Role column here rather than
        // borrowing a role from the producer side or printing a fake value.
        expect(within(consumedSection).queryByRole("columnheader", { name: "Role" })).not.toBeInTheDocument()
        expect(within(producedSection).getByRole("columnheader", { name: "Role" })).toBeVisible()

        // The metric counts are independent, not a combined tally: three
        // producer edges and three consumer edges, never 6, never 5 (deduped).
        const summary = screen.getByLabelText("Geometry provenance summary")
        const producingMetric = within(summary).getByText("Producing calculations").closest(".metric") as HTMLElement
        const consumingMetric = within(summary).getByText("Consuming calculations").closest(".metric") as HTMLElement
        expect(within(producingMetric).getByText("2")).toBeVisible()
        expect(within(consumingMetric).getByText("3")).toBeVisible()
    })

    it("distinguishes not-requested from genuinely-empty provenance lists", async () => {
        const withoutKey = mockRecord()
        delete (withoutKey.provenance as Record<string, unknown>).used_as_input_by
        server.use(http.get(ENDPOINT, () => HttpResponse.json(withoutKey)))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Used as input by" }).closest("section") as HTMLElement
        expect(within(section).getByText("This section was not requested for this view.")).toBeVisible()
        expect(within(section).queryByText(/No calculation is recorded as having consumed/))
            .not.toBeInTheDocument()
    })

    it("renders a genuinely empty producer list distinctly from not-requested", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({
            provenance: { produced_by: [], used_as_input_by: mockRecord().provenance.used_as_input_by },
        }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Produced by" }).closest("section") as HTMLElement
        expect(within(section).getByText("No calculation is recorded as having produced this geometry.")).toBeVisible()
        expect(within(section).queryByRole("link")).not.toBeInTheDocument()
    })

    it("shows a static message and no viewer when the geometry has no atom rows", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ natoms: 0, symbols: [], coords: [], atoms: [] }))))
        page()
        await screen.findByRole("heading", { name: "Geometry" })
        const viewerSection = screen.getByRole("heading", { name: "Structure projection" }).closest("section") as HTMLElement
        expect(within(viewerSection).getByText(/No atom rows are recorded/)).toBeVisible()
        expect(viewerSection.querySelector("svg")).toBeNull()
    })

    it("shows a message when no raw XYZ text is recorded", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ xyz_text: null }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
        expect(within(xyzSection).getByText("No raw XYZ text is recorded for this geometry.")).toBeVisible()
    })

    it("shows a specific not-found state for a 404", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ detail: "geometry not found" }, { status: 404 })))
        page()
        expect(await screen.findByRole("heading", { name: "Geometry not found" })).toBeVisible()
    })

    it("gives a wrong-handle-type 422 its own non-retryable state", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            code: "handle_type_mismatch",
            detail: "handle_type_mismatch: expected a geometry handle (prefix 'geom') but got prefix 'calc'",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a geometry reference" })).toBeVisible()
        expect(screen.getByText(/expected a geometry handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("gives a 500 its own unavailable state, never the empty-provenance text", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ detail: "internal error" }, { status: 500 })))
        page()
        expect(await screen.findByRole("heading", { name: "Geometry unavailable" })).toBeVisible()
        expect(screen.queryByText(/No calculation is recorded/)).not.toBeInTheDocument()
    })
})
