import {
    AdminSubmissionMachineReviewInspectionSchema,
    type AdminSubmissionMachineReviewInspection,
} from "../types/machineReviewInspection"

/**
 * API client for the admin-only machine-review inspection endpoint.
 *
 * Auth: this is an ADMIN-ONLY endpoint (``require_admin`` on the backend).
 * Human admins authenticate with the session cookie set by ``POST /auth/login``
 * (see ``backend/app/api/deps.py``), so requests are sent with
 * ``credentials: "include"``. The backend is the access-control authority: a
 * non-admin or anonymous caller gets 403/401 regardless of what the UI shows.
 */

/**
 * Base URL for the backend API. Same-origin by default (the admin shell is
 * expected to serve the SPA and proxy ``/api`` to the backend); override with
 * ``VITE_API_BASE_URL`` for split dev hosts. Trailing slash trimmed.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

export class InspectionFetchError extends Error {
    readonly status: number
    constructor(status: number, message: string) {
        super(message)
        this.name = "InspectionFetchError"
        this.status = status
    }
}

/**
 * Fetch the machine-review inspection projection for one submission.
 *
 * @param submissionId numeric submission id
 * @returns the validated inspection response
 * @throws InspectionFetchError on a non-2xx response (401/403 = not admin,
 *         404 = submission not found, etc.)
 */
export async function fetchMachineReviewInspection(
    submissionId: number,
): Promise<AdminSubmissionMachineReviewInspection> {
    const url = `${API_BASE}/api/v1/admin/submissions/${submissionId}/machine-review-inspection`

    const response = await fetch(url, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })

    if (!response.ok) {
        let detail = response.statusText
        try {
            const body = await response.json()
            if (body && typeof body.detail === "string") {
                detail = body.detail
            }
        } catch {
            // non-JSON error body; keep the status text
        }
        throw new InspectionFetchError(response.status, detail)
    }

    // Validate the payload against the backend contract so malformed/changed
    // responses fail loudly rather than rendering blank cells.
    return AdminSubmissionMachineReviewInspectionSchema.parse(await response.json())
}
