import { throwForFailedResponse } from "./authApi"
import {
    CuratorTaskBuildResultSchema,
    CuratorTaskEnvelopeSchema,
    CuratorTaskSchema,
    type CuratorTask,
    type CuratorTaskBuildResult,
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
 * The four single-task writes each answer with the **whole updated task**,
 * so a caller replaces the row it holds rather than patching fields it
 * guessed at -- which is why none of them returns void. The fifth write,
 * `buildCuratorTasksForSubmission`, is the odd one out: it acts on a whole
 * submission and answers with a tally, not a row.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/admin/machine-review/curator-tasks`

/**
 * The write reached the server and was applied; the reply could not be read.
 *
 * This exists so the UI never tells a curator "nothing was changed" about a
 * change that was in fact committed. The POST returned 2xx -- the database
 * row has moved -- and only the *response body* failed to validate, which
 * is what happens the first time the backend returns an enum member this
 * build predates. Saying "that did not go through" there would be a lie,
 * and the curator's natural next move (do it again) can then fail with a
 * state conflict about work that already succeeded.
 */
export class CuratorTaskResponseError extends Error {
    readonly action: string

    constructor(
        action: string,
        /**
         * What in fact happened, and how to go and see it. Defaults to the
         * wording for the four single-task writes; the build passes its own,
         * because "reload to see the task's current state" names a thing a
         * build has not got -- it may have written many tasks or none, and
         * the place to look is the queue, not this page.
         */
        outcome = "The change was saved, but this page could not read the reply. " +
            "Reload to see the task's current state.",
    ) {
        super(outcome)
        this.name = "CuratorTaskResponseError"
        this.action = action
    }
}

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

/** Parse a write's reply, distinguishing "refused" from "applied but unreadable". */
function parseWriteResult(payload: unknown, action: string): CuratorTask {
    const parsed = CuratorTaskSchema.safeParse(payload)
    if (!parsed.success) throw new CuratorTaskResponseError(action)
    return parsed.data
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
    const envelope = CuratorTaskEnvelopeSchema.parse(
        await readJson(`${BASE}${query ? `?${query}` : ""}`),
    )

    // Row by row, so one task this build cannot read costs that task and
    // not the queue. A queue that renders nothing at all is the worst
    // possible answer to "what work is outstanding".
    const items: CuratorTask[] = []
    let unreadable = 0
    for (const row of envelope.items) {
        const parsed = CuratorTaskSchema.safeParse(row)
        if (parsed.success) items.push(parsed.data)
        else unreadable += 1
    }
    return {
        items,
        total: envelope.total,
        skip: envelope.skip,
        limit: envelope.limit,
        unreadable,
    }
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
    return parseWriteResult(await postJson(`/${taskId}/start-review`, {}), "start-review")
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
    return parseWriteResult(
        await postJson(`/${taskId}/resolve`, {
            resolution_state: resolutionState,
            resolution_note: resolutionNote,
        }),
        "resolve",
    )
}

/** Reopen a terminal task. Clears the resolution triple; keeps the assignee. */
export async function reopenCuratorTask(taskId: number): Promise<CuratorTask> {
    return parseWriteResult(await postJson(`/${taskId}/reopen`, {}), "reopen")
}

/**
 * Build curator tasks from one submission's machine-review findings.
 *
 * This is the ONLY way a curator task comes into existence. Nothing runs on
 * upload (the route's own docstring says so), so before this call the
 * archive's machine-review findings sit in audit events and the curator
 * queue stays empty however many submissions arrive -- which is what
 * task #256 is about.
 *
 * Upserting, not appending: a finding that already has an open task reuses
 * it rather than raising a second one, and a finding whose task was closed
 * stays closed. Pressing the button twice is therefore safe, and the second
 * press honestly reports zero created.
 *
 * The reply is a tally, and `CuratorTaskBuildResultSchema` drops the
 * `task_ids` the server sends beside it -- see the note there.
 */
export async function buildCuratorTasksForSubmission(
    submissionId: number,
): Promise<CuratorTaskBuildResult> {
    const parsed = CuratorTaskBuildResultSchema.safeParse(
        await postJson(`/build-for-submission/${submissionId}`, {}),
    )
    if (!parsed.success) {
        throw new CuratorTaskResponseError(
            "build-for-submission",
            "The build ran, but this page could not read the tally. " +
                "Any tasks it made are in the curator queue; open it to see them.",
        )
    }
    return parsed.data
}
