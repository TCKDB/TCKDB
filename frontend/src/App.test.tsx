import { delay, http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "./App"

const speciesRef = "spc_5bxnghp44yj0hf2vp9k1a6tk20"
const entryRef = "spe_01J9X8K3Y2RM4F0X8K3Y2RM4F0"
const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("public archive shell", () => {
    it("renders visible keyboard navigation and excludes the admin diagnostic route", async () => {
        const user = userEvent.setup()
        render(<App />)
        expect(await screen.findByRole("heading", { name: "TCKDB" })).toBeVisible()
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "Species" })).toHaveFocus()
        expect(screen.queryByText(/Machine-Review Inspection/)).not.toBeInTheDocument()
    })

    it("shows invalid, empty, and rendered malformed-response error states", async () => {
        const user = userEvent.setup()
        render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("status")).toHaveTextContent("Enter a formula")
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [] })))
        await user.type(input, "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("status")).toHaveTextContent("No exact formula record")
        await user.clear(input); await user.type(input, "Ca")
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: speciesRef }] })))
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("status")).toHaveTextContent("could not complete")
    })

    it("renders deterministic result links for formula searches", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [
            { species_ref: speciesRef, entries: [] }, { species_ref: "spc_6bxnghp44yj0hf2vp9k1a6tk20", entries: [{ species_entry_ref: entryRef }] },
        ] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("link", { name: speciesRef })).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(screen.getByRole("link", { name: entryRef })).toHaveAttribute("href", `/species-entries/${entryRef}`)
    })

    it("keeps only the latest search result when a prior request resolves late", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async ({ request }) => {
            const formula = new URL(request.url).searchParams.get("formula")
            if (formula === "H2O") await delay(40)
            return HttpResponse.json({ records: [{ species_ref: formula === "H2O" ? speciesRef : "spc_6bxnghp44yj0hf2vp9k1a6tk20", entries: [] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.type(input, "H2O"); await user.click(screen.getByRole("button", { name: "Search" }))
        await user.clear(input); await user.type(input, "H2"); await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("link", { name: "spc_6bxnghp44yj0hf2vp9k1a6tk20" })).toBeVisible()
        expect(screen.queryByRole("link", { name: speciesRef })).not.toBeInTheDocument()
    })

    it("does not navigate after an in-flight public-reference search unmounts", async () => {
        server.use(http.get("/api/v1/scientific/species/search", async () => {
            await delay(40)
            return HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [{ species_entry_ref: entryRef }] }] })
        }))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), entryRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        cleanup(); await delay(60)
        expect(window.location.pathname).toBe("/")
    })

    it("routes public references and renders record-shell subsection refs", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: speciesRef, entries: [{ species_entry_ref: entryRef }] }] })))
        const user = userEvent.setup(); render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), entryRef)
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "Species entry" })).toBeVisible()
        expect(screen.getByText(entryRef)).toBeVisible()
        window.history.replaceState({}, "", `/species-entries/${entryRef}/thermo`)
        cleanup(); render(<App />)
        expect(await screen.findByRole("heading", { name: "Species entry section" })).toBeVisible()
        expect(screen.getByText(entryRef)).toBeVisible()
    })
})
