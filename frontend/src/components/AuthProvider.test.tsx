import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { AuthProvider } from "./AuthProvider"
import { AuthStatus } from "./AuthStatus"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const meResponse = {
    id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters", role: "user", is_active: true,
}

function renderStatus() {
    return render(
        <AuthProvider>
            <MemoryRouter><AuthStatus /></MemoryRouter>
        </AuthProvider>,
    )
}

describe("AuthProvider seeding", () => {
    it("a live session cookie (GET /auth/me -> 200) yields signed-in state on load", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        renderStatus()
        expect(await screen.findByRole("link", { name: "Calvin Pieters" })).toBeInTheDocument()
    })

    it("a 401 yields signed-out state and shows no error", async () => {
        server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
        renderStatus()
        expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument()
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    })

    it("a 500 does NOT claim the visitor is signed out", async () => {
        /**
         * A 401 is the server stating a fact: this visitor has no session.
         * A 500, a 502 or a dropped connection states nothing, and the two
         * must not render the same.
         *
         * `fetchMe` already separates them (401 returns null, anything else
         * throws). The provider used to catch every rejection and set
         * `signed-out`, discarding that. The cost is concrete: during an API
         * restart a signed-in curator reloads, is told "Sign in", and is
         * bounced off /account while their cookie is still valid. They then
         * retype a password to replace a session they never lost.
         *
         * Found in review of #467, where leaving the state `loading` forever
         * on rejection passed all 58 tests.
         */
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))
        renderStatus()

        // Nothing is asserted about the session, either way.
        await expect(screen.findByRole("link", { name: "Sign in" }, { timeout: 300 })).rejects.toThrow()
        expect(screen.queryByRole("link", { name: "Calvin Pieters" })).not.toBeInTheDocument()
    })
})

describe("logout", () => {
    it("calls POST /auth/logout AND clears client state", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        let logoutCalls = 0
        server.use(http.post("/api/v1/auth/logout", ({ request }) => {
            logoutCalls += 1
            expect(request.credentials).toBe("include")
            return new HttpResponse(null, { status: 204 })
        }))

        const user = userEvent.setup()
        renderStatus()
        await screen.findByRole("link", { name: "Calvin Pieters" })

        await user.click(screen.getByRole("button", { name: "Sign out" }))

        // Both halves of the red-first criterion, in one place: the
        // endpoint was actually reached, AND the client's own state
        // reverted to signed-out (not just "no error was thrown").
        expect(logoutCalls).toBe(1)
        expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument()
        expect(screen.queryByRole("link", { name: "Calvin Pieters" })).not.toBeInTheDocument()
    })
})
