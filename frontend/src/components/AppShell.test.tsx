import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { BROWSE_KINDS, BROWSE_KIND_PATHS, RELATED_BROWSE_KIND, type BrowseKind } from "../api/browseApi"
import shellCss from "../app-shell.css?raw"
import { AppShell } from "./AppShell"
import { AuthProvider } from "./AuthProvider"
import { PageShell } from "./PageShell"
import { SectionHeading } from "./PageSections"

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

    it("tabs through the skip link, the wordmark, each link, the theme control, sign in, then the search box", async () => {
        const user = userEvent.setup()
        renderShell()
        await screen.findByRole("link", { name: "Sign in" })
        await user.tab(); expect(screen.getByText("Skip to content")).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "TCKDB home" })).toHaveFocus()
        for (const name of ["Molecules", "Reactions", "Methods", "Guide"]) {
            await user.tab(); expect(screen.getByRole("link", { name })).toHaveFocus()
        }
        // The theme control is one Tab stop: the radio that is on.
        await user.tab(); expect(screen.getByRole("radio", { checked: true })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("link", { name: "Sign in" })).toHaveFocus()
        await user.tab(); expect(screen.getByRole("searchbox", { name: "Search molecules and reactions" })).toHaveFocus()
    })

    it("puts the search last in the markup and gives it no order outside the narrow bar", () => {
        const { container } = renderShell()
        const end = container.querySelector(".site-bar-end")!
        expect(end.lastElementChild).toHaveClass("site-search")
        const cut = shellCss.indexOf("@media (max-width: 60rem)")
        expect(shellCss.slice(0, cut)).not.toMatch(/\.site-search[^{]*\{[^}]*\border\s*:/)
        expect(shellCss.slice(cut)).toMatch(/\.site-search \{ order: 3;/)
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

describe("phone More menu focus", () => {
    it("closes when Tab moves focus outside the menu", async () => {
        mockPhone(true)
        const user = userEvent.setup()
        renderShell()
        const button = screen.getByRole("button", { name: "More" })
        await user.click(button)
        expect(button).toHaveAttribute("aria-expanded", "true")
        // Shift+Tab from the button lands on the Methods tab, outside the menu.
        button.focus()
        await user.tab({ shift: true })
        expect(screen.getByRole("link", { name: "Methods" })).toHaveFocus()
        expect(button).toHaveAttribute("aria-expanded", "false")
    })

    it("closes when focus leaves the document", async () => {
        mockPhone(true)
        const user = userEvent.setup()
        renderShell()
        const button = screen.getByRole("button", { name: "More" })
        await user.click(button)
        expect(button).toHaveAttribute("aria-expanded", "true")
        fireEvent.focusOut(screen.getByRole("link", { name: "Guide" }), { relatedTarget: null })
        expect(button).toHaveAttribute("aria-expanded", "false")
    })
})

describe("site search input", () => {
    it("limits its text to 1024 characters", () => {
        renderShell()
        expect(screen.getByRole("searchbox", { name: "Search molecules and reactions" })).toHaveAttribute("maxlength", "1024")
    })
})

describe("skip link", () => {
    it("is followed, once main has focus, by the page content and not by the contents list", async () => {
        const user = userEvent.setup()
        render(<AuthProvider><MemoryRouter initialEntries={["/record"]}>
            <Routes><Route element={<AppShell />}><Route path="*" element={
                <PageShell identity={<a href="#top">first content link</a>}>
                    <SectionHeading id="one">One</SectionHeading>
                    <SectionHeading id="two">Two</SectionHeading>
                </PageShell>
            } /></Route></Routes>
        </MemoryRouter></AuthProvider>)
        // Following the skip link puts focus on main; the next Tab stop is the first thing inside it.
        screen.getByRole("main").focus()
        await user.tab()
        expect(screen.getByRole("link", { name: "first content link" })).toHaveFocus()
        expect(screen.getByRole("navigation", { name: "Sections on this page" })).not.toContainElement(document.activeElement as HTMLElement)
    })
})

/**
 * Every browse index must be reachable by clicking: from the site bar, or from a list page that is.
 * Derived from `BROWSE_KINDS`, so a new browse kind fails here until it is given a way in.
 */
describe("every browse index is reachable from the site bar or a list page", () => {
    it("reaches each browse kind's path", () => {
        renderShell()
        const nav = within(screen.getByRole("navigation", { name: "Primary navigation" }))
        const barHrefs = nav.getAllByRole("link").map((a) => a.getAttribute("href"))
        const reached = new Set<BrowseKind>(BROWSE_KINDS.filter((kind) => barHrefs.includes(BROWSE_KIND_PATHS[kind])))
        for (let changed = true; changed;) {
            changed = false
            for (const kind of [...reached]) {
                const next = RELATED_BROWSE_KIND[kind]
                if (!reached.has(next)) { reached.add(next); changed = true }
            }
        }
        expect([...reached].sort()).toEqual([...BROWSE_KINDS].sort())
    })
})
