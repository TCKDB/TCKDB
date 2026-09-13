import { useCallback, useEffect, useState } from "react"
import type { ReactNode } from "react"
import { fetchMe, login as loginRequest, logout as logoutRequest, register as registerRequest } from "../api/authApi"
import { AuthContext, type AuthState } from "../hooks/useAuth"

/**
 * Owns the app-wide session: seeds it from `GET /auth/me` on mount so a
 * returning visitor with a live session cookie is already signed in
 * before they click anything, and exposes `login`/`register`/`logout`
 * through `useAuth()` (`hooks/useAuth.ts`) to every component beneath it
 * -- `App.tsx` mounts exactly one, wrapping the whole router, the same
 * way `main.tsx`'s `QueryClientProvider` wraps `App` itself.
 *
 * A failed or absent session probe (401, or the request failing outright
 * -- offline, the backend down) resolves to "signed-out" with no error
 * surfaced: not being logged in is the ordinary state of a first-time or
 * logged-out visitor, not a failure this app should report on load. Only
 * `login`/`register` throw outward (`AuthApiError`, from `authApi.ts`) --
 * those are the two places a reader is actively trying to do something
 * and needs to know it did not work.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
    const [state, setState] = useState<AuthState>({ status: "loading" })

    useEffect(() => {
        let cancelled = false
        fetchMe()
            .then((user) => {
                if (cancelled) return
                setState(user === null ? { status: "signed-out" } : { status: "signed-in", user })
            })
            .catch(() => {
                if (cancelled) return
                setState({ status: "signed-out" })
            })
        return () => { cancelled = true }
    }, [])

    const login = useCallback(async (username: string, password: string) => {
        const user = await loginRequest({ username, password })
        setState({ status: "signed-in", user })
        return user
    }, [])

    const register = useCallback(async (input: { username: string; password: string; email?: string; full_name?: string }) => {
        const user = await registerRequest(input)
        setState({ status: "signed-in", user })
        return user
    }, [])

    // Always attempts the endpoint first (clearing the cookie is the
    // point of logging out), and always clears client state afterward
    // via `finally` -- including when the request itself fails, so a
    // reader who asked to sign out is never left looking signed in
    // because of a network hiccup on the way out.
    const logout = useCallback(async () => {
        try {
            await logoutRequest()
        } finally {
            setState({ status: "signed-out" })
        }
    }, [])

    return <AuthContext.Provider value={{ state, login, register, logout }}>{children}</AuthContext.Provider>
}
