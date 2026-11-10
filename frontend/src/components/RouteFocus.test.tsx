import { useEffect, useState } from "react"
import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import { RouteFocus } from "./RouteFocus"
import { pageKey } from "../domain/pageKey"

afterEach(cleanup)

function Slow() {
    const [ready, setReady] = useState(false)
    useEffect(() => {
        const timer = setTimeout(() => setReady(true), 30)
        return () => clearTimeout(timer)
    }, [])
    // Keyed so the loading heading is replaced by a new element, as a lazily loaded page does.
    return ready ? <h1 key="loaded">Slow page loaded</h1> : <h1 key="loading">Slow page loading</h1>
}

function Harness({ start = "/a" }: { start?: string }) {
    return (
        <MemoryRouter initialEntries={[start]}>
            <RouteFocus />
            <main id="main-content" tabIndex={-1}>
                <nav>
                    <Link to="/a">to a</Link>
                    <Link to="/b">to b</Link>
                    <Link to="/a?filter=1">filter a</Link>
                    <Link to="/a#section">hash a</Link>
                    <Link to="/slow">to slow</Link>
                    <Link to="/species-entries/spe_1/thermo">thermo tab</Link>
                    <Link to="/species-entries/spe_1/statmech">statmech tab</Link>
                    <Link to="/species-entries/spe_2/thermo">other entry</Link>
                </nav>
                <Routes>
                    <Route path="/a" element={<h1>Page A</h1>} />
                    <Route path="/b" element={<h1>Page B</h1>} />
                    <Route path="/slow" element={<Slow />} />
                    <Route path="/species-entries/:ref/:section" element={<h1>Entry</h1>} />
                </Routes>
            </main>
        </MemoryRouter>
    )
}

describe("RouteFocus", () => {
    it("does not move focus on first load", () => {
        render(<Harness />)
        expect(document.body).toHaveFocus()
    })

    it("moves focus to the new page's h1 after a link navigation", async () => {
        const user = userEvent.setup()
        render(<Harness />)
        await user.click(screen.getByRole("link", { name: "to b" }))
        const heading = await screen.findByRole("heading", { name: "Page B" })
        await waitFor(() => expect(heading).toHaveFocus())
        expect(heading).toHaveAttribute("tabindex", "-1")
    })

    it("leaves focus alone when only the query string or hash changes", async () => {
        const user = userEvent.setup()
        render(<Harness />)
        const filter = screen.getByRole("link", { name: "filter a" })
        await user.click(filter)
        expect(filter).toHaveFocus()
        const hash = screen.getByRole("link", { name: "hash a" })
        await user.click(hash)
        expect(hash).toHaveFocus()
    })

    it("leaves focus on the tab when only the species-entry section changes, but moves it for another entry", async () => {
        const user = userEvent.setup()
        render(<Harness start="/species-entries/spe_1/thermo" />)
        const tab = screen.getByRole("link", { name: "statmech tab" })
        await user.click(tab)
        expect(tab).toHaveFocus()
        await user.click(screen.getByRole("link", { name: "other entry" }))
        await waitFor(() => expect(screen.getByRole("heading", { name: "Entry" })).toHaveFocus())
    })

    it("follows the heading when a page swaps its loading heading for the real one", async () => {
        const user = userEvent.setup()
        render(<Harness />)
        await user.click(screen.getByRole("link", { name: "to slow" }))
        await waitFor(() => expect(screen.getByRole("heading", { name: "Slow page loading" })).toHaveFocus())
        await waitFor(() => expect(screen.getByRole("heading", { name: "Slow page loaded" })).toHaveFocus())
    })

    it("stops following once the reader presses a key", async () => {
        const user = userEvent.setup()
        render(<Harness />)
        await user.click(screen.getByRole("link", { name: "to slow" }))
        const loading = await screen.findByRole("heading", { name: "Slow page loading" })
        await waitFor(() => expect(loading).toHaveFocus())
        await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true })) })
        await user.tab()
        await waitFor(() => expect(screen.getByRole("heading", { name: "Slow page loaded" })).toBeInTheDocument())
        expect(screen.getByRole("heading", { name: "Slow page loaded" })).not.toHaveFocus()
    })
})

describe("pageKey", () => {
    it("drops the species-entry section and a trailing slash, nothing else", () => {
        expect(pageKey("/species-entries/spe_1/thermo")).toBe("/species-entries/spe_1")
        expect(pageKey("/species-entries/spe_1")).toBe("/species-entries/spe_1")
        expect(pageKey("/species/")).toBe("/species")
        expect(pageKey("/")).toBe("/")
        expect(pageKey("/calculations/calc_1")).toBe("/calculations/calc_1")
    })
})
