import { useCallback, useEffect, useId, useRef, useState } from "react"
import type { KeyboardEvent as ReactKeyboardEvent } from "react"
import { Link } from "react-router-dom"
import { useAuth } from "../hooks/useAuth"
import type { AppUserRole } from "../types/auth"

/**
 * The header's sign-in/account affordance -- rendered in `AppShell.tsx`
 * next to `ThemeToggle`, the one place every route shares. Renders
 * nothing while the initial `GET /auth/me` probe is still in flight
 * (`AuthProvider`'s "loading" state), so a returning visitor with a live
 * session never sees a "Sign in" link flash before it resolves to their
 * name.
 *
 * Display name is `full_name || username`, never `username` alone --
 * `username` is a login handle, not the identity (see `types/auth.ts`'s
 * own docstring on why: ORCID linking is the intended second login
 * method, and an account may end up reachable by more than one).
 *
 * ## Why one menu and not a row of links
 *
 * This used to render a flat row -- Review, Admin, the name, Sign out --
 * one more item per role-gated page. That row was already four items
 * wide with four pages to reach, it pushed the primary navigation
 * leftwards on every route, and every new curator or admin surface made
 * it wider. The owner asked for a single control instead ("I don't like
 * we are adding Admin and Review to the top bar. Can we not make it a
 * burger menu instead?").
 *
 * So: one trigger showing the name, and one popup holding everything.
 * Adding a fifth admin page now costs a line in `pagesFor` and no header
 * width at all.
 */
export function AuthStatus() {
    const { state, logout } = useAuth()

    if (state.status === "loading") return null
    // Render nothing rather than "Sign in": offering a sign-in link asserts
    // the visitor is signed out, and on a transport failure that is not
    // known. Silence is the only honest option in the header.
    if (state.status === "unreachable") return null

    if (state.status === "signed-out") {
        return <Link className="auth-status-link" to="/login">Sign in</Link>
    }

    return (
        <AccountMenu
            displayName={state.user.full_name || state.user.username}
            role={state.user.role}
            onSignOut={() => {
                // `logout` rethrows so a caller that wants the failure can
                // have it. This one does not: the provider already records
                // it in `signOutIncomplete`, which the login page renders.
                // `void logout()` alone left the rejection unhandled, which
                // printed the server's error to the console and failed the
                // test run as an unhandled rejection. Raised in review of
                // #467.
                void logout().catch(() => {})
            }}
        />
    )
}

/** One navigable entry in the menu's upper group. */
type MenuPage = { label: string; to: string }

/**
 * The role-gated pages, in menu order.
 *
 * This is navigation, NOT the security control -- exactly as the flat row
 * it replaces was not. Every route named here is gated server-side
 * (`require_curator_or_admin` for record review, `require_admin` for the
 * three admin surfaces), and each page refuses to render its contents
 * without the role as well. What hiding an entry buys is that a reader is
 * not offered a door that will shut in their face; what it does not buy
 * is any protection whatsoever. Nothing here should ever become the only
 * check.
 *
 * Record review is offered to curators as well as admins because
 * `PATCH /record-reviews` is gated on `require_curator_or_admin`: it is a
 * curator's page. Before the header offered it at all, its only link
 * lived inside `/admin` -- which a curator cannot open -- so the address
 * bar was that role's entire navigation. That is the failure this entry
 * exists to fix.
 */
function pagesFor(role: AppUserRole): MenuPage[] {
    const pages: MenuPage[] = []
    if (role === "curator" || role === "admin") {
        pages.push({ label: "Record review", to: "/review-queue" })
    }
    if (role === "admin") {
        pages.push({ label: "Machine findings", to: "/admin/curator-queue" })
        pages.push({
            label: "Machine-review inspection",
            to: "/admin/machine-review-inspection",
        })
        pages.push({ label: "Administration", to: "/admin" })
    }
    return pages
}

