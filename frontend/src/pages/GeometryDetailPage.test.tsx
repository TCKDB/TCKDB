import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"
import { ANGSTROM_TO_BOHR } from "../domain/geometryXyz"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
    // Some tests below stub `navigator.clipboard` (jsdom does not provide
    // one). Reset it so a stub from one test cannot leak into the next.
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
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

describe("GeometryDetailPage identity (#321)", () => {
    // Deliberately a DIFFERENT formula from what the CH4 fixture's atom
    // rows would Hill-derive ("CH4") -- a fixture where the two agree
    // cannot tell "reads the served identity" apart from "still computes
    // it client-side and got lucky".
    const speciesIdentity = {
        kind: "species_entry",
        species_entry: {
            species_ref: "spc_demo", species_entry_ref: "spe_demo", formula: "XeF6",
            canonical_smiles: "F[Xe](F)(F)(F)(F)F", inchi_key: "AYEKOFBPNLCAJY-UHFFFAOYSA-N",
            charge: 0, multiplicity: 1,
        },
        transition_state_entry: null,
        ambiguous_owners: [],
    }

    it("renders the SERVED formula, not the client-derived Hill formula, when identity is present", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ identity: speciesIdentity }))))
        page()
        expect(await screen.findByRole("heading", { name: "XeF6 geometry" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "CH4 geometry" })).not.toBeInTheDocument()
        expect(screen.queryByText(/computed on this page from atom symbols/)).not.toBeInTheDocument()
    })

    it("falls back to the client-derived Hill formula, labelled as such, when identity is absent", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ identity: null }))))
        page()
        expect(await screen.findByRole("heading", { name: "CH4 geometry" })).toBeVisible()
        expect(screen.getByText(/computed on this page from atom symbols/)).toBeVisible()
    })

    it("renders the ambiguous case distinctly -- falls back to the Hill formula (never guesses an owner) and shows the owner list", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({
            identity: {
                kind: null, species_entry: null, transition_state_entry: null,
                ambiguous_owners: [{ kind: "species_entry", ref: "spe_a" }, { kind: "species_entry", ref: "spe_b" }],
            },
        }))))
        page()
        expect(await screen.findByRole("heading", { name: "CH4 geometry" })).toBeVisible()
        expect(screen.getByTestId("record-identity-ambiguous")).toHaveTextContent(/more than one distinct owner/)
        expect(screen.getByText("spe_a")).toBeVisible()
        expect(screen.getByText("spe_b")).toBeVisible()
    })

    it("a transition-state owner never renders an empty SMILES field", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({
            identity: {
                kind: "transition_state_entry", species_entry: null,
                transition_state_entry: {
                    transition_state_ref: "ts_demo", transition_state_entry_ref: "tse_demo",
                    formula: null, unmapped_smiles: null, charge: 0, multiplicity: 2,
                },
                ambiguous_owners: [],
            },
        }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        expect(screen.queryByText("SMILES")).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()
        expect(screen.getByText("Reaction SMILES (unmapped)")).toBeVisible()
        // This page does not opt out of `RecordIdentityHeader`'s
        // `explainTransitionStateIdentity` default (only
        // `TransitionStateEntryPage`, which has its own Reaction-section
        // lede, does) -- it has no Reaction section of its own for a
        // TS-owned geometry, so it must keep the one explanation.
        expect(screen.getByText(/no canonical SMILES/i)).toBeVisible()
    })

    it("renders no submission row when the key is absent (anonymous caller)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        expect(screen.queryByText("Submission")).not.toBeInTheDocument()
    })

    it("renders the submission ref when the key is present (authenticated caller)", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ submission_ref: "sub_demo" }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        expect(screen.getByText("Submission")).toBeVisible()
        expect(screen.getByText("sub_demo")).toBeVisible()
    })
})

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

    // Item 1/7, design/foundations PR B: mutation check for header order
    // (the kicker-row/h1 that `RecordIdentityHeader` now owns, ahead of
    // this page's own provenance `.kv-list`) and the table primitive
    // (`.data-table`, never `.stage-table`) -- see the identical checks on
    // `ConformerGroupPage.test.tsx`/`ConformerObservationPage.test.tsx`.
    it("renders the kicker row before the h1, and every table as .data-table, never .stage-table", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        const { container } = page()
        const h1 = await screen.findByRole("heading", { name: "CH4 geometry" })
        const header = h1.closest(".basin-header") as HTMLElement
        expect(header).not.toBeNull()
        const kickerRow = header.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(kickerRow).not.toBeNull()

        expect(container.querySelector(".stage-table")).toBeNull()
        const tables = Array.from(container.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThan(0)
        for (const table of tables) {
            expect(table).toHaveClass("data-table")
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })

    it("says validation is not recorded for this geometry, rather than fabricating a verdict", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")
        expect(within(summary).getByText("Not recorded for this geometry")).toBeVisible()
        expect(within(summary).queryByText(/passed/i)).not.toBeInTheDocument()
        expect(within(summary).queryByText(/failed/i)).not.toBeInTheDocument()
    })

    it("points to the Produced by / Used as input by tables below, rather than repeating every ref inline", async () => {
        // Finding #13: this card used to repeat every producing/consuming
        // calculation ref inline as its own `<dl>` of links -- up to twelve
        // on the live CH3 record, reported rendering all-caps with no link
        // affordance. Those SAME refs are already real, correctly-cased
        // links in the "Produced by" / "Used as input by" tables further
        // down this page (see the tests around those headings below) -- this
        // card now just points there instead of duplicating the list, so
        // there is only ONE place on the page that can get a ref's case or
        // link styling wrong.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")

        expect(within(summary).getByText(/Produced by.*Used as input by/s)).toBeVisible()
        // No calculation refs repeated inline in this card -- confirmed by
        // there being no links inside it at all (the pointer is plain text).
        expect(within(summary).queryAllByRole("link")).toHaveLength(0)
    })

    it("says nothing about a validation pointer when a geometry has no producers or consumers at all", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({
            provenance: { produced_by: [], used_as_input_by: [] },
        }))))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const summary = screen.getByLabelText("Geometry provenance summary")
        expect(within(summary).queryByText(/Produced by/)).not.toBeInTheDocument()
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
        expect(within(secondDataRow).getByText("0.11", { selector: "[data-column='x']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.22", { selector: "[data-column='y']" })).toBeVisible()
        expect(within(secondDataRow).getByText("0.33", { selector: "[data-column='z']" })).toBeVisible()
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
        const viewerSection = screen.getByRole("heading", { name: "Structure view" }).closest("section") as HTMLElement
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

    it("renders this geometry's own xyz_text verbatim in the Raw XYZ block", async () => {
        // Only asserts what this test file can actually see: the Raw XYZ
        // section's own rendering of the record's xyz_text. Whether that
        // same string reaches <GeometryViewer xyzText={...}> — the prop,
        // not the block below it — is a separate, page-level wiring claim
        // this test cannot verify without mocking GeometryViewer, which
        // would break every other (unmocked, real-3dmol) test in this
        // file. See GeometryDetailPage.viewerWiring.test.tsx, split out
        // for exactly that reason, for the prop-forwarding assertion.
        const record = mockRecord()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
        page()
        await screen.findByRole("heading", { name: "CH4 geometry" })
        const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
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
        const viewerSection = screen.getByRole("heading", { name: "Structure view" }).closest("section") as HTMLElement
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

    describe("coordinate table units toggle (ångström <-> bohr)", () => {
        it("defaults to ångström — the owner's own instruction was 'starts with angstrom cause that's how it's stored'", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement
            expect(within(section).getByRole("button", { name: "Å" })).toHaveAttribute("aria-pressed", "true")
            expect(within(section).getByRole("button", { name: "bohr" })).toHaveAttribute("aria-pressed", "false")
            // The raw, unconverted value from the fixture — mutating the
            // default unit to "bohr" would fail this (it would show the
            // converted 0.2079... value instead of 0.11).
            const secondRow = within(table).getAllByRole("row")[2]
            expect(within(secondRow).getByText("0.11", { selector: "[data-column='x']" })).toBeVisible()
        })

        it("converts every coordinate with the exact CODATA Å→bohr factor when toggled, and back when toggled again", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement

            fireEvent.click(within(section).getByRole("button", { name: "bohr" }))

            expect(within(section).getByRole("button", { name: "bohr" })).toHaveAttribute("aria-pressed", "true")
            expect(within(section).getByRole("button", { name: "Å" })).toHaveAttribute("aria-pressed", "false")
            const secondRow = within(table).getAllByRole("row")[2]
            // 0.11 Å and -0.63 Å converted by the exact factor, not a
            // rounded approximation, not the untouched angstrom value,
            // and not an inverted (divided) factor.
            expect(within(secondRow).getByText((0.11 * ANGSTROM_TO_BOHR).toFixed(6), { selector: "[data-column='x']" })).toBeVisible()
            expect(within(secondRow).getByText((0.22 * ANGSTROM_TO_BOHR).toFixed(6), { selector: "[data-column='y']" })).toBeVisible()
            expect(within(secondRow).getByText((0.33 * ANGSTROM_TO_BOHR).toFixed(6), { selector: "[data-column='z']" })).toBeVisible()
            const thirdRow = within(table).getAllByRole("row")[3]
            expect(within(thirdRow).getByText((-0.63 * ANGSTROM_TO_BOHR).toFixed(6), { selector: "[data-column='x']" })).toBeVisible()

            fireEvent.click(within(section).getByRole("button", { name: "Å" }))
            expect(within(secondRow).getByText("0.11", { selector: "[data-column='x']" })).toBeVisible()
        })

        it("names the exact conversion factor and states the wire truth (stored in ångström), never implying the archive holds a bohr-valued record", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement
            // Finding #9: plain wording, no `coordinate_units`-on-the-wire jargon --
            // the guarantee (stored in ångström; bohr is a display conversion) stays.
            expect(within(section).getByText(/Stored in ångström; bohr here is a display conversion/)).toBeVisible()
            expect(within(section).queryByText(/coordinate_units/)).not.toBeInTheDocument()
            expect(within(section).getByText(new RegExp(ANGSTROM_TO_BOHR.toFixed(10).replace(".", "\\.")))).toBeVisible()
            expect(within(section).queryByText(/\(converted\)/i)).not.toBeInTheDocument()
        })

        it("flips the x/y/z column HEADERS to the active unit when toggled, not just the body", async () => {
            // A mutation that froze the header text to "x (Å)" while the
            // body kept rendering bohr numbers underneath survived the
            // full suite before this test existed — the toggle's own
            // tests above only ever read the body cells, never the
            // <th>'s own text.
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement
            expect(within(table).getByRole("columnheader", { name: "x (Å)" })).toBeVisible()
            expect(within(table).getByRole("columnheader", { name: "y (Å)" })).toBeVisible()
            expect(within(table).getByRole("columnheader", { name: "z (Å)" })).toBeVisible()

            fireEvent.click(within(section).getByRole("button", { name: "bohr" }))

            expect(within(table).getByRole("columnheader", { name: "x (bohr)" })).toBeVisible()
            expect(within(table).getByRole("columnheader", { name: "y (bohr)" })).toBeVisible()
            expect(within(table).getByRole("columnheader", { name: "z (bohr)" })).toBeVisible()
            expect(within(table).queryByRole("columnheader", { name: "x (Å)" })).not.toBeInTheDocument()
        })

        it("renders every ångström decimal place the archive sent, never truncated to a fixed precision", async () => {
            // Every coordinate in the shared `mockRecord()` fixture has at
            // most 2 decimal places, so a mutation that swapped the
            // ångström branch's `String(v)` for `v.toFixed(2)` produces
            // byte-identical text against that fixture and survives every
            // other test in this file. This fixture's first atom carries
            // a genuinely 6-decimal coordinate (matching the precision the
            // live archive actually returns, e.g. 1.078957), so a
            // truncation is observable here. Built as a local override
            // rather than editing the shared `mockRecord()` default,
            // since dozens of other assertions in this file are pinned to
            // that default's exact literal values (0.11, 0.22, ...).
            const record = mockRecord({
                atoms: [
                    { atom_index: 1, element: "C", x: 1.078957, y: 0, z: 0 },
                    { atom_index: 2, element: "H", x: 0.11, y: 0.22, z: 0.33 },
                    { atom_index: 3, element: "H", x: -0.63, y: -0.63, z: 0.63 },
                    { atom_index: 4, element: "H", x: -0.63, y: 0.63, z: -0.63 },
                    { atom_index: 5, element: "H", x: 0.63, y: -0.63, z: -0.63 },
                ],
            })
            server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const firstDataRow = within(table).getAllByRole("row")[1]
            expect(within(firstDataRow).getByText("1.078957", { selector: "[data-column='x']" })).toBeVisible()
            // Would read "1.08" under a `.toFixed(2)` mutation of the
            // ångström branch — asserted absent so this test actually
            // fails under that mutation rather than merely not checking.
            expect(within(firstDataRow).queryByText("1.08")).not.toBeInTheDocument()
        })

        it("carries the active unit in the mobile stacked-row label, not a bare axis letter", async () => {
            // `conformer-group.css`'s mobile rule renders
            // `td::before { content: attr(data-label) }` below 680px — a
            // bare "x" label with no unit anywhere on the value is a
            // wrong-unit-reading hazard one breakpoint away from the
            // desktop layout. jsdom cannot evaluate the media query
            // itself, but the attribute value this test reads is exactly
            // what that CSS renders, unconditionally of viewport width.
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement
            const firstDataRow = within(table).getAllByRole("row")[1]
            const xCell = firstDataRow.querySelector("[data-column='x']") as HTMLElement
            expect(xCell.getAttribute("data-label")).toBe("x (Å)")

            fireEvent.click(within(section).getByRole("button", { name: "bohr" }))
            expect(xCell.getAttribute("data-label")).toBe("x (bohr)")
        })
    })

    describe("coordinate table element display toggle (symbol <-> atomic number)", () => {
        it("defaults to element symbols, and switching to Number shows each row's own atomic number", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement
            expect(within(section).getByRole("button", { name: "Symbol" })).toHaveAttribute("aria-pressed", "true")

            fireEvent.click(within(section).getByRole("button", { name: "Number" }))

            const rows = within(table).getAllByRole("row").slice(1)
            expect(within(rows[0]).getByText("6", { selector: "[data-label='Element']" })).toBeVisible()
            for (const row of rows.slice(1)) {
                expect(within(row).getByText("1", { selector: "[data-label='Element']" })).toBeVisible()
            }
        })

        it("renders an unrecognised element symbol honestly, never as 0 or blank", async () => {
            const record = mockRecord({
                natoms: 1,
                symbols: ["Xx"],
                coords: [[0, 0, 0]],
                atoms: [{ atom_index: 1, element: "Xx", x: 0, y: 0, z: 0 }],
            })
            server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
            page()
            await screen.findByRole("heading", { name: "Xx geometry" })
            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const section = table.closest("section") as HTMLElement

            fireEvent.click(within(section).getByRole("button", { name: "Number" }))

            const row = within(table).getAllByRole("row")[1]
            const cell = within(row).getByText(/unknown/, { selector: "[data-label='Element']" })
            expect(cell).toHaveTextContent("unknown (Xx)")
            expect(cell).not.toHaveTextContent(/^0$/)
        })
    })

    describe("raw XYZ copy button", () => {
        it("reuses the shared CopyButton next to the raw XYZ block", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
            expect(within(xyzSection).getByRole("button", { name: "Copy raw XYZ text" })).toBeVisible()
        })

        it("renders no copy button when there is no raw XYZ text to copy", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord({ xyz_text: null }))))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
            expect(within(xyzSection).queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument()
        })

        it("copies the archive's own xyz_text verbatim, even while the coordinate table is showing bohr", async () => {
            // Neither existing copy test above ever reads WHAT the button
            // copies — both are presence-only (button exists / doesn't).
            // A mutation that made the copy emit the bohr-converted block
            // while the raw-XYZ <pre> still displayed ångström text
            // survived the full suite. The coordinate table's unit toggle
            // and the raw-XYZ copy button are wired to two entirely
            // separate pieces of state; this pins that the latter is
            // never affected by the former.
            const writeText = vi.fn().mockResolvedValue(undefined)
            Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
            const record = mockRecord()
            server.use(http.get(ENDPOINT, () => HttpResponse.json(record)))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })

            const table = screen.getByRole("table", { name: "Coordinates for geom_ch4_one" })
            const coordSection = table.closest("section") as HTMLElement
            fireEvent.click(within(coordSection).getByRole("button", { name: "bohr" }))

            const xyzSection = screen.getByRole("heading", { name: "Raw XYZ" }).closest("section") as HTMLElement
            fireEvent.click(within(xyzSection).getByRole("button", { name: "Copy raw XYZ text" }))

            expect(writeText).toHaveBeenCalledTimes(1)
            expect(writeText).toHaveBeenCalledWith(record.xyz_text)
            // Never the bohr-converted numbers the table happens to be
            // showing at the moment of the click.
            expect(writeText.mock.calls[0][0]).not.toContain((0.11 * ANGSTROM_TO_BOHR).toFixed(6))
        })
    })

    describe("validation card shape", () => {
        // Finding #13: this card no longer repeats every producing/consuming
        // calculation ref inline (a `<dl>` of named pointer rows, previously
        // asserted here) -- it points at the "Produced by" / "Used as input
        // by" tables further down the page instead, where those SAME refs
        // already render as real, correctly-cased links. See the
        // "points to the Produced by / Used as input by tables below" and
        // "says nothing about a validation pointer..." tests above, which
        // now cover this card's shape.
        it("renders the pointer sentence as plain prose inside the validation card, not a link list", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })
            const summary = screen.getByLabelText("Geometry provenance summary")
            const card = within(summary).getByText("Validation").closest(".validation-card") as HTMLElement
            expect(card.querySelector("dl")).toBeNull()
            expect(card.querySelector("a")).toBeNull()
        })
    })

    describe("page shell: table of contents and width caps", () => {
        // This page's real, unmodified fixture (`mockRecord()`) renders 5
        // `<h2>` sections at runtime -- Structure view, Coordinate table,
        // Raw XYZ, Produced by, Used as input by -- even though the page's
        // own file only declares 4 `<h2 ...>` call sites (`ProvenanceSection`
        // is one component invoked twice). This is a REAL page rendering
        // real data, not a fixture hand-built to hit a round number.
        it("shows a table of contents with one entry per section that actually rendered", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            page()
            await screen.findByRole("heading", { name: "CH4 geometry" })

            const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
            // The nav appears once TWO sections have registered, but this page
            // has five -- so awaiting the nav is not enough on its own; the
            // remaining registrations can land a tick later.
            await waitFor(() => expect(
                within(toc).getAllByRole("link").map((link) => link.textContent),
            ).toEqual([
                "Structure view", "Coordinate table", "Raw XYZ", "Produced by", "Used as input by",
            ]))
            expect(within(toc).getAllByRole("link")[0]).toHaveAttribute("href", "#viewer-heading")
        })

        it("caps running prose at its own width while the 3D viewer and coordinate table use the page's full width", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockRecord())))
            const { container } = page()
            await screen.findByRole("heading", { name: "CH4 geometry" })

            // `.basin-intro` retired (design/foundations PR B) -- this
            // header's intro paragraph now composes `.section-intro`
            // (`conformer-group.css`), the same `--measure-prose` (44rem)
            // cap `SectionHeading`'s own optional `intro` prop uses for
            // every in-page section on these five record pages.
            const prose = container.querySelector(".section-intro") as HTMLElement
            const viewer = container.querySelector(".geometry-viewer") as HTMLElement
            // `.stage-table` retired from this page's own tables in favour
            // of the shared `.data-table` primitive (item 7).
            const table = container.querySelector(".data-table") as HTMLElement
            expect(prose).not.toBeNull()
            expect(viewer).not.toBeNull()
            expect(table).not.toBeNull()
            expect(container.querySelector(".stage-table")).toBeNull()

            const proseMaxWidth = getComputedStyle(prose).maxWidth
            const viewerMaxWidth = getComputedStyle(viewer).maxWidth
            // Prose has an explicit, narrow cap; the viewer and the table
            // have none at all (the page's own 100rem cap is the only
            // thing bounding them) -- the two must not resolve to the same
            // value, which is what "wide page, capped prose" actually
            // means as a checkable claim rather than a screenshot.
            // Was a one-off literal `44rem` (`.basin-intro`) -- design/
            // foundations PR B consolidates every intro-style paragraph on
            // these five record pages onto the ONE `--measure-prose` token
            // step, the same cap `SectionHeading`'s own `intro` prop and
            // the old `.ledger-heading` wrapper already used, rather than a
            // second, slightly different figure for the page header
            // specifically. jsdom's CSSOM does not resolve CSS custom
            // properties (no `var()` substitution -- a documented jsdom
            // limitation), so the raw, unresolved token reference is what
            // `getComputedStyle` actually reports here; asserting that
            // literal string is a STRONGER check than a resolved rem value
            // would be, since it also pins WHICH token this class points
            // at, not just its current numeric value.
            expect(proseMaxWidth).toBe("var(--measure-prose)")
            expect(viewerMaxWidth).not.toBe(proseMaxWidth)
            // jsdom's CSSOM reports an unset property as "" rather than
            // resolving it to CSS's own "none" initial value -- either way,
            // the point holds: nothing constrains this element's width.
            expect(viewerMaxWidth).toBe("")
            expect(getComputedStyle(table).maxWidth).not.toBe(proseMaxWidth)
        })
    })
})
