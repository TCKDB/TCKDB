import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AppShell } from "./AppShell"
import { AuthProvider } from "./AuthProvider"

// `AppShell` now renders `AuthStatus`, which reads `useAuth()` -- it must
// be mounted beneath an `AuthProvider`, which in turn probes `GET
// /auth/me` on mount. A signed-out default keeps this file's own
// assertions (about primary navigation, not auth) unaffected by that
// probe.
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

function renderShell(path = "/") {
    return render(<AuthProvider><MemoryRouter initialEntries={[path]}>
        <Routes><Route element={<AppShell />}><Route path="*" element={<p>page</p>} /></Route></Routes>
    </MemoryRouter></AuthProvider>)
}

/** Makes `(max-width: 680px)` (the phone layout) match or not. */
function mockPhone(matches: boolean) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: matches && query.includes("680px"),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia
}

afterEach(() => { delete (window as { matchMedia?: unknown }).matchMedia })

describe("primary navigation", () => {
    it("links to molecules, reactions, methods and the guide, in that order", () => {
        renderShell()
        const nav = within(screen.getByRole("navigation", { name: "Primary navigation" }))
        expect(nav.getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
            ["Molecules", "/species"], ["Reactions", "/reactions"], ["Methods", "/methods"], ["Guide", "/guide"],
        ])
    })

    it("tabs through the skip link, the wordmark, each link, then the search box", async () => {
        const user = userEvent.setup()
        renderShell()
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        for (const name of ["Molecules", "Reactions", "Methods", "Guide"]) {
            await user.tab(); expect(screen.getByRole("link", { name })).toHaveFocus()
        }
        await user.tab(); expect(screen.getByRole("searchbox", { name: "Search molecules and reactions" })).toHaveFocus()
    })

    it("marks only the link for the current page", () => {
        renderShell("/reactions/rxn_abc")
        const nav = within(screen.getByRole("navigation", { name: "Primary navigation" }))
        expect(nav.getByRole("link", { name: "Reactions" })).toHaveAttribute("aria-current", "page")
        for (const name of ["Molecules", "Methods", "Guide"]) {
            expect(nav.getByRole("link", { name })).not.toHaveAttribute("aria-current")
        }
    })
})

describe("phone navigation", () => {
    it("shows a bottom bar with three links and a More button, and moves the theme control into More", async () => {
        mockPhone(true)
        const user = userEvent.setup()
        renderShell()
        const bar = within(screen.getByRole("navigation", { name: "Primary navigation" }))
        expect(bar.getAllByRole("link").map((a) => a.textContent)).toEqual(["Molecules", "Reactions", "Methods"])
        expect(screen.queryByRole("radiogroup", { name: "Theme" })).toBeNull()
        await user.click(bar.getByRole("button", { name: "More" }))
        expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeVisible()
        expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute("href", "/guide")
    })

    it("opens More with Enter, closes it with Escape and returns focus to the button", async () => {
        mockPhone(true)
        const user = userEvent.setup()
        renderShell()
        const button = screen.getByRole("button", { name: "More" })
        expect(button).toHaveAttribute("aria-expanded", "false")
        button.focus()
        await user.keyboard("{Enter}")
        expect(button).toHaveAttribute("aria-expanded", "true")
        expect(screen.getByRole("link", { name: "Guide" })).toHaveFocus()
        await user.keyboard("{Escape}")
        expect(button).toHaveAttribute("aria-expanded", "false")
        expect(button).toHaveFocus()
    })
})

describe("site search", () => {
    it("is a labelled search landmark", () => {
        renderShell()
        expect(screen.getByRole("search")).toBeInTheDocument()
    })
})