/**
 * The signed-in header control: a button showing the reader's name, and a
 * popup holding every page their role can reach plus their own account
 * and sign-out.
 *
 * ## The ARIA shape, and why this one
 *
 * WAI-ARIA's menu-button pattern: `aria-haspopup="menu"` +
 * `aria-expanded` on a real `<button>`, a `role="menu"` container, and
 * `role="menuitem"` children with roving `tabIndex`. The popup's contents
 * are `<Link>`s and one `<button>`, so the underlying elements stay
 * genuinely navigable (middle-click, "open in new tab", a crawler
 * following hrefs all still work) while assistive technology is told the
 * truth about the composite: one control, one list, one thing at a time.
 *
 * Roving focus rather than every item being a tab stop, because that is
 * what `role="menu"` promises a screen-reader user: Tab leaves the whole
 * menu, arrows move within it. Making all seven items tab stops would
 * announce a menu and then behave like a toolbar.
 *
 * ## Keyboard, in full
 *
 *   - Enter/Space/click on the trigger opens and focuses the first item,
 *     so the menu is usable without ever touching a pointer. ArrowDown
 *     does the same; ArrowUp opens on the LAST item, the conventional
 *     shortcut to "Sign out" at the bottom.
 *   - ArrowDown/ArrowUp move and wrap; Home/End jump to the ends.
 *   - Escape closes and returns focus to the trigger -- without that
 *     last part a keyboard reader who dismisses the menu is left with
 *     focus on `document.body` and has to Tab from the top of the page
 *     again.
 *   - Tab closes the menu and lets focus move on normally (no
 *     `preventDefault`), which is the pattern's own answer to "what if I
 *     just want to leave".
 *
 * A pointer user gets the two things they expect and nothing else has to
 * supply: clicking outside closes, and clicking an entry navigates and
 * closes.
 */
