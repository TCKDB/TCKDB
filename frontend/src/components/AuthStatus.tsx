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

    if (state.status === "signed-out") {
        return <Link className="auth-status-link" to="/login">Sign in</Link>
    }

    const displayName = state.user.full_name || state.user.username
    return (
        <div className="auth-status">
            <Link className="auth-status-link" to="/account">{displayName}</Link>
            <button type="button" className="auth-status-signout" onClick={() => { void logout() }}>
                Sign out
            </button>
        </div>
    )
}
