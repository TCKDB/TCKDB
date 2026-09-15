import { throwForFailedResponse } from "./authApi"
import {
    MachineReviewRunResultSchema,
    type MachineReviewRunResult,
} from "../types/machineReviewRun"

/**
 * Client for `POST /api/v1/admin/machine-review/run-for-submission/{id}`
 * (`backend/app/api/routes/admin.py`).
 *
 * Same session-cookie contract as the rest of the admin surface:
 * `credentials: "include"` on every call, or the browser never attaches
 * `tckdb_session` and an admin looks anonymous. The route is gated on
 * `require_admin`; the server is the authority, and a UI that hides the
 * button changes nothing about that.
 *
 * No request body is sent, because the route takes none: the submission is
 * in the path and the acting admin comes from the session. Sending a body
 * would be the page inventing parameters the contract has not got.
 *
 * ## A failed review is a 200, not an error
 *
 * `status: "machine_review_failed"` comes back with a 200. Machine review
 * is advisory, so a reviewer that failed is a RECORDED OUTCOME, not a
 * broken request, and this function returns it like any other result. Only
 * the request itself failing reaches a caller as a throw.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/admin/machine-review/run-for-submission`

/**
 * The run reached the server and happened; the reply could not be read.
 *
 * The same discipline as `CuratorTaskResponseError` in `curatorTasksApi.ts`,
 * and it exists for the same reason: the UI must never tell an admin that
 * nothing happened about something that did. The POST returned 2xx, so a
 * review ran and its findings and audit event are already written; only the
 * *response body* failed to validate, which is what happens the first time
 * the backend adds a field shape this build predates.
 *
 * Saying "it did not run" there would be a lie, and the admin's natural
 * next move -- press it again -- then runs a second real review, at a real
 * provider's cost, over work that already succeeded.
 */
export class MachineReviewRunResponseError extends Error {
    readonly action = "run-for-submission"

    constructor(
        outcome = "The review ran, but this page could not read its result. " +
            "Inspect this submission again to see what it recorded.",
    ) {
        super(outcome)
        this.name = "MachineReviewRunResponseError"
    }
}

/**
 * Run machine review for one submission and return what it recorded.
 *
 * @throws AuthApiError when the server refused (401/403 not an admin, 404 no
 *         such submission). A refusal wrote nothing.
 * @throws MachineReviewRunResponseError when a 2xx body would not parse. It
 *         DID run.
 * @throws whatever `fetch` rejects with when no answer arrived at all, which
 *         leaves the caller genuinely not knowing.
 */
export async function runMachineReviewForSubmission(
    submissionId: number,
): Promise<MachineReviewRunResult> {
    const response = await fetch(`${BASE}/${submissionId}`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)

    // A 2xx that is not JSON at all lands here, not in the caller's
    // "no answer" branch. Both are exceptions out of this function, but they
    // mean opposite things to an admin deciding whether to press the button
    // again, so the unreadable-2xx case is classified here rather than left
    // to surface as a bare SyntaxError.
    let payload: unknown
    try {
        payload = await response.json()
    } catch {
        throw new MachineReviewRunResponseError()
    }

    const parsed = MachineReviewRunResultSchema.safeParse(payload)
    if (!parsed.success) throw new MachineReviewRunResponseError()
    return parsed.data
}
