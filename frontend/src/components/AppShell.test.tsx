import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { BROWSE_KINDS, BROWSE_KIND_PATHS } from "../api/browseApi"
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

/**
 * Every browse index must be reachable by clicking, from the primary nav.
 *
 * This guard exists because it was violated the moment the per-page kind
 * switcher was deleted: the switcher had been the ONLY in-app route to
 * `/transition-states` and `/vdw-complexes`, so removing it orphaned both.
 * Transition states was put in the nav in the same change and van der Waals
 * was missed, leaving an index that existed, held a route, rendered a page,
 * and could not be reached from anywhere in the app.
 *
 * Derived from `BROWSE_KINDS`/`BROWSE_KIND_PATHS` rather than a hand-written
 * list, so a FIFTH browse kind added later fails here until it is given a way
 * in. A hand-listed assertion would have to be remembered, which is exactly
 * what was not remembered the first time.
 */
describe("primary navigation reaches every browse index", () => {
    it("links to each browse kind's own path", () => {
        render(<AuthProvider><MemoryRouter><AppShell /></MemoryRouter></AuthProvider>)
        const nav = within(screen.getByRole("navigation", { name: "Primary navigation" }))
        const hrefs = nav.getAllByRole("link").map((a) => a.getAttribute("href"))
        for (const kind of BROWSE_KINDS) {
            expect(hrefs).toContain(BROWSE_KIND_PATHS[kind])
        }
    })
})
