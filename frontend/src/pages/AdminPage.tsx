import { useCallback, useEffect, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { AuthApiError } from "../api/authApi"
import { changeUserRole, isLastAdminRefusal, listAdminUsers } from "../api/adminApi"
import { StorageCapacityPanel } from "../components/StorageCapacityPanel"
import { useAuth } from "../hooks/useAuth"
import type { AdminUser } from "../types/admin"
import { AppUserRoleSchema, type AppUserRole } from "../types/auth"

const ROLES: readonly AppUserRole[] = AppUserRoleSchema.options

const isoDate = (value: string) => value.slice(0, 10)

type UsersState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; users: AdminUser[]; total: number }

/**
 * `/admin` -- account and role management for admins.
 *
 * This page exists because `PATCH /admin/users/{id}/role` shipped keyed
 * on a numeric user id that nothing disclosed, so changing a role meant
 * reading ids out of the database by hand. `GET /admin/users` closed
 * that; this is the surface that uses it.
 *
 * Scope is deliberately accounts and roles only. Submissions and the
 * reviewer queue are their own surface (task #222) and sharing a shell
 * with them is a decision to make when that one is built, not a thing to
 * assume now.
 *
 * The role check below is a courtesy, not a control. Every route this
 * page calls is gated on `require_admin` server-side; hiding the page
 * from a non-admin saves them a confusing 403, but the server is what
 * actually refuses. Nothing here should ever become the only check.
 */
export default function AdminPage() {
    const { state } = useAuth()
    const [usersState, setUsersState] = useState<UsersState>({ status: "loading" })
    const [pendingId, setPendingId] = useState<number | null>(null)
    const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null)

    const signedInUserId = state.status === "signed-in" ? state.user.id : null
    const isAdmin = state.status === "signed-in" && state.user.role === "admin"

    const loadUsers = useCallback(async () => {
        setUsersState({ status: "loading" })
        try {
            const page = await listAdminUsers({ limit: 200 })
            setUsersState({ status: "ready", users: page.items, total: page.total })
        } catch (caught) {
            setUsersState({
                status: "error",
                message: caught instanceof AuthApiError ? caught.message : "Could not load accounts.",
            })
        }
    }, [])

    useEffect(() => {
        if (isAdmin) void loadUsers()
    }, [isAdmin, loadUsers])

    if (state.status === "loading") return null
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Administration</h1>
                <p role="alert">
                    Could not reach the archive, so your sign-in state is unknown. Reload once it is back.
                </p>
            </section>
        )
    }
    if (state.status === "signed-out") return <Navigate to="/login" replace state={{ from: "/admin" }} />

    if (!isAdmin) {
        // Not a redirect and not a 404-alike. This visitor is signed in and
        // the page exists; they simply do not hold the role. Saying so is
        // more useful than pretending the address is wrong, and it does not
        // disclose anything -- they already know their own role.
        return (
            <section className="admin-page">
                <h1>Administration</h1>
                <p role="alert">This area is for administrators. Your account does not hold that role.</p>
            </section>
        )
    }

    async function handleRoleChange(user: AdminUser, role: AppUserRole) {
        if (role === user.role) return
        setRowError(null)
        setPendingId(user.id)
        try {
            const changed = await changeUserRole(user.id, role)
            setUsersState((previous) => (
                previous.status === "ready"
                    ? {
                        ...previous,
                        users: previous.users.map((row) => (row.id === changed.id ? { ...row, role: changed.role } : row)),
                    }
                    : previous
            ))
        } catch (caught) {
            setRowError({
                id: user.id,
                message: isLastAdminRefusal(caught)
                    // The archive's own sentence already says what to do; it is
                    // written for exactly this reader. Restating it here would
                    // be a second copy to keep in step.
                    ? (caught as AuthApiError).message
                    : caught instanceof AuthApiError
                        ? caught.message
                        : "Could not change that role.",
            })
        } finally {
            setPendingId(null)
        }
    }

    return (
        <section className="admin-page">
            <h1>Administration</h1>
            <p className="admin-lede">
                Accounts and the role each one holds. A role takes effect on the
                account&rsquo;s next request; it does not end a session already in
                progress.
            </p>

            <nav className="admin-nav" aria-label="Administration sections">
                <Link to="/review-queue">Review queue</Link>
                <Link to="/admin/curator-queue">Curator queue</Link>
                <Link to="/admin/machine-review-inspection">
                    Machine-review inspection
                </Link>
            </nav>

            <StorageCapacityPanel />

            <h2>Accounts</h2>
            {usersState.status === "loading" && <p>Loading accounts&hellip;</p>}
            {usersState.status === "error" && <p className="auth-error" role="alert">{usersState.message}</p>}

            {usersState.status === "ready" && (
                <>
                    <p className="admin-count" role="status">
                        {usersState.total} {usersState.total === 1 ? "account" : "accounts"}
                    </p>
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Accounts and roles">
                            <thead>
                                <tr>
                                    <th scope="col">Username</th>
                                    <th scope="col">Name</th>
                                    <th scope="col">Affiliation</th>
                                    <th scope="col">Joined</th>
                                    <th scope="col">Role</th>
                                </tr>
                            </thead>
                            <tbody>
                                {usersState.users.map((user) => (
                                    <tr key={user.id}>
                                        <th scope="row">
                                            {user.username}
                                            {user.id === signedInUserId && <span className="admin-you"> (you)</span>}
                                            {!user.is_active && <span className="admin-inactive"> deactivated</span>}
                                        </th>
                                        <td>{user.full_name ?? <span className="admin-absent">not given</span>}</td>
                                        <td>{user.affiliation ?? <span className="admin-absent">not given</span>}</td>
                                        <td>{isoDate(user.created_at)}</td>
                                        <td>
                                            {/* `aria-label` rather than a visually-hidden
                                                <label>: this app has no global
                                                visually-hidden utility, and inventing one
                                                for a single control is more surface than
                                                the accessible name needs. The column
                                                header alone is not enough -- every row's
                                                select would announce as "Role". */}
                                            <select
                                                aria-label={`Role for ${user.username}`}
                                                className="admin-role-select"
                                                value={user.role}
                                                disabled={pendingId === user.id}
                                                onChange={(event) => {
                                                    void handleRoleChange(user, AppUserRoleSchema.parse(event.target.value))
                                                }}
                                            >
                                                {ROLES.map((role) => (
                                                    <option key={role} value={role}>{role}</option>
                                                ))}
                                            </select>
                                            {rowError?.id === user.id && (
                                                <p className="auth-error admin-row-error" role="alert">{rowError.message}</p>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {usersState.users.length < usersState.total && (
                        <p className="admin-truncated" role="status">
                            Showing {usersState.users.length} of {usersState.total}. Paging is not built yet.
                        </p>
                    )}
                </>
            )}
        </section>
    )
}
