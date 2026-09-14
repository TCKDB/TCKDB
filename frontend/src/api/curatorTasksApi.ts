import { throwForFailedResponse } from "./authApi"
import {
    CuratorTaskPageSchema,
    CuratorTaskSchema,
    type CuratorTask,
    type CuratorTaskPage,
    type CuratorTaskState,
} from "../types/curatorTask"

/**
 * Client for `/api/v1/admin/machine-review/curator-tasks`
 * (`backend/app/api/routes/admin.py`).
 *
 * Same session-cookie contract as the rest of the admin surface:
 * `credentials: "include"` on every call, or the browser never attaches
 * `tckdb_session` and an admin looks anonymous. Every route is gated on
 * `require_admin`; the page checks the role before rendering, but the
 * server enforces it, and that order matters.
 *
 * The four write calls each answer with the **whole updated task**, so a
 * caller replaces the row it holds rather than patching fields it guessed
 * at -- which is why none of them returns void.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/admin/machine-review/curator-tasks`

async function readJson(url: string): Promise<unknown> {
    const response = await fetch(url, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
    return response.json()
}

async function postJson(path: string, body: unknown): Promise<unknown> {
    const response = await fetch(`${BASE}${path}`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(body),
    })
    if (!response.ok) return throwForFailedResponse(response)
    return response.json()
}

export async function listCuratorTasks(options?: {
    workflowState?: CuratorTaskState
    limit?: number
    offset?: number
}): Promise<CuratorTaskPage> {
    const params = new URLSearchParams()
    if (options?.workflowState) params.set("workflow_state", options.workflowState)
    if (options?.limit !== undefined) params.set("limit", String(options.limit))
    if (options?.offset !== undefined) params.set("offset", String(options.offset))
    const query = params.toString()
    return CuratorTaskPageSchema.parse(await readJson(`${BASE}${query ? `?${query}` : ""}`))
}

/**
 * Move an open task into `in_curator_review`.
 *
 * The body is sent empty on purpose: the backend defaults the acting user
 * to the authenticated admin and auto-assigns an unassigned task. Naming
 * an `actor_user_id` here would let the page claim somebody else did the
 * work, which is exactly what an audit trail should not allow from a UI.
 */
export async function startCuratorTaskReview(taskId: number): Promise<CuratorTask> {
    return CuratorTaskSchema.parse(await postJson(`/${taskId}/start-review`, {}))
}

/**
 * Close a task into a terminal state.
 *
 * `resolutionNote` is required and non-empty by the backend (400 if
 * blank). That is the point of the field: a closed task without a reason
 * tells the next reader nothing about why the finding stopped mattering.
 */
export async function resolveCuratorTask(
    taskId: number,
    resolutionState: CuratorTaskState,
    resolutionNote: string,
): Promise<CuratorTask> {
    return CuratorTaskSchema.parse(
        await postJson(`/${taskId}/resolve`, {
            resolution_state: resolutionState,
            resolution_note: resolutionNote,
        }),
    )
}

/** Reopen a terminal task. Clears the resolution triple; keeps the assignee. */
export async function reopenCuratorTask(taskId: number): Promise<CuratorTask> {
    return CuratorTaskSchema.parse(await postJson(`/${taskId}/reopen`, {}))
}
