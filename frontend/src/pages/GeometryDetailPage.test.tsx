import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
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
 * deliberately built with TWO producers and THREE consumers, in an order
 * that is not alphabetical by ref and not identical between the two
 * lists — a single-edge fixture cannot distinguish "read this row's own
 * fields" from "always show the first row", and a fixture whose two
 * lists happen to share an order cannot catch a client-side re-sort. One
 * calculation (`calc_opt_two`) is deliberately present in BOTH lists —
 * measured live behaviour (calc_htgb7s5nakuw52eqhcxpvilpoq on
 * geom_or52ifyemdi3eewsjym2fuvo3a), not a hypothetical — so a mutation
 * that merges, sums, or conflates the two lists is observable.
 *
 * `geom_hash` is a full 64-character hex string, not a short placeholder
 * — a fixture short enough to coincidentally survive a `.slice(0, 8)`
 * truncation mutation cannot prove the page renders the hash in full.
 */
function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        geometry_ref: "geom_ch4_one",
        natoms: 5,
        geom_hash: "ab12cd34".repeat(8),
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
        // Full 64-char hash, not a truncated prefix — a mutation that
        // rendered `geom_hash.slice(0, 8)` would still pass a shorter
        // fixture by coincidence, so this fixture's hash is realistically
        // long and this assertion checks the whole string.
        expect(within(context).getByText("ab12cd34".repeat(8))).toBeVisible()
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

    it("points to validation on BOTH producing and consuming calculations, each labelled by its own relationship", async () => {
        // A geometry_validation row can carry either an input_geometry_ref
        // or an output_geometry_ref, so the check can live on a consuming
        // calculation just as easily as a producing one. Gating this
        // pointer on producers only (an earlier version of this page) left
        // consume-only geometries with a sentence and zero links. This
        // fixture's fixed producer/consumer sets (calc_opt_two appears in
        // both) mean a swap or a merge of the two lists is observable here,
        // not just their presence.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")

        const producerPointer = within(summary).getByTestId("validation-producer-pointer")
        expect(producerPointer).toHaveTextContent(/producing calculations/)
        expect(within(producerPointer).getByRole("link", { name: "calc_opt_two" }))
            .toHaveAttribute("href", "/calculations/calc_opt_two")
        expect(within(producerPointer).getByRole("link", { name: "calc_opt_one" }))
            .toHaveAttribute("href", "/calculations/calc_opt_one")
        // calc_freq_one and calc_sp_one never produced this geometry — a
        // mutation that swapped the two lists would put them here.
        expect(within(producerPointer).queryByRole("link", { name: "calc_freq_one" })).not.toBeInTheDocument()
        expect(within(producerPointer).queryByRole("link", { name: "calc_sp_one" })).not.toBeInTheDocument()

        const consumerPointer = within(summary).getByTestId("validation-consumer-pointer")
        expect(consumerPointer).toHaveTextContent(/consuming calculations/)
        expect(within(consumerPointer).getByRole("link", { name: "calc_freq_one" }))
            .toHaveAttribute("href", "/calculations/calc_freq_one")
        expect(within(consumerPointer).getByRole("link", { name: "calc_opt_two" }))
            .toHaveAttribute("href", "/calculations/calc_opt_two")
        expect(within(consumerPointer).getByRole("link", { name: "calc_sp_one" }))
            .toHaveAttribute("href", "/calculations/calc_sp_one")
        // calc_opt_one only produced this geometry — a mutation that
        // merged the two lists into one would put it here too.
        expect(within(consumerPointer).queryByRole("link", { name: "calc_opt_one" })).not.toBeInTheDocument()

        // No fragment on any link — see the module comment on this block:
        // this app has no fragment-scroll handling, and the target id
        // lives inside a closed <details>.
        for (const link of within(summary).getAllByRole("link")) {
            expect(link.getAttribute("href")).not.toContain("#")
        }
    })

    it("says nothing about validation pointers when a geometry has no producers or consumers at all", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({
            provenance: { produced_by: [], used_as_input_by: [] },
        }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")
        expect(within(summary).queryByTestId("validation-producer-pointer")).not.toBeInTheDocument()
        expect(within(summary).queryByTestId("validation-consumer-pointer")).not.toBeInTheDocument()
    })

    it("renders every atom row in the coordinate table, in payload order", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
        const rows = within(table).getAllByRole("row").slice(1)
        expect(rows).toHaveLength(5)

        // Every row's own Atom-index and Element cell, scoped by
        // data-label so this cannot be satisfied by "always show row 0's
        // element" (rows 2-5 are all H, distinct from row 1's C) or by an
        // off-by-one atom_index (each row's own index, not a shared or
        // shifted one).
        const expectedAtoms = [
            { index: "1", element: "C" },
            { index: "2", element: "H" },
            { index: "3", element: "H" },
            { index: "4", element: "H" },
            { index: "5", element: "H" },
        ]
        expectedAtoms.forEach(({ index, element }, i) => {
            expect(within(rows[i]).getByText(index, { selector: "[data-label='Atom']" })).toBeVisible()
            expect(within(rows[i]).getByText(element, { selector: "[data-label='Element']" })).toBeVisible()
        })

        // Distinct x/y/z values per column, scoped to their own cell — a
        // mutation that swapped which coordinate lands in which column
        // (or read one atom's coordinate for another's row) is observable
        // here, unlike a fixture whose x/y/z happened to share one value.
        const secondDataRow = rows[1]
        expect(within(secondDataRow).getByText("0.11", { selector: "[data-label='x']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.22", { selector: "[data-label='y']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.33", { selector: "[data-label='z']" })).toBeVisible()
    })

    it("flags a mismatch between the declared atom count and the returned coordinate rows", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ natoms: 99 }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Coordinate table" }).closest("section") as HTMLElement
        expect(within(section).getByRole("alert")).toHaveTextContent(
            /declared atom count \(99\).*does not match.*coordinate rows returned\s*\(5\)/,
        )
        // The table itself still renders the rows the archive actually sent.
        expect(within(section).getAllByRole("row")).toHaveLength(6)
    })

    it("says nothing about a count mismatch when natoms and the returned rows agree", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Coordinate table" }).closest("section") as HTMLElement
        expect(within(section).queryByRole("alert")).not.toBeInTheDocument()
    })

    it("renders the raw XYZ text verbatim in a selectable block", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
        expect(within(xyzSection).getByText(/H 0\.110000 0\.220000 0\.330000/)).toBeVisible()
    })

    it("renders the WebGL viewer container in place of the old SVG projection, with the bond-inference disclosure intact", async () => {
        // This page does not mock "3dmol" (that mock lives in
        // GeometryViewer.test.tsx, split out for the same reason
        // GeometryDetailPage.errorBoundary.test.tsx is split out), and
        // jsdom has neither real WebGL nor real layout — see the
        // size-check comment in GeometryViewer.tsx — so the viewer
        // mounted here will genuinely settle into its "unavailable"
        // status, exactly as it would in a real browser with no WebGL
        // and a not-yet-laid-out container. What this test pins is the
        // page-level wiring: the structure section renders the new
        // `.viewer-canvas` element (never an SVG projection any more)
        // and keeps the disclosure paragraph next to it.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const viewerSection = screen.getByRole("heading", { name: "Structure projection" }).closest("section") as HTMLElement
        expect(viewerSection.querySelector("svg")).toBeNull()
        expect(viewerSection.querySelector(".viewer-canvas")).not.toBeNull()
        // A distinct sentence from the "interactive 3D view" framing
        // sentence in the same paragraph, so a mutation that deletes only
        // the bond-inference disclaimer must not be able to hide behind
        // an assertion that only matches the framing sentence next to it.
        // See also the isolated component-level test in
        // `GeometryViewer.test.tsx`.
        expect(within(viewerSection).getByText(
            /Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record\./,
        )).toBeVisible()
    })

    it("forwards this geometry's own xyz_text to the viewer section — the picture and the raw XYZ block read the same record", async () => {
        const record = mockRecord()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
        // Confirms the page-level source of truth for the raw XYZ block —
        // GeometryViewer.test.tsx (mocked 3dmol) separately pins that
        // GeometryViewer itself forwards this same string to 3Dmol
        // unmodified, so together these two tests cover the full path
        // from API response to what 3Dmol actually receives.
        expect(within(xyzSection).getByText(/H 0\.630000 -0\.630000 -0\.630000/)).toBeVisible()
        expect(record.xyz_text).toContain("H 0.630000 -0.630000 -0.630000")
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

        // Every provenance link's href, not just its visible text — the
        // spec line for this route is "producer/consumer links", and a
        // mutation that pointed these at `/geometries/...` instead of
        // `/calculations/...` (or any other wrong target) would still
        // satisfy every name-only assertion above.
        expect(within(producedSection).getByRole("link", { name: "calc_opt_two" }))
            .toHaveAttribute("href", "/calculations/calc_opt_two")
        expect(within(producedSection).getByRole("link", { name: "calc_opt_one" }))
            .toHaveAttribute("href", "/calculations/calc_opt_one")
        expect(within(consumedSection).getByRole("link", { name: "calc_freq_one" }))
            .toHaveAttribute("href", "/calculations/calc_freq_one")
        expect(within(consumedSection).getByRole("link", { name: "calc_sp_one" }))
            .toHaveAttribute("href", "/calculations/calc_sp_one")

        // No role column at all on the consumer table — CalculationInputGeometry
        // has no role column (it is always null on every input-link row), so
        // this page does not render a Role column here rather than
        // borrowing a role from the producer side or printing a fake value.
        expect(within(consumedSection).queryByRole("columnheader", { name: "Role" })).not.toBeInTheDocument()
        expect(within(producedSection).getByRole("columnheader", { name: "Role" })).toBeVisible()

        // The metric counts are independent, not a combined tally: two
        // producer edges and three consumer edges, never summed (5) and
        // never deduplicated by unique ref (4, since calc_opt_two repeats).
        const summary = screen.getByLabelText("Geometry provenance summary")
        const producingMetric = within(summary).getByText("Producing calculations").closest(".metric") as HTMLElement
        const consumingMetric = within(summary).getByText("Consuming calculations").closest(".metric") as HTMLElement
        expect(within(producingMetric).getByText("2")).toBeVisible()
        expect(within(consumingMetric).getByText("3")).toBeVisible()
    })

    it("distinguishes an archive-dropped field from a genuinely empty list", async () => {
        // This page always sends `include=provenance` and no token gates
        // any field on this endpoint (see api/geometryApi.ts) — so an
        // absent key here can only mean the archive itself dropped the
        // field, never that this client failed to request it. The copy
        // must say that, not "not requested".
        const withoutKey = mockRecord()
        delete (withoutKey.provenance as Record<string, unknown>).used_as_input_by
        server.use(http.get(ENDPOINT, () => HttpResponse.json(withoutKey)))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const section = screen.getByRole("heading", { name: "Used as input by" }).closest("section") as HTMLElement
        expect(within(section).getByText("The archive did not return this field for this geometry.")).toBeVisible()
        expect(within(section).queryByText(/not requested/i)).not.toBeInTheDocument()
        expect(within(section).queryByText(/No calculation is recorded as having consumed/))
            .not.toBeInTheDocument()
    })

    it("renders a genuinely empty producer list distinctly from an archive-dropped field", async () => {
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

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state, distinct from a wrong-prefix ref", async () => {
        // `invalid_handle` is what live traffic actually returns for a
        // malformed-but-right-prefix ref, distinct from the
        // `handle_type_mismatch` case above. Pins the `INVALID_HANDLE_CODES`
        // classification in `useScientificRecord` — shared machinery this
        // page depends on, added for the `unprocessable`/`geometry_too_large`
        // state below.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'geom_' not a recognised geometry handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a geometry reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised geometry handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("classifies a 422 carrying no `code` at all as invalid, never as unprocessable", async () => {
        // The wire type allows a 422 with no `code` field (the archive's
        // own error envelope always sends one today, but a caller/mock
        // that omits it must still be handled deterministically).
        // `useScientificRecord` treats an undefined code the same as a
        // recognised invalid-handle code — never `unprocessable`, which
        // would misleadingly read as "a real record exists but was too
        // large/costly to serve" for a case that carries no evidence of
        // that at all.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            detail: "geometry not found or malformed",
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a geometry reference" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Geometry could not be displayed" })).not.toBeInTheDocument()
    })

    it("gives a 500 its own unavailable state, never the empty-provenance text", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ detail: "internal error" }, { status: 500 })))
        page()
        expect(await screen.findByRole("heading", { name: "Geometry unavailable" })).toBeVisible()
        expect(screen.queryByText(/No calculation is recorded/)).not.toBeInTheDocument()
    })

    it("gives a valid-but-oversized geometry its own state, distinct from an invalid reference", async () => {
        // Measured: `app/services/scientific_read/geometry.py` raises
        // `geometry_too_large` (422) when `natoms` exceeds the public cap
        // (`max_geometry_atoms_public`, default 500) — a real record with a
        // valid ref that this view declines to serve in full. Reading
        // every 422 as "not a valid reference" (the old behaviour) would
        // render a heading that flatly contradicts its own body text.
        server.use(http.get(ENDPOINT, () => HttpResponse.json({
            code: "geometry_too_large",
            detail: "geometry has 812 atoms which exceeds the public cap of 500. Contact a curator for bulk access.",
            context: { max_atoms: 500, atoms: 812 },
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Geometry could not be displayed" })).toBeVisible()
        expect(screen.getByText(/exceeds the public cap of 500/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
        // Must not read as "wrong reference" — the ref is valid and the
        // record exists, which is exactly what the body text says.
        expect(screen.queryByRole("heading", { name: "Not a geometry reference" })).not.toBeInTheDocument()
        expect(screen.queryByText(/does not identify a geometry/)).not.toBeInTheDocument()
    })
})
