import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import LevelOfTheoryPage from "./LevelOfTheoryPage"
import { auxCard, b3lypCard, def2tzvpCard, gfn2Card } from "../test/catalogueFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const ENDPOINT = "/api/v1/scientific/level-of-theories/lot_x"

function page() {
    return render(
        <MemoryRouter initialEntries={["/methods/lot_x"]}>
            <Routes>
                <Route path="/methods/:lotRef" element={<LevelOfTheoryPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function lotRecord(lot: Record<string, unknown>, catalogue: Record<string, unknown> | undefined) {
    return {
        request: { include: [] },
        review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 },
        record: {
            level_of_theory: {
                level_of_theory_ref: "lot_x",
                method: "gfn2",
                basis: null,
                aux_basis: null,
                cabs_basis: null,
                dispersion: null,
                solvent: null,
                solvent_model: null,
                spin_treatment: null,
                lot_hash: "hash-x",
                created_at: "2026-11-09T00:00:00Z",
                ...lot,
            },
            evidence_summary: { calculation_usage_count: 2, has_correction_schemes: false, has_frequency_scale_factors: false, distinct_software_count: 1 },
            available_sections: {
                has_correction_schemes: false, has_frequency_scale_factors: false, has_used_by: true, has_software: true,
                has_catalogue: catalogue === undefined ? null : true,
            },
            correction_schemes: [],
            frequency_scale_factors: [],
            used_by: [],
            software: { software: [], workflow_tools: [] },
            ...(catalogue === undefined ? {} : { catalogue }),
        },
    }
}

describe("LevelOfTheoryPage: the method and basis-set catalogue", () => {
    it("asks for the catalogue, names the method under the heading and shows its card", async () => {
        let includes: string[] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            includes = new URL(request.url).searchParams.getAll("include")
            return HttpResponse.json(lotRecord({}, { method: gfn2Card(), dispersion: null, basis: null, aux_basis: null, cabs_basis: null }))
        }))
        page()
        await screen.findByRole("heading", { name: "gfn2", level: 1 })
        expect(includes).toContain("catalogue")
        expect(screen.getByText("GFN2-xTB · semiempirical")).toBeInTheDocument()
        const card = screen.getByRole("region", { name: "About this method" })
        expect(within(card).getByText("Geometry, Frequency, Noncovalent, eXtended Tight Binding, second parametrisation")).toBeInTheDocument()
        expect(within(card).getByRole("link", { name: "doi:10.1021/acs.jctc.8b01176" })).toBeInTheDocument()
        expect(within(card).getByRole("link", { name: "Open the method page" })).toHaveAttribute("href", "/methods/method/gfn2-xtb")
        // A level with no basis spelling has nothing to call uncatalogued.
        expect(screen.queryByText(/not in the catalogue/)).toBeNull()
    })

    it("shows one card per entry and says plainly which spellings are not in the catalogue", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(lotRecord(
            { method: "dlpno-ccsd(t)-f12", basis: "cc-pvtz-f12", aux_basis: "aug-cc-pvtz/c", cabs_basis: "cc-pvtz-f12-cabs" },
            { method: null, dispersion: null, basis: null, aux_basis: auxCard(), cabs_basis: null },
        ))))
        page()
        await screen.findByRole("heading", { name: /dlpno-ccsd\(t\)-f12/, level: 1 })
        expect(screen.getByRole("region", { name: "About this auxiliary basis set" })).toBeInTheDocument()
        expect(screen.queryByRole("region", { name: "About this method" })).toBeNull()
        const section = screen.getByRole("region", { name: "Catalogue" })
        expect(section).toHaveTextContent("Method dlpno-ccsd(t)-f12: not in the catalogue.")
        expect(section).toHaveTextContent("Basis set cc-pvtz-f12: not in the catalogue.")
        expect(section).toHaveTextContent("CABS basis set cc-pvtz-f12-cabs: not in the catalogue.")
        // No uncatalogued method means no catalogue name under the heading.
        expect(document.querySelector(".lot-catalogue-subtitle")).toBeNull()
    })

    it("names method and basis together and keeps the qualifier line", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(lotRecord(
            { method: "b3lyp", basis: "def2tzvp", spin_treatment: "unrestricted" },
            { method: b3lypCard(), dispersion: null, basis: def2tzvpCard(), aux_basis: null, cabs_basis: null },
        ))))
        page()
        await screen.findByRole("heading", { name: "b3lyp/def2tzvp", level: 1 })
        expect(screen.getByText("B3LYP / def2-TZVP · density functional, hybrid")).toBeInTheDocument()
        expect(screen.getByText("unrestricted", { selector: ".lot-qualifier-subtitle" })).toBeInTheDocument()
        expect(screen.getByRole("region", { name: "About this method" })).toBeInTheDocument()
        expect(screen.getByRole("region", { name: "About this basis set" })).toBeInTheDocument()
    })

    it("shows no catalogue section when the server did not serve one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(lotRecord({}, undefined))))
        page()
        await screen.findByRole("heading", { name: "gfn2", level: 1 })
        expect(screen.queryByRole("region", { name: "Catalogue" })).toBeNull()
        expect(screen.queryByText(/not in the catalogue/)).toBeNull()
    })
})
