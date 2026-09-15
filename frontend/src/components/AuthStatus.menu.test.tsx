import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { AuthStatus } from "./AuthStatus"
import { AuthProvider } from "./AuthProvider"

/**
 * The header's account menu: who is offered which page, and whether the
 * menu can actually be used without a mouse.
 *
 * ## Why the role gating is tested at all
 *
 * Record review is gated on `require_curator_or_admin`, not
 * `require_admin` -- so a **curator** is the role it exists for. When the
 * page was first built its only link lived inside `/admin`, which a
 * curator cannot open. The page was reachable by typing the address and
 * no other way: a surface built for a role that role could not find.
 * Caught by looking at the rendered header, not by any test, which is
 * why this file exists.
 *
 * This is navigation, not authorisation. Hiding an entry stops nobody;
 * the server gates every write, and each page refuses to render without
 * the role either. What these tests pin is that the menu OFFERS the
 * right doors, not that it guards them.
 *
 * ## Why the keyboard is tested at all
 *
 * A popup that only a pointer can open is not a smaller defect than a
 * missing link -- it is the same defect for a different reader. Escape,
 * click-outside, arrow entry and `aria-expanded` each get their own test
 * below because each is a separate thing to forget, and every one of
 * them passes silently when absent unless something asserts it.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function meIs(role: string, full_name: string | null = null) {
    server.use(
        http.get("/api/v1/auth/me", () =>
            HttpResponse.json({
                id: 1,
                username: "someone",
                email: "someone@example.com",
                full_name,
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
                {/* Something focusable AFTER the menu, so the "Tab leaves the
                    menu" behaviour has somewhere to land -- with nothing
                    after it, jsdom would wrap focus back to the body and the
                    test could not tell "closed and moved on" from "closed
                    and lost focus". */}
                <AuthStatus />
                <a href="/species">After the menu</a>
            </MemoryRouter>
        </AuthProvider>,
    )
}

/** The trigger, once the session probe has resolved. */
function trigger(name = "someone") {
    return screen.findByRole("button", { name })
}

/** Open with a pointer: focus stays on the trigger, as for a mouse user. */
async function openMenu(name = "someone") {
    const user = userEvent.setup()
    const button = await trigger(name)
    await user.click(button)
    return { user, button }
}

/**
 * Open the way a keyboard reader does: focus the trigger, press Enter.
 * Focus lands on the first item, which is the difference that matters --
 * see `AccountMenu`'s own note on `event.detail`.
 */
async function openMenuByKeyboard(name = "someone") {
    const user = userEvent.setup()
    const button = await trigger(name)
    button.focus()
    await user.keyboard("{Enter}")
    return { user, button }
}

describe("what the menu offers each role", () => {
    it("a curator is offered record review, and nothing admin-only", async () => {
        meIs("curator")
        renderStatus()
        await openMenu()

        expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveAttribute(
            "href",
            "/review-queue",
        )
        // The two are different gates: curator/admin for reviewing records,
        // admin for everything under /admin.
        for (const hidden of ["Machine findings", "Machine-review inspection", "Administration"]) {
            expect(screen.queryByRole("menuitem", { name: hidden })).not.toBeInTheDocument()
        }
    })

    it("an admin is offered all four pages, at their real paths", async () => {
        meIs("admin")
        renderStatus()
        await openMenu()

        const expected: [string, string][] = [
            ["Record review", "/review-queue"],
            ["Machine findings", "/admin/curator-queue"],
            ["Machine-review inspection", "/admin/machine-review-inspection"],
            ["Administration", "/admin"],
        ]
        for (const [label, href] of expected) {
            expect(screen.getByRole("menuitem", { name: label })).toHaveAttribute("href", href)
        }
    })

    it("a plain signed-in user gets account and sign-out only", async () => {
        meIs("user")
        renderStatus()
        await openMenu()

        // Asserted AFTER the menu is open, so this is an absence inside a
        // rendered menu rather than the absence of a menu.
        expect(screen.getByRole("menuitem", { name: "Account" })).toBeInTheDocument()
        expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeInTheDocument()
        for (const hidden of [
            "Record review",
            "Machine findings",
            "Machine-review inspection",
            "Administration",
        ]) {
            expect(screen.queryByRole("menuitem", { name: hidden })).not.toBeInTheDocument()
        }
    })

    it("every role reaches their own account and sign-out", async () => {
        meIs("admin")
        renderStatus()
        await openMenu()

        expect(screen.getByRole("menuitem", { name: "Account" })).toHaveAttribute("href", "/account")
        expect(screen.getByRole("menuitem", { name: "Sign out" }).tagName).toBe("BUTTON")
    })

    it("the trigger shows the full name when there is one", async () => {
        meIs("admin", "Ada Lovelace")
        renderStatus()

        expect(await trigger("Ada Lovelace")).toBeInTheDocument()
        // `username` is a login handle, not the identity -- it is the
        // fallback, never the label when a real name exists.
        expect(screen.queryByRole("button", { name: "someone" })).not.toBeInTheDocument()
    })
})

describe("the states where the header must show no menu at all", () => {
    it("a signed-out visitor gets a sign-in link and no trigger", async () => {
        server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
        renderStatus()

        await screen.findByRole("link", { name: "Sign in" })
        expect(screen.queryByRole("button")).not.toBeInTheDocument()
        expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    })

    it("an unreachable archive renders nothing, not a sign-in link", async () => {
        // A transport failure states nothing about this visitor's session.
        // Offering "Sign in" would assert they are signed out, which is
        // exactly the claim that cannot be made here.
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.error()))
        renderStatus()

        await expect(
            screen.findByRole("link", { name: "Sign in" }, { timeout: 300 }),
        ).rejects.toThrow()
        expect(screen.queryByRole("button")).not.toBeInTheDocument()
    })
})

