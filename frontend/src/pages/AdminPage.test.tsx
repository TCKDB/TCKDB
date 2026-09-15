import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import AdminPage from "./AdminPage"
import { AuthProvider } from "../components/AuthProvider"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

// The page also renders `StorageCapacityPanel`, which reads its own
// endpoint on mount. These tests are about accounts and roles, so the
// store is quiet by default -- without this the panel's failed read adds
// a second `role="alert"` and the row-error assertions below become
// ambiguous. `StorageCapacityPanel.test.tsx` covers the panel itself.
beforeEach(() => {
    server.use(http.get("/api/v1/admin/artifact-storage/capacity", () => HttpResponse.json({
        storage_full: false, storage_full_observed_at: null, s3_code: null, refused_bytes: null,
    })))
})

const admin = {
    id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters",
    role: "admin", is_active: true,
}
const plainUser = { ...admin, id: 2, username: "noah", full_name: null, role: "user" }

function userRow(over: Partial<Record<string, unknown>> = {}) {
    return {
        id: 2, username: "noah", full_name: null, affiliation: null,
        role: "user", is_active: true, created_at: "2026-01-15T09:00:00", ...over,
    }
}

function meIs(who: Record<string, unknown>) {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(who)))
}

function usersAre(items: Record<string, unknown>[]) {
    server.use(http.get("/api/v1/admin/users", () =>
        HttpResponse.json({ items, total: items.length, skip: 0, limit: 200 })))
}

function renderPage() {
    return render(
        <AuthProvider>
            <MemoryRouter><AdminPage /></MemoryRouter>
        </AuthProvider>,
    )
}

describe("who can see /admin", () => {
    it("an admin sees the accounts and the role each one holds", async () => {
        meIs(admin)
        usersAre([
            userRow({ id: 1, username: "calvin", full_name: "Calvin Pieters", role: "admin" }),
            userRow(),
        ])
        renderPage()

        expect(await screen.findByRole("combobox", { name: "Role for noah" })).toHaveValue("user")
        expect(screen.getByRole("combobox", { name: "Role for calvin" })).toHaveValue("admin")
    })

    it("a signed-in non-admin is told, and no account list is fetched", async () => {
        meIs(plainUser)
        // Deliberately NO /admin/users handler. `onUnhandledRequest: "error"`
        // turns any request into a failure, so this asserts the page does not
        // even try -- not merely that it hid the result.
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent(/for administrators/i)
        expect(screen.queryByRole("table")).not.toBeInTheDocument()
    })

    it("a backend outage does not render as 'you are not an admin'", async () => {
        /**
         * The same distinction #467 drew for the header. A 500 on /auth/me
         * states nothing about this visitor's role, and telling a real admin
         * they lack the role -- or bouncing them to a login form -- is a
         * claim the archive never made.
         */
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))
        renderPage()

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not reach the archive/i)
        expect(alert).not.toHaveTextContent(/administrator/i)
    })
})

describe("changing a role", () => {
    it("PATCHes the chosen role and reflects the row the server returns", async () => {
        meIs(admin)
        usersAre([userRow()])
        let patched: { id: string; body: unknown } | null = null
        server.use(http.patch("/api/v1/admin/users/:id/role", async ({ params, request }) => {
            patched = { id: String(params.id), body: await request.json() }
            return HttpResponse.json({ id: 2, username: "noah", role: "curator" })
        }))

        renderPage()
        const select = await screen.findByRole("combobox", { name: "Role for noah" })
        await userEvent.selectOptions(select, "curator")

        // Both halves: the request carried the right id and role, AND the
        // rendered row moved. A test asserting only the select's value would
        // pass on a control that never called the server at all.
        expect(patched).toEqual({ id: "2", body: { role: "curator" } })
        expect(await screen.findByRole("combobox", { name: "Role for noah" })).toHaveValue("curator")
    })

    it("shows the archive's own sentence when it refuses to remove the last admin", async () => {
        meIs(admin)
        usersAre([userRow({ id: 1, username: "calvin", full_name: "Calvin Pieters", role: "admin" })])
        server.use(http.patch("/api/v1/admin/users/:id/role", () => HttpResponse.json({
            code: "last_admin_demotion",
            detail: "last_admin_demotion: this is the archive's only active admin, and no route can grant the role back once nobody holds it. Promote another account to admin first, then change this one.",
            context: {},
        }, { status: 409 })))

        renderPage()
        const select = await screen.findByRole("combobox", { name: "Role for calvin" })
        await userEvent.selectOptions(select, "curator")

        const alert = await screen.findByRole("alert")
        // The repair instruction is the part that matters, and the `code: `
        // prefix must be stripped -- a reader should not see wire format.
        expect(alert).toHaveTextContent(/promote another account to admin first/i)
        expect(alert.textContent).not.toContain("last_admin_demotion:")
        // And the row must not pretend the change happened.
        expect(screen.getByRole("combobox", { name: "Role for calvin" })).toHaveValue("admin")
    })

    it("a failed change leaves the other rows alone", async () => {
        meIs(admin)
        usersAre([
            userRow({ id: 1, username: "calvin", full_name: "Calvin Pieters", role: "admin" }),
            userRow(),
        ])
        server.use(http.patch("/api/v1/admin/users/:id/role", () =>
            HttpResponse.json({ code: "http_500", detail: "boom", context: {} }, { status: 500 })))

        renderPage()
        await userEvent.selectOptions(
            await screen.findByRole("combobox", { name: "Role for noah" }), "curator")

        await screen.findByRole("alert")
        expect(screen.getByRole("combobox", { name: "Role for calvin" })).toHaveValue("admin")
        expect(screen.getByRole("combobox", { name: "Role for noah" })).toHaveValue("user")
    })
})

describe("what the page does not show", () => {
    it("renders no email address even if one somehow arrives in the payload", async () => {
        /**
         * The backend withholds `email` from this route by decision. This
         * asserts the page is not a second place that would leak it: the
         * payload below carries one, and it must not reach the document.
         */
        meIs(admin)
        server.use(http.get("/api/v1/admin/users", () => HttpResponse.json({
            items: [{ ...userRow(), email: "noah@example.com" }],
            total: 1, skip: 0, limit: 200,
        })))

        renderPage()
        await screen.findByRole("combobox", { name: "Role for noah" })

        const table = screen.getByRole("table")
        expect(within(table).queryByText(/noah@example\.com/)).not.toBeInTheDocument()
        expect(document.body.textContent).not.toContain("noah@example.com")
    })

    it("carries no in-page nav duplicating the header menu", async () => {
        /**
         * There used to be a `<nav className="admin-nav">` here with links
         * to Record review, Machine findings and Machine-review inspection.
         * The header's account menu offers all three from every route, so
         * the block was a second copy of the same navigation on one of the
         * routes it pointed at -- two things to keep in step, and the owner
         * read the duplication as the defect rather than the styling.
         *
         * Asserted as "no link to those three paths ANYWHERE on this page",
         * not "no element with that class": re-adding the block under a new
         * class name would be the same defect, and a class-name assertion
         * would sail past it.
         */
        meIs(admin)
        usersAre([userRow()])
        renderPage()
        await screen.findByRole("combobox", { name: "Role for noah" })

        const hrefs = screen.queryAllByRole("link").map((a) => a.getAttribute("href"))
        for (const path of ["/review-queue", "/admin/curator-queue", "/admin/machine-review-inspection"]) {
            expect(hrefs).not.toContain(path)
        }
    })
})
