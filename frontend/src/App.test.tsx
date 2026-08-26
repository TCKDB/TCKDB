import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "./App"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("public archive shell", () => {
    it("renders keyboard-visible navigation and the archive landing", async () => {
        const user = userEvent.setup()
        render(<App />)
        expect(await screen.findByRole("heading", { name: "TCKDB" })).toBeVisible()
        await user.tab()
        expect(screen.getByText("Skip to content")).toHaveFocus()
        expect(screen.queryByText(/Machine-Review Inspection/)).not.toBeInTheDocument()
    })

    it("shows invalid and empty search states", async () => {
        const user = userEvent.setup()
        render(<App />)
        const input = await screen.findByLabelText("Exact species identifier")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("status")).toHaveTextContent("Enter a formula")
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [] })))
        await user.type(input, "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("status")).toHaveTextContent("No exact formula record")
    })

    it("routes a successful search to the stable record", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{ species_ref: "spec_water", entries: [{ species_entry_ref: "se_water" }] }] })))
        const user = userEvent.setup()
        render(<App />)
        await user.type(await screen.findByLabelText("Exact species identifier"), "H2O")
        await user.click(screen.getByRole("button", { name: "Search" }))
        expect(await screen.findByRole("heading", { name: "Species entry" })).toBeVisible()
        expect(screen.getByText("se_water")).toBeVisible()
    })
})