describe("opening and closing", () => {
    it("aria-expanded follows the menu, in both directions", async () => {
        meIs("admin")
        renderStatus()

        const button = await trigger()
        expect(button).toHaveAttribute("aria-expanded", "false")
        expect(screen.queryByRole("menu")).not.toBeInTheDocument()

        const user = userEvent.setup()
        await user.click(button)
        expect(button).toHaveAttribute("aria-expanded", "true")
        expect(screen.getByRole("menu")).toBeInTheDocument()

        // Closing again matters as much as opening: a flag stuck on "true"
        // tells a screen reader a menu is open over a page that has none.
        await user.click(button)
        await waitFor(() => expect(button).toHaveAttribute("aria-expanded", "false"))
        expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    })

    it("Escape from INSIDE the menu closes it and puts focus back on the trigger", async () => {
        /**
         * Opened by keyboard on purpose, so focus is on a menu ITEM when
         * Escape is pressed. There are two Escape handlers -- one on the
         * trigger, one on the popup -- and a test that pressed Escape with
         * focus still on the trigger passed with the popup's handler
         * deleted. MEASURED: deleting it was a mutation that escaped, which
         * is what moved this test to the keyboard path.
         */
        meIs("admin")
        renderStatus()
        const { user, button } = await openMenuByKeyboard()
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveFocus(),
        )

        await user.keyboard("{Escape}")

        await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument())
        expect(button).toHaveAttribute("aria-expanded", "false")
        // Without the focus return a keyboard reader is left on
        // `document.body` and has to Tab from the top of the page again.
        expect(button).toHaveFocus()
    })

    it("Escape with focus still on the trigger closes it too", async () => {
        // The mouse-opened case: the popup is open but focus never left the
        // trigger, so the popup's own handler never sees the key. The
        // trigger needs its own, and this is the test that says so.
        meIs("admin")
        renderStatus()
        const { user, button } = await openMenu()
        expect(button).toHaveFocus()

        await user.keyboard("{Escape}")

        await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument())
        expect(button).toHaveAttribute("aria-expanded", "false")
    })

    it("a click outside closes it", async () => {
        meIs("admin")
        renderStatus()
        const { user, button } = await openMenu()

        await user.click(screen.getByRole("link", { name: "After the menu" }))

        await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument())
        expect(button).toHaveAttribute("aria-expanded", "false")
    })

    it("a click INSIDE it does not close it", async () => {
        // The other half of the click-outside rule. A handler that closed on
        // every document click would pass the test above while making the
        // menu unusable.
        meIs("admin")
        renderStatus()
        const { user } = await openMenu()

        await user.click(screen.getByRole("menu"))

        expect(screen.getByRole("menu")).toBeInTheDocument()
    })

    it("choosing a page closes the menu", async () => {
        meIs("admin")
        renderStatus()
        const { user } = await openMenu()

        await user.click(screen.getByRole("menuitem", { name: "Administration" }))

        await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument())
    })
})

describe("the menu works with a keyboard alone", () => {
    it("opening with a pointer leaves focus on the trigger, with no ring", async () => {
        // The counterpart to the Enter case below. Pulling focus onto the
        // first item after a mouse click paints a focus ring on a menu the
        // reader is about to click something in -- the ring is for people
        // who cannot see where the pointer is, and a pointer user can.
        meIs("admin")
        renderStatus()
        const { button } = await openMenu()

        expect(screen.getByRole("menu")).toBeInTheDocument()
        expect(button).toHaveFocus()
    })

    it("Tab reaches the trigger and Enter opens it, focused on the first item", async () => {
        meIs("admin")
        renderStatus()
        await trigger()

        const user = userEvent.setup()
        await user.tab()
        expect(await trigger()).toHaveFocus()

        await user.keyboard("{Enter}")

        expect(screen.getByRole("menu")).toBeInTheDocument()
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveFocus(),
        )
    })

    it("ArrowDown on the trigger opens on the first item, ArrowUp on the last", async () => {
        meIs("admin")
        renderStatus()
        const button = await trigger()
        const user = userEvent.setup()

        button.focus()
        await user.keyboard("{ArrowUp}")
        // Sign out is last, which is the point of the shortcut.
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Sign out" })).toHaveFocus(),
        )

        await user.keyboard("{Escape}")
        await user.keyboard("{ArrowDown}")
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveFocus(),
        )
    })

    it("ArrowDown walks the items in order and wraps", async () => {
        meIs("curator")
        renderStatus()
        const { user } = await openMenuByKeyboard()

        // A curator's menu is exactly three items: Record review, Account,
        // Sign out. Walking four steps proves both the order and the wrap.
        const order = ["Record review", "Account", "Sign out", "Record review"]
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveFocus(),
        )
        for (const name of order.slice(1)) {
            await user.keyboard("{ArrowDown}")
            expect(screen.getByRole("menuitem", { name })).toHaveFocus()
        }
    })

    it("Tab out of the menu closes it and moves on", async () => {
        meIs("curator")
        renderStatus()
        const { user } = await openMenuByKeyboard()
        await waitFor(() =>
            expect(screen.getByRole("menuitem", { name: "Record review" })).toHaveFocus(),
        )

        await user.tab()

        await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument())
        // Focus did not get stranded on the body.
        expect(document.activeElement).not.toBe(document.body)
    })
})
