import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { AuthStatus } from "./AuthStatus"
import { AuthProvider } from "./AuthProvider"

/**
 * The review queue is gated on `require_curator_or_admin`, not
 * `require_admin` -- so a **curator** is the role it exists for.
 *
 * When the page was first built its only link lived inside `/admin`,
 * which a curator cannot open. The page was reachable by typing the
 * address and no other way: a surface built for a role that role could
 * not find. Caught by looking at the rendered header, not by any test,
 * which is why this file exists.
 *
 * This is navigation, not authorisation. Hiding a link stops nobody; the
 * server gates every write, and `ReviewQueuePage` refuses to render the
 * queue without the role either.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function meIs(role: string) {
    server.use(
        http.get("/api/v1/auth/me", () =>
            HttpResponse.json({
                id: 1,
                username: "someone",
                email: "someone@example.com",
                full_name: null,
                role,
                is_active: true,
            }),
        ),
    )
}

function renderStatus() {
    return render(
        <AuthProvider>
            <MemoryRouter>
                <AuthStatus />
            </MemoryRouter>
        </AuthProvider>,
    )
}

describe("who is offered the review queue in the header", () => {
    it("a curator is, because it is their page", async () => {
        meIs("curator")
        renderStatus()

        const link = await screen.findByRole("link", { name: "Review" })
        expect(link).toHaveAttribute("href", "/review-queue")
    })

    it("an admin is too", async () => {
        meIs("admin")
        renderStatus()

        expect(await screen.findByRole("link", { name: "Review" })).toHaveAttribute(
            "href",
            "/review-queue",
        )
    })

    it("a plain signed-in user is not", async () => {
        meIs("user")
        renderStatus()

        // Wait for the session to resolve before asserting an absence,
        // or this passes while still loading and proves nothing.
        await screen.findByRole("link", { name: "someone" })
        expect(screen.queryByRole("link", { name: "Review" })).not.toBeInTheDocument()
    })

    it("a curator is still not offered the admin page", async () => {
        meIs("curator")
        renderStatus()

        await screen.findByRole("link", { name: "Review" })
        // The two are different gates: curator/admin for reviewing,
        // admin for everything under /admin.
        expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument()
    })
})
