import { createContext, useContext } from "react"
import type { MeResponse } from "../types/auth"

/**
 * App-wide session state. Split from `components/AuthProvider.tsx` for the
 * same reason `usePageSections.ts` is split from `PageSections.tsx`:
 * react-refresh's `only-export-components` rule objects to a `.tsx` file
 * mixing hook and component exports, since Fast Refresh cannot preserve
 * state across an edit to a file shaped like that.
 *
 * "loading" is the state between mount and the first `GET /auth/me`
 * resolving -- neither signed in nor confidently signed out yet, so a
 * caller that renders differently for the two must not collapse this
 * into "signed-out" (that would flash a "Sign in" link for a heartbeat on
 * every load, even for a returning visitor with a live cookie).
 */
export type AuthState =
    | { status: "loading" }
    | { status: "signed-out" }
    | { status: "signed-in"; user: MeResponse }
    //: The probe could not reach the archive, so whether this visitor has a
    //: session is unknown. Distinct from `signed-out`, which is a fact the
    //: server stated by answering 401.
    //:
    //: Collapsing the two was the original shape and it is wrong in a way
    //: that costs the user their session: during an API restart the probe
    //: gets a 502, a signed-in curator is told "Sign in", and `/account`
    //: bounces them to the login form while their cookie is still valid.
    //: `fetchMe` already separates 401 from a transport failure; this member
    //: is what lets the UI keep that distinction instead of discarding it.
    | { status: "unreachable" }

export type AuthContextValue = {
    state: AuthState
    login: (username: string, password: string) => Promise<MeResponse>
    register: (input: { username: string; password: string; email?: string; full_name?: string }) => Promise<MeResponse>
    logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * Throws outside `AuthProvider` rather than silently degrading -- unlike
 * `usePageSections`'s "no provider means no ToC entry" fallback, there is
 * no honest default session state to hand back (`signed-out`? `loading`
 * forever?) when nothing is actually tracking the session. `AuthProvider`
 * wraps the whole app in `App.tsx`, so every component that would
 * plausibly call this already renders beneath it.
 */
export function useAuth(): AuthContextValue {
    const value = useContext(AuthContext)
    if (value === null) throw new Error("useAuth must be used within an AuthProvider")
    return value
}
