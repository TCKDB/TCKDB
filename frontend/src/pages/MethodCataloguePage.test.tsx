import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import MethodCataloguePage from "./MethodCataloguePage"
import { auxCard, def2tzvpCard, gfn2Card } from "../test/catalogueFixtures"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const ENVELOPE = { request: { include: [] }, review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 } }

function page(path: string) {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route path="/methods/method/:key" element={<MethodCataloguePage kind="method" />} />
                <Route path="/methods/basis/:key" element={<MethodCataloguePage kind="basis" />} />
            </Routes>
        </MemoryRouter>,
    )
}

describe("MethodCataloguePage: a method", () => {
    it("shows the names, family, description, titled references and the levels that use it", async () => {
        server.use(http.get("/api/v1/scientific/methods/gfn2-xtb", () => HttpResponse.json({
            ...ENVELOPE,
            record: { method: gfn2Card(), levels: [{ level_of_theory_ref: "lot_gfn2", label: "gfn2", calculation_usage_count: 2 }] },
        })))
        page("/methods/method/gfn2-xtb")
        expect(await screen.findByRole("heading", { name: "GFN2-xTB", level: 1 })).toBeInTheDocument()
        expect(screen.getByText("Geometry, Frequency, Noncovalent, eXtended Tight Binding, second parametrisation")).toBeInTheDocument()
        expect(screen.getByText("A self-consistent tight-binding method with multipole electrostatics.")).toBeInTheDocument()
        expect(screen.getByText("semiempirical")).toBeInTheDocument()
        expect(screen.getByText("gfn2-xtb, gfn2")).toBeInTheDocument()
        expect(screen.getByText("mcat_gfn2")).toBeInTheDocument()

        const references = screen.getByRole("region", { name: "References" })
        expect(within(references).getByText("Original article")).toBeInTheDocument()
        expect(within(references).getByText(/GFN2-xTB: an accurate and broadly parametrized/)).toBeInTheDocument()
        expect(within(references).getByRole("link", { name: "doi:10.1021/acs.jctc.8b01176" })).toHaveAttribute("href", "https://doi.org/10.1021/acs.jctc.8b01176")

        const levels = screen.getByRole("table", { name: "Levels of theory using this method" })
        expect(within(levels).getByRole("link", { name: "gfn2" })).toHaveAttribute("href", "/methods/lot_gfn2")
        expect(within(levels).getByText("2")).toBeInTheDocument()
        expect(screen.getByText(/This order is not a recommendation\./)).toBeInTheDocument()
    })

    it("says so when no listed level uses the entry", async () => {
        server.use(http.get("/api/v1/scientific/methods/gfn2-xtb", () => HttpResponse.json({
            ...ENVELOPE, record: { method: gfn2Card({ spellings_seen: [], usage: { level_usage_count: 0, calculation_usage_count: 0 } }), levels: [] },
        })))
        page("/methods/method/gfn2-xtb")
        await screen.findByRole("heading", { name: "GFN2-xTB", level: 1 })
        expect(screen.getByText("No level of theory with a listed calculation uses this method.")).toBeInTheDocument()
    })

    it("reports an unknown handle as missing", async () => {
        server.use(http.get("/api/v1/scientific/methods/nope", () => HttpResponse.json({ detail: "not found", code: "handle_not_found" }, { status: 404 })))
        page("/methods/method/nope")
        expect(await screen.findByText(/No method with this stable reference/)).toBeInTheDocument()
    })
})

describe("MethodCataloguePage: a basis set", () => {
    it("shows family and role and the levels that use it", async () => {
        server.use(http.get("/api/v1/scientific/basis-sets/def2-tzvp", () => HttpResponse.json({
            ...ENVELOPE,
            record: { basis_set: def2tzvpCard(), levels: [{ level_of_theory_ref: "lot_b3lyp", label: "b3lyp/def2tzvp", calculation_usage_count: 416 }] },
        })))
        page("/methods/basis/def2-tzvp")
        expect(await screen.findByRole("heading", { name: "def2-TZVP", level: 1 })).toBeInTheDocument()
        expect(screen.getByText("Karlsruhe def2 · orbital basis")).toBeInTheDocument()
        const levels = screen.getByRole("table", { name: "Levels of theory using this basis set" })
        expect(within(levels).getByRole("link", { name: "b3lyp/def2tzvp" })).toHaveAttribute("href", "/methods/lot_b3lyp")
    })

    it("reads an entry whose name holds a slash by its ref", async () => {
        server.use(http.get("/api/v1/scientific/basis-sets/bcat_augccpvtzc", () => HttpResponse.json({
            ...ENVELOPE, record: { basis_set: auxCard(), levels: [] },
        })))
        page("/methods/basis/bcat_augccpvtzc")
        expect(await screen.findByRole("heading", { name: "aug-cc-pVTZ/C", level: 1 })).toBeInTheDocument()
        expect(screen.getByText("No reference is recorded for this entry.")).toBeInTheDocument()
    })
})
