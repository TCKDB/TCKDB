import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import GeometryDetailPage from "./GeometryDetailPage"
import "../design-system.css"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/geometries/geom_iso"

function page() {
    return render(
        <MemoryRouter initialEntries={["/geometries/geom_iso"]}>
            <Routes>
                <Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

type Atom = { element: string; mass?: number | null; xyz: [number, number, number] }

function record(atoms: Atom[]) {
    return {
        geometry_ref: "geom_iso",
        natoms: atoms.length,
        geom_hash: "ab12cd34".repeat(8),
        format: "cartesian",
        coordinate_units: "angstrom",
        symbols: atoms.map((a) => a.element),
        coords: atoms.map((a) => a.xyz),
        atoms: atoms.map((a, i) => ({
            atom_index: i + 1, element: a.element, x: a.xyz[0], y: a.xyz[1], z: a.xyz[2],
            isotope_mass_number: a.mass ?? null,
        })),
        xyz_text: "",
        created_at: "2026-07-21T12:06:50.748258",
        identity: null,
        provenance: { produced_by: [], used_as_input_by: [] },
    }
}

const O: Atom = { element: "O", xyz: [0, 0, 0] }
const hAt = (mass: number | null, x: number): Atom => ({ element: "H", mass, xyz: [x, 0.59, 0] })

describe("GeometryDetailPage isotope labels (#685)", () => {
    it("shows a D2O geometry as 2H atoms and a 2H2O formula, not H / H2O", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record([O, hAt(2, 0.76), hAt(2, -0.76)]))))
        page()
        expect(await screen.findByRole("heading", { name: "²H2O geometry" })).toBeVisible()
        expect(screen.getAllByRole("cell", { name: "²H" })).toHaveLength(2)
        expect(screen.queryByRole("heading", { name: "H2O geometry" })).not.toBeInTheDocument()
    })

    it("renders a mixed HDO geometry with one labelled and one plain H", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record([O, hAt(null, 0.76), hAt(2, -0.76)]))))
        page()
        expect(await screen.findByRole("heading", { name: "H²HO geometry" })).toBeVisible()
        expect(screen.getAllByRole("cell", { name: "²H" })).toHaveLength(1)
        expect(screen.getAllByRole("cell", { name: "H" })).toHaveLength(1)
    })

    it("labels a carbon-13 atom", async () => {
        const c13: Atom = { element: "C", mass: 13, xyz: [0, 0, 0] }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record([c13, hAt(null, 1), hAt(null, -1)]))))
        page()
        expect(await screen.findByRole("heading", { name: "¹³CH2 geometry" })).toBeVisible()
        expect(screen.getByRole("cell", { name: "¹³C" })).toBeVisible()
    })

    it("shows the geometry's own ²H2O formula even though the owning entry serves H2O", async () => {
        const rec = {
            ...record([O, hAt(2, 0.76), hAt(2, -0.76)]),
            identity: {
                kind: "species_entry",
                species_entry: {
                    species_ref: "spc_w", species_entry_ref: "spe_w", formula: "H2O",
                    canonical_smiles: "O", inchi_key: "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
                    charge: 0, multiplicity: 1,
                },
                transition_state_entry: null,
                ambiguous_owners: [],
            },
        }
        server.use(http.get(ENDPOINT, () => HttpResponse.json(rec)))
        page()
        expect(await screen.findByRole("heading", { name: "²H2O geometry" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "H2O geometry" })).not.toBeInTheDocument()
    })

    it("does not invent an isotope for an unlabelled geometry", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(record([O, hAt(null, 0.76), hAt(null, -0.76)]))))
        page()
        expect(await screen.findByRole("heading", { name: "H2O geometry" })).toBeVisible()
        expect(screen.queryByRole("cell", { name: "²H" })).not.toBeInTheDocument()
    })
})
