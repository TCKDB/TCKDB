import { AuthApiError, throwForFailedResponse } from "./authApi"
import {
    AdminUserPageSchema,
    StorageCapacityStateSchema,
    UserRoleChangeSchema,
    type AdminUserPage,
    type StorageCapacityState,
    type UserRoleChange,
} from "../types/admin"
import type { AppUserRole } from "../types/auth"

/**
 * Client for `/api/v1/admin/*` (`backend/app/api/routes/admin.py`).
 *
 * Same session-cookie contract as `authApi.ts`: `credentials: "include"`
 * on every call, or the browser never attaches `tckdb_session` and an
 * admin looks anonymous. Every route here is gated on `require_admin`,
 * so a non-admin gets 403 and an unauthenticated caller 401 -- the page
 * checks the role before rendering, but the server is the one enforcing
 * it, and that order matters: the check in the UI is a courtesy, not a
 * control.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

/**
 * The 409 `PATCH /admin/users/{id}/role` answers with when the change
 * would leave the archive with no admin. Named here rather than matched
 * as a string at the call site, because this is the one refusal the page
 * has to explain differently from every other failure: it is not an
 * error the operator should retry, it is a precondition they have to
 * satisfy first (promote someone else).
 */
export const LAST_ADMIN_DEMOTION = "last_admin_demotion"

export async function listAdminUsers(options?: { role?: AppUserRole; limit?: number; offset?: number }): Promise<AdminUserPage> {
    const params = new URLSearchParams()
    if (options?.role) params.set("role", options.role)
    if (options?.limit !== undefined) params.set("limit", String(options.limit))
    if (options?.offset !== undefined) params.set("offset", String(options.offset))
    const query = params.toString()
    const response = await fetch(`${API_BASE}/api/v1/admin/users${query ? `?${query}` : ""}`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
    return AdminUserPageSchema.parse(await response.json())
}

export async function changeUserRole(userId: number, role: AppUserRole): Promise<UserRoleChange> {
    const response = await fetch(`${API_BASE}/api/v1/admin/users/${userId}/role`, {
        method: "PATCH",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
    })
    if (!response.ok) return throwForFailedResponse(response)
    return UserRoleChangeSchema.parse(await response.json())
}

/** True when `caught` is the archive refusing to remove the last admin. */
export function isLastAdminRefusal(caught: unknown): boolean {
    return caught instanceof AuthApiError && caught.code === LAST_ADMIN_DEMOTION
}

export async function getStorageCapacity(): Promise<StorageCapacityState> {
    const response = await fetch(`${API_BASE}/api/v1/admin/artifact-storage/capacity`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
    return StorageCapacityStateSchema.parse(await response.json())
}

/**
 * Declare a storage-full condition resolved.
 *
 * `reason` is required by the server (min length 1) and the requirement is
 * not incidental: this is the one clearing path that rests on an
 * operator's assertion rather than on a measurement, so the log has to
 * record who said what. The call APPENDS -- the original refusal stays in
 * the log as the account of what happened, and this supersedes it.
 *
 * If the store is in fact still full, the next refused upload appends a
 * new refusal and `/status` degrades again. That is the system working,
 * not a clear that was lost, and the page says so.
 */
export async function clearStorageCapacity(reason: string): Promise<StorageCapacityState> {
    const response = await fetch(`${API_BASE}/api/v1/admin/artifact-storage/capacity/clear`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
    })
    if (!response.ok) return throwForFailedResponse(response)
    return StorageCapacityStateSchema.parse(await response.json())
}
