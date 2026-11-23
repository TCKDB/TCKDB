import { useContext } from "react"
import { AuthContext } from "./useAuth"

/**
 * Whether the viewer is a signed-in curator or admin. Unlike `useAuth`, it does not throw outside an `AuthProvider`: a
 * page without one has no session to speak of, so the answer is false. False too while the session is still loading.
 */
export function useIsCurator(): boolean {
    const value = useContext(AuthContext)
    if (value === null || value.state.status !== "signed-in") return false
    return value.state.user.role === "curator" || value.state.user.role === "admin"
}