function AccountMenu({
    displayName,
    role,
    onSignOut,
}: {
    displayName: string
    role: AppUserRole
    onSignOut: () => void
}) {
    const [open, setOpen] = useState(false)
    const triggerRef = useRef<HTMLButtonElement>(null)
    const menuRef = useRef<HTMLDivElement>(null)
    const wrapRef = useRef<HTMLDivElement>(null)
    // Which item to focus once the popup has actually mounted. Focus cannot
    // be moved in the same tick as `setOpen(true)`, because the items do not
    // exist in the DOM yet -- so the intent is parked here and the effect
    // below acts on it on the next commit.
    //
    // A ref and not state: this value is never rendered, and clearing it
    // from inside the effect that consumes it would be a `setState` in an
    // effect body (the `react-hooks/set-state-in-effect` lint rule catches
    // exactly that, and it is right to -- it would schedule a second render
    // for a value nothing displays).
    const focusOnOpen = useRef<"first" | "last" | null>(null)
    const menuId = useId()
    const triggerId = useId()

    const pages = pagesFor(role)

    /** Every focusable item in the popup, in the order they render. */
    const items = useCallback(
        () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
        [],
    )

    const close = useCallback((returnFocus: boolean) => {
        setOpen(false)
        focusOnOpen.current = null
        if (returnFocus) triggerRef.current?.focus()
    }, [])

    useEffect(() => {
        if (!open || focusOnOpen.current === null) return
        const all = items()
        const target = focusOnOpen.current === "first" ? all[0] : all[all.length - 1]
        focusOnOpen.current = null
        target?.focus()
    }, [open, items])

    /**
     * Open the menu with focus already on one end of it.
     *
     * When it is ALREADY open the focus is moved directly -- the effect
     * above only fires on the transition, so parking an intent here would
     * do nothing. That case is reachable: Shift+Tab from the first item
     * lands back on the trigger with the menu still open.
     */
    function openWith(which: "first" | "last") {
        if (open) {
            const all = items()
            const target = which === "first" ? all[0] : all[all.length - 1]
            target?.focus()
            return
        }
        focusOnOpen.current = which
        setOpen(true)
    }

    // Close on a click anywhere outside the control. `pointerdown` rather
    // than `click`: a `click` listener fires after the menu item's own
    // handler has already navigated, and on a re-render the target may no
    // longer be inside `wrapRef` -- which read as "outside" and closed the
    // menu in a second, redundant pass. Attached only while open, so the
    // common case (nobody has opened anything) costs no document listener.
    useEffect(() => {
        if (!open) return
        function onPointerDown(event: PointerEvent | MouseEvent) {
            const target = event.target
            if (target instanceof Node && wrapRef.current?.contains(target)) return
            // No focus return here: the reader chose to click elsewhere, and
            // yanking focus back to the trigger would fight whatever they
            // just clicked on.
            close(false)
        }
        document.addEventListener("pointerdown", onPointerDown)
        // jsdom's `userEvent` dispatches `pointerdown`, but a plain
        // `fireEvent.mouseDown` (and some older browsers) only ever sends
        // `mousedown`. Listening for both costs nothing: a real pointer
        // sends both, and the handler is idempotent.
        document.addEventListener("mousedown", onPointerDown)
        return () => {
            document.removeEventListener("pointerdown", onPointerDown)
            document.removeEventListener("mousedown", onPointerDown)
        }
    }, [open, close])

    /** Move focus by `step` items, wrapping at both ends. */
    function moveFocus(step: 1 | -1) {
        const all = items()
        if (all.length === 0) return
        const at = all.indexOf(document.activeElement as HTMLElement)
        const next = at === -1 ? (step === 1 ? 0 : all.length - 1) : (at + step + all.length) % all.length
        all[next]?.focus()
    }

    function onTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
        if (event.key === "ArrowDown") {
            event.preventDefault()
            openWith("first")
        } else if (event.key === "ArrowUp") {
            event.preventDefault()
            openWith("last")
        } else if (event.key === "Escape" && open) {
            event.preventDefault()
            close(true)
        }
    }

    function onMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
        switch (event.key) {
            case "Escape":
                event.preventDefault()
                close(true)
                break
            case "ArrowDown":
                event.preventDefault()
                moveFocus(1)
                break
            case "ArrowUp":
                event.preventDefault()
                moveFocus(-1)
                break
            case "Home":
                event.preventDefault()
                items()[0]?.focus()
                break
            case "End": {
                event.preventDefault()
                const all = items()
                all[all.length - 1]?.focus()
                break
            }
            case "Tab":
                // Deliberately NOT prevented: Tab means "I am done here".
                //
                // Deferred to the next frame, and that is the whole fix.
                // Closing synchronously unmounts the focused menu item
                // BEFORE the browser performs the default Tab, which leaves
                // it with no element to move from -- so it restarts at the
                // top of the document. MEASURED in real Chrome: Tab from the
                // first item landed on "Skip to content", i.e. a keyboard
                // user was thrown to the top of the page by tabbing out of a
                // menu. That is worse than the plain links this replaced.
                //
                // Deferring keeps the item mounted for the default, so focus
                // lands on the next tabbable element after the menu, and the
                // close happens once it has.
                //
                // jsdom does not implement Tab's default at all, so no jsdom
                // test can see this either way -- the existing one asserted
                // only "focus is not on body", which was true in both worlds.
                // The test for this is a real-browser one.
                requestAnimationFrame(() => close(false))
                break
        }
    }

    return (
        <div className="account-menu" ref={wrapRef}>
            <button
                type="button"
                id={triggerId}
                ref={triggerRef}
                className="account-menu-trigger"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onKeyDown={onTriggerKeyDown}
                onClick={(event) => {
                    if (open) {
                        close(false)
                        return
                    }
                    // `detail` is the click count, and it is 0 only when the
                    // browser synthesised the click from Enter or Space on a
                    // focused button -- a real pointer always reports at
                    // least 1. That is the one reliable way to tell the two
                    // apart in a single `onClick`, and the difference
                    // matters: a keyboard reader who activates the trigger
                    // must land INSIDE the menu (otherwise the menu opens
                    // behind them and their next Tab leaves it), while a
                    // mouse user must not, because moving focus to the first
                    // item paints a focus ring nobody asked for on a menu
                    // they are about to click something in.
                    if (event.detail === 0) openWith("first")
                    else setOpen(true)
                }}
            >
                {/* Three bars, drawn in CSS rather than shipped as an icon
                    font or an inline SVG: this app has neither, and one
                    decorative glyph is not the reason to start. `aria-hidden`
                    because the button's accessible name is the reader's own
                    name, which says far more than "menu" would. */}
                <span aria-hidden="true" className="account-menu-bars" />
                {/* The name is its own element so it can be truncated: a
                    `full_name` is free text and a long one would otherwise
                    push the whole header sideways. */}
                <span className="account-menu-name">{displayName}</span>
            </button>

            {open && (
                <div
                    id={menuId}
                    ref={menuRef}
                    role="menu"
                    aria-labelledby={triggerId}
                    className="account-menu-popup"
                    onKeyDown={onMenuKeyDown}
                >
                    {pages.length > 0 && (
                        <div className="account-menu-group">
                            {pages.map((page) => (
                                <Link
                                    key={page.to}
                                    to={page.to}
                                    role="menuitem"
                                    tabIndex={-1}
                                    className="account-menu-item"
                                    onClick={() => close(false)}
                                >
                                    {page.label}
                                </Link>
                            ))}
                        </div>
                    )}
                    {/* Account and Sign out are a separate group with a rule
                        above them: the entries above are pages about the
                        archive's records, these two are about the reader's
                        own session. A curator scanning for "Record review"
                        should not have to read past "Sign out" to find it. */}
                    <div className="account-menu-group">
                        <Link
                            to="/account"
                            role="menuitem"
                            tabIndex={-1}
                            className="account-menu-item"
                            onClick={() => close(false)}
                        >
                            Account
                        </Link>
                        <button
                            type="button"
                            role="menuitem"
                            tabIndex={-1}
                            className="account-menu-item account-menu-signout"
                            onClick={() => {
                                // Closed first: the trigger this menu hangs
                                // off is about to be replaced by the "Sign
                                // in" link, and a popup left open around a
                                // vanished trigger is a stranded dialog.
                                close(false)
                                onSignOut()
                            }}
                        >
                            Sign out
                        </button>
                    </div>
                </div>
            )}
        </div>
    )
}
