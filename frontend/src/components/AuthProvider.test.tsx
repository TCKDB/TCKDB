import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { AuthProvider } from "./AuthProvider"
import { AuthStatus } from "./AuthStatus"
import { useAuth } from "../hooks/useAuth"

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

describe("a sign-out that never reached the server", () => {
    /**
     * Local state is cleared either way -- the intent is unambiguous, and
     * every mainstream session-cookie site does the same. But the session
     * row is still live and the cookie still set, and because the cookie is
     * httpOnly nothing on the client can revoke it. The session TTL bounds
     * the window (12 hours for an admin) without closing it.
     *
     * So the one thing that must not happen is claiming the sign-out
     * completed. On a shared machine that is the difference between a user
     * who knows to check and one who walks away.
     */
    it("still clears local state, and records that the server was not told", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        server.use(http.post("/api/v1/auth/logout", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))

        const seen: boolean[] = []
        function Probe() {
            const { state, signOutIncomplete } = useAuth()
            seen.push(signOutIncomplete)
            return <span data-testid="who">{state.status}</span>
        }

        render(
            <AuthProvider>
                <MemoryRouter>
                    <AuthStatus />
                    <Probe />
                </MemoryRouter>
            </AuthProvider>,
        )

        await screen.findByRole("button", { name: "Sign out" })
        await userEvent.click(screen.getByRole("button", { name: "Sign out" }))

        // Cleared regardless: the name is gone.
        expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument()
        // And the failure is recorded rather than swallowed.
        expect(seen.at(-1)).toBe(true)
    })

    it("a sign-out that did reach the server records nothing", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        server.use(http.post("/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })))

        const seen: boolean[] = []
        function Probe() {
            const { signOutIncomplete } = useAuth()
            seen.push(signOutIncomplete)
            return null
        }

        render(
            <AuthProvider>
                <MemoryRouter>
                    <AuthStatus />
                    <Probe />
                </MemoryRouter>
            </AuthProvider>,
        )

        await screen.findByRole("button", { name: "Sign out" })
        await userEvent.click(screen.getByRole("button", { name: "Sign out" }))
        await screen.findByRole("link", { name: "Sign in" })

        // A flag that is always true is decoration.
        expect(seen.every((value) => value === false)).toBe(true)
    })
})

describe("the sign-out request survives the page going away", () => {
    it("sends keepalive so a closing tab does not strand the session", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        let keepalive: boolean | undefined
        server.events.on("request:start", ({ request }) => {
            if (request.method === "POST" && request.url.includes("/auth/logout")) keepalive = request.keepalive
        })
        server.use(http.post("/api/v1/auth/logout", () => new HttpResponse(null, { status: 204 })))

        render(
            <AuthProvider>
                <MemoryRouter><AuthStatus /></MemoryRouter>
            </AuthProvider>,
        )
        await screen.findByRole("button", { name: "Sign out" })
        await userEvent.click(screen.getByRole("button", { name: "Sign out" }))
        await screen.findByRole("link", { name: "Sign in" })

        expect(keepalive).toBe(true)
    })
})
