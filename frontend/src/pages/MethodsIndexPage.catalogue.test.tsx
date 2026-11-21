import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import MethodsIndexPage from "./MethodsIndexPage"
import { auxCard, b3lypCard, def2tzvpCard, gfn2Card } from "../test/catalogueFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const ENVELOPE = { request: { include: [] }, review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 } }

function baseHandlers() {
    return [
        http.get("/api/v1/scientific/level-of-theories/browse", () => HttpResponse.json({ records: [], pagination: { offset: 0, limit: 200, returned: 0, total: 0 } })),
        http.get("/api/v1/scientific/meta/software", () => HttpResponse.json({ results: [] })),
        http.get("/api/v1/scientific/meta/workflow-tools", () => HttpResponse.json({ results: [] })),
    ]
}

describe("MethodsIndexPage: the catalogue tables", () => {
    it("lists methods and basis sets with full name, family, levels and calculations, each linking to its page", async () => {
        const dispersion = gfn2Card({
            catalogue_ref: "mcat_d3bj", key: "d3bj", keys: ["d3bj"], kind: "dispersion_correction", short_name: "D3(BJ)",
            full_name: "Grimme D3 dispersion correction with Becke-Johnson damping", family: null,
            usage: { level_usage_count: 0, calculation_usage_count: 0 },
        })
        server.use(
            ...baseHandlers(),
            http.get("/api/v1/scientific/methods", () => HttpResponse.json({ ...ENVELOPE, records: [b3lypCard(), dispersion, gfn2Card()] })),
            http.get("/api/v1/scientific/basis-sets", () => HttpResponse.json({ ...ENVELOPE, records: [auxCard(), def2tzvpCard()] })),
        )
        render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)

        const methods = await screen.findByRole("table", { name: "Method catalogue" })
        const b3lypRow = within(methods).getByRole("link", { name: "B3LYP" }).closest("tr") as HTMLElement
        expect(within(methods).getByRole("link", { name: "B3LYP" })).toHaveAttribute("href", "/methods/method/b3lyp")
        expect(within(b3lypRow).getByText("Becke three-parameter hybrid exchange with Lee-Yang-Parr correlation")).toBeInTheDocument()
        expect(within(b3lypRow).getByText("density functional, hybrid")).toBeInTheDocument()
        expect(within(b3lypRow).getByText("4")).toBeInTheDocument()
        expect(within(b3lypRow).getByText("416")).toBeInTheDocument()
        const d3Row = within(methods).getByRole("link", { name: "D3(BJ)" }).closest("tr") as HTMLElement
        expect(within(d3Row).getByText("dispersion correction")).toBeInTheDocument()
        // Rows stay in the server's key order.
        expect(within(methods).getAllByRole("link").map((link) => link.textContent)).toEqual(["B3LYP", "D3(BJ)", "GFN2-xTB"])

        const basis = await screen.findByRole("table", { name: "Basis-set catalogue" })
        expect(within(basis).getByRole("link", { name: "def2-TZVP" })).toHaveAttribute("href", "/methods/basis/def2-tzvp")
        expect(within(basis).getByRole("link", { name: "aug-cc-pVTZ/C" })).toHaveAttribute("href", "/methods/basis/bcat_augccpvtzc")
        expect(within(basis).getByText("Karlsruhe def2 · orbital basis")).toBeInTheDocument()
    })

    it("lists the spellings in use that no entry names under each table, in the server's order", async () => {
        const usage = (levels: number, calculations: number) => ({ level_usage_count: levels, calculation_usage_count: calculations })
        server.use(
            ...baseHandlers(),
            http.get("/api/v1/scientific/methods", () => HttpResponse.json({
                ...ENVELOPE,
                records: [b3lypCard()],
                uncatalogued: [
                    { key: "d2", kind: "dispersion_correction", family: null, dft_rung: null, spellings_seen: ["D2"], usage: usage(1, 2) },
                    { key: "pm7", kind: "method", family: "semiempirical", dft_rung: null, spellings_seen: ["PM7", "pm7"], usage: usage(2, 4) },
                ],
            })),
            http.get("/api/v1/scientific/basis-sets", () => HttpResponse.json({
                ...ENVELOPE,
                records: [def2tzvpCard()],
                uncatalogued: [{ key: "aug-cc-pvtz-f12", spellings_seen: ["aug-cc-pVTZ-F12"], usage: usage(1, 39) }],
            })),
        )
        render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)

        const methodSection = await screen.findByRole("region", { name: "Method catalogue" })
        const methodList = await within(methodSection).findByRole("region", { name: "In use, not in the catalogue" })
        expect(within(methodList).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
            "D2 (dispersion correction)Used by 1 level of theory · 2 calculations",
            "PM7, pm7Used by 2 levels of theory · 4 calculations",
        ])
        expect(within(methodList).queryByRole("link")).toBeNull()

        const basisSection = screen.getByRole("region", { name: "Basis-set catalogue" })
        const basisList = await within(basisSection).findByRole("region", { name: "In use, not in the catalogue" })
        expect(within(basisList).getByRole("listitem")).toHaveTextContent("aug-cc-pVTZ-F12Used by 1 level of theory · 39 calculations")
    })

    it("shows no uncatalogued list when every spelling in use has an entry", async () => {
        server.use(
            ...baseHandlers(),
            http.get("/api/v1/scientific/methods", () => HttpResponse.json({ ...ENVELOPE, records: [b3lypCard()], uncatalogued: [] })),
            http.get("/api/v1/scientific/basis-sets", () => HttpResponse.json({ ...ENVELOPE, records: [def2tzvpCard()] })),
        )
        render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)
        await screen.findByRole("table", { name: "Basis-set catalogue" })
        await screen.findByRole("table", { name: "Method catalogue" })
        expect(screen.queryByRole("heading", { name: "In use, not in the catalogue" })).toBeNull()
    })

    it("says so when a catalogue list cannot be loaded, and keeps the rest of the page", async () => {
        server.use(
            ...baseHandlers(),
            http.get("/api/v1/scientific/methods", () => HttpResponse.json({ detail: "down" }, { status: 503 })),
            http.get("/api/v1/scientific/basis-sets", () => HttpResponse.json({ ...ENVELOPE, records: [] })),
        )
        render(<MemoryRouter><MethodsIndexPage /></MemoryRouter>)
        const section = await screen.findByRole("region", { name: "Method catalogue" })
        expect(await within(section).findByRole("alert")).toHaveTextContent("could not load this list")
        expect(await screen.findByText("The basis-set catalogue has no entries.")).toBeInTheDocument()
        expect(screen.getByRole("heading", { name: "Levels of theory" })).toBeInTheDocument()
    })
})
