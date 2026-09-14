import { Link } from "react-router-dom"
import { useAuth } from "../hooks/useAuth"

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

    const displayName = state.user.full_name || state.user.username
    return (
        <div className="auth-status">
            {/* Shown to curators as well as admins, because the review
                queue is theirs: `PATCH /record-reviews` is gated on
                `require_curator_or_admin`, not `require_admin`.

                Without this a curator had no way to reach the one page
                built for them -- the only link lived inside `/admin`,
                which they cannot open, so the address bar was the entire
                navigation. That is the failure this link exists to fix,
                not a security control: the server gates every write. */}
            {(state.user.role === "curator" || state.user.role === "admin") && (
                <Link className="auth-status-link" to="/review-queue">Review</Link>
            )}
            {/* Shown only to admins. This hides a page they could otherwise
                only find by typing the address; it is not what stops anyone
                else using it -- every route `/admin` calls is gated on
                `require_admin` server-side, and that is the control. */}
            {state.user.role === "admin" && (
                <Link className="auth-status-link" to="/admin">Admin</Link>
            )}
            <Link className="auth-status-link" to="/account">{displayName}</Link>
            <button type="button" className="auth-status-signout" onClick={() => {
                    // `logout` rethrows so a caller that wants the failure can
                    // have it. This one does not: the provider already records
                    // it in `signOutIncomplete`, which the login page renders.
                    // `void logout()` alone left the rejection unhandled, which
                    // printed the server's error to the console and failed the
                    // test run as an unhandled rejection. Raised in review of
                    // #467.
                    void logout().catch(() => {})
                }}>
                Sign out
            </button>
        </div>
    )
}
