import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { AuthProvider } from "../components/AuthProvider"
import { AuthStatus } from "../components/AuthStatus"
import LoginPage from "./LoginPage"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

const meResponse = {
    id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters", role: "user", is_active: true,
}

/**
 * `AuthStatus` rides along in every render here so a test can assert the
 * ACTUAL session state ("Sign in" link vs. the signed-in name), not just
 * whatever `LoginPage` itself happens to render on failure -- the exact
 * distinction the brief's red-first criteria call out ("assert the user
 * is not signed in, not merely that an error appeared").
 */
function renderLoginPage() {
    return render(
        <AuthProvider>
            <MemoryRouter initialEntries={["/login"]}>
                <AuthStatus />
                <Routes>
                    <Route path="/login" element={<LoginPage />} />
                    <Route path="/account" element={<div>Account landing</div>} />
                </Routes>
            </MemoryRouter>
        </AuthProvider>,
    )
}

describe("LoginPage", () => {
    it("signs the user in on valid credentials, sending credentials: include", async () => {
        server.use(
            http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
            http.post("/api/v1/auth/login", async ({ request }) => {
                expect(request.credentials).toBe("include")
                expect(await request.json()).toEqual({ username: "calvin", password: "correct-horse" })
                return HttpResponse.json(meResponse)
            }),
        )
        const user = userEvent.setup()
        renderLoginPage()

        await user.type(await screen.findByLabelText("Username"), "calvin")
        await user.type(screen.getByLabelText("Password"), "correct-horse")
        await user.click(screen.getByRole("button", { name: "Sign in" }))

        expect(await screen.findByText("Account landing")).toBeInTheDocument()
        expect(await screen.findByRole("button", { name: "Calvin Pieters" })).toBeInTheDocument()
    })

    it("shows the server's message and leaves the user signed out on a failed login", async () => {
        server.use(
            http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
            http.post("/api/v1/auth/login", () => HttpResponse.json({ detail: "Invalid username or password." }, { status: 401 })),
        )
        const user = userEvent.setup()
        renderLoginPage()

        await user.type(await screen.findByLabelText("Username"), "calvin")
        await user.type(screen.getByLabelText("Password"), "wrong-password")
        await user.click(screen.getByRole("button", { name: "Sign in" }))

        expect(await screen.findByRole("alert")).toHaveTextContent("Invalid username or password.")
        // Not signed in: the header still offers "Sign in", not the
        // account name, and the page never navigated to /account.
        expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument()
        expect(screen.queryByText("Account landing")).not.toBeInTheDocument()
    })

    it("surfaces the specific 409 (username taken) on register, not a generic failure", async () => {
        server.use(
            http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })),
            http.post("/api/v1/auth/register", () => HttpResponse.json(
                { code: "username_taken", detail: "username_taken: That username is already in use.", context: {} },
                { status: 409 },
            )),
        )
        const user = userEvent.setup()
        renderLoginPage()

        await user.click(await screen.findByRole("radio", { name: "Create account" }))
        await user.type(screen.getByLabelText("Username"), "calvin")
        await user.type(screen.getByLabelText("Password"), "password123")
        await user.click(screen.getByRole("button", { name: "Create account" }))

        expect(await screen.findByRole("alert")).toHaveTextContent("That username is already in use.")
        expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument()
    })
})
