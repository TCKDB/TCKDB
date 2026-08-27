import { StrictMode } from "react"
import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import App from "./App"

const speciesRef = "spc_abcde234567abcde234567abcd"
const speciesRefTwo = "spc_bcdef234567bcdef234567abcde"
const entryRef = "spe_cdefg234567cdefg234567abcd"
const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("public archive shell", () => {
    it("renders visible keyboard navigation and excludes the admin diagnostic route", async () => {
        const user = userEvent.setup(); render(<App />)
        expect(await screen.findByRole("heading", { name: "TCKDB" })).toBeVisible()
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "Species" })).toHaveFocus()
        expect(screen.queryByText(/Machine-Review Inspection/)).not.toBeInTheDocument()
    })

    it("offers a visible accessible Formula/SMILES choice for ambiguous input", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "Cl")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        expect(await screen.findByRole("link", { name: speciesRef })).toBeVisible()
    })

    it("completes a StrictMode search and clears its loading state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })))
        const user = userEvent.setup(); render(<StrictMode><App /></StrictMode>)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        const button = screen.getByRole("button", { name: "Search" })
        await user.click(button)
        expect(await screen.findByRole("link", { name: speciesRef })).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("routes spc references to species even when entries are returned", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [{ species_entry_ref: entryRef }] }] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), speciesRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "Species" })).toBeVisible()
        expect(screen.getByText(speciesRef)).toBeVisible()
    })

    it("routes spe references to the precise entry", async () => {
        const entry = {
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: false,
                has_statmech: false,
                has_transport: false,
                has_conformers: false,
                calculation_count: 0,
            },
        }
        const species = {
            species_ref: speciesRef,
            canonical_smiles: "O",
            inchi_key: "X",
            formula: "O",
            charge: 0,
            multiplicity: 1,
            entries: [entry],
        }
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [species] })),
            http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [] })),
        )
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), entryRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "O" })).toBeVisible()
        expect(screen.getByText(entryRef)).toBeVisible()
    })

    it("renders formula search results as species-grain Links and follows one", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [
            { species_ref: speciesRef, entries: [{ species_entry_ref: entryRef }] }, { species_ref: speciesRefTwo, entries: [] },
        ] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        const result = await screen.findByRole("link", { name: speciesRef })
        expect(result).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(screen.getByRole("link", { name: speciesRefTwo })).toBeVisible()
        await user.click(result)
        expect(await screen.findByRole("heading", { name: "Species" })).toBeVisible()
    })

    it("keeps structure search at entry grain", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [{ species_ref: speciesRef, species_entry_ref: entryRef }] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "smiles:CCO")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("link", { name: entryRef })).toHaveAttribute("href", `/species-entries/${entryRef}`)
    })

    it("keeps only the latest search result and does not navigate after unmount", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") await delay(40)
            return HttpResponse.json({ records: [{ species_ref: formula === "H2O" ? speciesRef : speciesRefTwo, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "H2O"); await user.click(screen.getByRole("button", { name: "Search" }))
        await user.clear(input); await user.type(input, "H2"); await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("link", { name: speciesRefTwo })).toBeVisible()
        cleanup(); await delay(60); expect(window.location.pathname).toBe("/")
    })

    it("renders empty, malformed-success, and HTTP-error states with idle busy state", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") return HttpResponse.json({ records: [] })
            if (formula === "Ca") return HttpResponse.json({ records: [{ species_ref: speciesRef }] })
            return HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("No exact formula record")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "Ca"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
        expect(button).toHaveAttribute("aria-busy", "false")
    })

    it("clears a slow valid request synchronously for invalid and ambiguous submits", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async () => {
            await delay(50)
            return HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        const button = screen.getByRole("button", { name: "Search" })
        await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "spc_BAD"); await user.click(button)
        expect(await screen.findByRole("status")).toHaveTextContent("26 lowercase base32")
        expect(button).toHaveAttribute("aria-busy", "false")
        await user.clear(input); await user.type(input, "H2O"); await user.click(button)
        await user.clear(input); await user.type(input, "Cl"); await user.click(button)
        expect(await screen.findByRole("group", { name: /Search “Cl” as/ })).toBeVisible()
        expect(button).toHaveAttribute("aria-busy", "false")
        await delay(60); expect(screen.queryByRole("link", { name: speciesRef })).not.toBeInTheDocument()
    })

    it("does not allow an ambiguity chooser to search stale textbox content", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            expect(new URL(request.url).searchParams.get("formula")).toBe("Br")
            return HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "Cl"); await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("button", { name: "Formula" })).toBeVisible()
        await user.clear(input); await user.type(input, "Br")
        expect(screen.queryByRole("button", { name: "Formula" })).not.toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("group", { name: /Search “Br” as/ })).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Formula" }))
        expect(await screen.findByRole("link", { name: speciesRef })).toBeVisible()
    })
})

const publicRoutes: Array<[path: string, heading: string, ref?: string]> = [
    ["/species", "Species", undefined], ["/species/spc_abcde234567abcde234567abcd", "Species", speciesRef],
    ["/conformer-groups/cfg_abc", "Conformer group", "cfg_abc"], ["/conformer-observations/cfo_abc", "Conformer observation", "cfo_abc"],
    ["/calculations/calc_abc", "Calculation", "calc_abc"], ["/geometries/geom_abc", "Geometry", "geom_abc"],
    ["/reactions", "Reactions", undefined], ["/reactions/rxn_abc", "Reaction", "rxn_abc"], ["/methods", "Methods", undefined],
]

describe.each(publicRoutes)("route shell %s", (path, heading, ref) => {
    it("renders the declared public route deterministically", async () => {
        window.history.replaceState({}, "", path); render(<App />)
        expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
        if (ref) expect(screen.getByText(ref)).toBeVisible()
        if (path.includes("/species-entries/") && path.split("/").length === 4) {
            expect(screen.getByText(path.split("/").at(-1) ?? "", { selector: "code" })).toBeVisible()
        }
    })
})

it("retains the admin machine-review route outside public navigation", async () => {
    window.history.replaceState({}, "", "/admin/machine-review-inspection")
    render(<QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider>)
    expect(await screen.findByRole("heading", { name: "Submission Machine-Review Inspection" })).toBeVisible()
})
