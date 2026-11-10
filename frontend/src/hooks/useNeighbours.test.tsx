import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { SpeciesEntryRelationshipMap } from "../components/RelationshipMap"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterAll(() => server.close())

const hits: string[] = []

function mockReads(overrides: Partial<Record<string, () => Response>> = {}) {
    const reply = (name: string, body: Record<string, unknown>) => () => {
        hits.push(name)
        return overrides[name]?.() ?? HttpResponse.json(body)
    }
    server.use(
        http.get("/api/v1/scientific/species/search", reply("species", { records: [{ species_ref: "spc_1" }], pagination: { total: 1 } })),
        http.get("/api/v1/scientific/conformers/search", reply("conformers", { records: [{ conformer_group: { conformer_group_ref: "cg_1" } }], pagination: { total: 3 } })),
        http.get("/api/v1/scientific/species-calculations/search", reply("calculations", { records: [], pagination: { total: 0 } })),
        http.get("/api/v1/scientific/networks/search", reply("networks", { records: [{ network: { network_ref: "net_1" } }], pagination: { total: 1 } })),
        http.get("/api/v1/scientific/reactions/search", reply("reactions", { records: [{ reaction_entry_ref: "rxe_1" }], pagination: { total: 2 } })),
    )
}

type ObserverCallback = (entries: { isIntersecting: boolean }[]) => void
let observers: { callback: ObserverCallback; options?: IntersectionObserverInit }[] = []

beforeEach(() => {
    hits.length = 0
    observers = []
    class FakeObserver {
        constructor(callback: ObserverCallback, options?: IntersectionObserverInit) { observers.push({ callback, options }) }
        observe() {}
        disconnect() {}
        unobserve() {}
    }
    vi.stubGlobal("IntersectionObserver", FakeObserver)
})
afterEach(() => {
    cleanup()
    server.resetHandlers()
    vi.unstubAllGlobals()
})

const map = (entryRef = "spe_1") => (
    <MemoryRouter><SpeciesEntryRelationshipMap entryRef={entryRef} smiles="CCO" /></MemoryRouter>
)

describe("species entry neighbours", () => {
    it("makes no request until the map is near the viewport", async () => {
        mockReads()
        render(map())
        await new Promise((resolve) => setTimeout(resolve, 30))
        expect(hits).toEqual([])
        expect(observers).toHaveLength(1)
        expect(observers[0].options?.rootMargin).toBeTruthy()
        act(() => observers[0].callback([{ isIntersecting: true }]))
        await waitFor(() => expect([...hits].sort()).toEqual(["calculations", "conformers", "networks", "reactions", "species"]))
    })

    it("builds nodes from what the reads returned, leaves out an empty read, and shows the server's totals", async () => {
        mockReads()
        render(map())
        act(() => observers[0].callback([{ isIntersecting: true }]))
        const list = await screen.findByRole("list", { name: "Connected records" })
        await waitFor(() => expect(list.textContent).toContain("Reactions with this molecule"))
        expect(list.textContent).toContain("The molecule")
        expect(list.textContent).toContain("3 found")
        expect(list.textContent).toContain("2 found")
        expect(list.textContent).not.toContain("Calculations")
        for (const link of screen.getAllByRole("link", { name: /^Networks/ })) expect(link).toHaveAttribute("href", "/networks/net_1")
        expect(screen.queryAllByRole("link", { name: /^Reactions with this molecule/ })).toHaveLength(0)
    })

    it("sends the molecule's SMILES as reactants with direction either", async () => {
        let seen: URLSearchParams | null = null
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [], pagination: { total: 0 } })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [], pagination: { total: 0 } })),
            http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({ records: [], pagination: { total: 0 } })),
            http.get("/api/v1/scientific/networks/search", () => HttpResponse.json({ records: [], pagination: { total: 0 } })),
            http.get("/api/v1/scientific/reactions/search", ({ request }) => {
                seen = new URL(request.url).searchParams
                return HttpResponse.json({ records: [], pagination: { total: 0 } })
            }),
        )
        render(map())
        act(() => observers[0].callback([{ isIntersecting: true }]))
        await waitFor(() => expect(seen).not.toBeNull())
        expect(seen!.get("reactants")).toBe("CCO")
        expect(seen!.get("direction")).toBe("either")
        expect(seen!.get("limit")).toBe("1")
    })

    it("shares requests between two maps for the same entry", async () => {
        mockReads()
        render(<MemoryRouter><SpeciesEntryRelationshipMap entryRef="spe_1" smiles="CCO" /><SpeciesEntryRelationshipMap entryRef="spe_1" smiles="CCO" /></MemoryRouter>)
        act(() => observers.forEach((o) => o.callback([{ isIntersecting: true }])))
        await waitFor(() => expect(hits.length).toBeGreaterThanOrEqual(5))
        await new Promise((resolve) => setTimeout(resolve, 30))
        expect(hits).toHaveLength(5)
    })

    it("leaves out the node of a read that fails and keeps the rest", async () => {
        mockReads({ conformers: () => HttpResponse.json({ error: "boom" }, { status: 500 }) as unknown as Response })
        render(map())
        act(() => observers[0].callback([{ isIntersecting: true }]))
        const list = await screen.findByRole("list", { name: "Connected records" })
        await waitFor(() => expect(list.textContent).toContain("The molecule"))
        expect(list.textContent).not.toContain("Conformer groups")
    })
})
