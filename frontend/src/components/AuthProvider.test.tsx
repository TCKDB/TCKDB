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
