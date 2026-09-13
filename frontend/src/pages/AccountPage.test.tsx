import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AuthProvider } from "../components/AuthProvider"
import AccountPage from "./AccountPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const meResponse = {
    id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters", role: "user", is_active: true,
}

function signedInHandlers(keys: unknown[] = []) {
    return [
        http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)),
        http.get("/api/v1/auth/api-keys", () => HttpResponse.json(keys)),
    ]
}

function renderAccountPage() {
    return render(
        <AuthProvider>
            <MemoryRouter initialEntries={["/account"]}>
                <Routes>
                    <Route path="/account" element={<AccountPage />} />
                    <Route path="/login" element={<div>Login landing</div>} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

describe("AccountPage", () => {
    it("redirects a signed-out visitor to /login instead of rendering the account page", async () => {
        server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
        renderAccountPage()
        expect(await screen.findByText("Login landing")).toBeInTheDocument()
    })

    it("lists the signed-in user's identity and an empty key state", async () => {
        server.use(...signedInHandlers([]))
        renderAccountPage()
        expect(await screen.findByText("Calvin Pieters")).toBeInTheDocument()
        expect(await screen.findByText("No API keys yet.")).toBeInTheDocument()
    })

    it("shows a newly created key's plaintext once, and never again after the list is re-fetched", async () => {
        server.use(...signedInHandlers([]))
        server.use(http.post("/api/v1/auth/api-keys", () => HttpResponse.json({
            id: 9, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null,
            key: "tckdb_live_supersecret",
        }, { status: 201 })))

        const user = userEvent.setup()
        renderAccountPage()
        await screen.findByText("No API keys yet.")

        await user.type(screen.getByLabelText("Label"), "ARC on Zeus")
        await user.click(screen.getByRole("button", { name: "Create API key" }))

        expect(await screen.findByText("tckdb_live_supersecret")).toBeInTheDocument()
        expect(screen.getByRole("cell", { name: "ARC on Zeus" })).toBeInTheDocument()

        // Re-fetching the list (a revoke, here of the same key -- any
        // authoritative reload of `GET /auth/api-keys` is the same event)
        // must not carry the plaintext along with it.
        server.use(
            http.delete("/api/v1/auth/api-keys/9", () => new HttpResponse(null, { status: 204 })),
            http.get("/api/v1/auth/api-keys", () => HttpResponse.json([
                { id: 9, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: "2026-09-13T00:05:00Z" },
            ])),
        )
        await user.click(screen.getByRole("button", { name: "Revoke" }))
        await waitFor(() => expect(screen.getByRole("cell", { name: "revoked" })).toBeInTheDocument())
        expect(screen.queryByText("tckdb_live_supersecret")).not.toBeInTheDocument()
    })

    it("does not carry a revealed key's plaintext across an unmount/remount (navigating away and back)", async () => {
        server.use(...signedInHandlers([]))
        server.use(http.post("/api/v1/auth/api-keys", () => HttpResponse.json({
            id: 11, label: null, created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null,
            key: "tckdb_live_anothersecret",
        }, { status: 201 })))

        const user = userEvent.setup()
        const { unmount } = renderAccountPage()
        await screen.findByText("No API keys yet.")
        await user.click(screen.getByRole("button", { name: "Create API key" }))
        expect(await screen.findByText("tckdb_live_anothersecret")).toBeInTheDocument()

        unmount()

        server.use(...signedInHandlers([
            { id: 11, label: null, created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null },
        ]))
        renderAccountPage()
        await screen.findByText("Calvin Pieters")
        expect(screen.queryByText("tckdb_live_anothersecret")).not.toBeInTheDocument()
    })
})
