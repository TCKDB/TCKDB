import { artifactDownloadName } from "../domain/artifactDownloadName"

/**
 * `GET /api/v1/scientific/artifacts/{sha256}/download`.
 *
 * Raw artifact bytes are served only to authenticated callers, on every
 * deployment, with no opt-out: unredacted ESS logs can embed
 * producer-side scratch paths, usernames and cluster hostnames
 * (`docs/adr/0004-store-artifacts-verbatim-gate-raw-log-access.md`).
 * So `credentials: "include"` is load-bearing here, not boilerplate --
 * without it every download is a 401.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

/**
 * A download that did not happen, with a sentence saying what to do
 * about it. `retryable` is the distinction the archive itself draws and
 * a reader cannot: a 503 is the object store not answering, and trying
 * again is the right move; a 502 is a RECORDED break in custody of the
 * bytes, and trying again will produce the same answer forever.
 */
export class ArtifactDownloadError extends Error {
    readonly status: number
    readonly code?: string
    readonly retryable: boolean

    constructor(status: number, message: string, code: string | undefined, retryable: boolean) {
        super(message)
        this.name = "ArtifactDownloadError"
        this.status = status
        this.code = code
        this.retryable = retryable
    }
}

async function readCode(response: Response): Promise<string | undefined> {
    try {
        const body: unknown = await response.json()
        if (body && typeof body === "object" && "code" in body && typeof body.code === "string") {
            return body.code
        }
    } catch {
        // A non-JSON body on an error is itself unremarkable; the status
        // already carries the part this function needs.
    }
    return undefined
}

function messageFor(status: number, code: string | undefined): { message: string; retryable: boolean } {
    if (status === 401 || status === 403) {
        return { message: "Sign in to download stored files.", retryable: false }
    }
    if (status === 404) {
        // One reason now. Until 2026-09-14 a 404 here also covered "exists,
        // but not approved and not yours", so the message had to name both
        // and the reader could not tell which applied. Authentication is the
        // whole gate now, so a signed-in reader seeing this can take it
        // literally: the archive has no file with that digest.
        return { message: "No file with that digest is in the archive.", retryable: false }
    }
    if (status === 502) {
        return {
            message:
                code === "artifact_object_missing"
                    ? "The archive's record of this file no longer matches what the store holds: "
                      + "the object is missing. This is recorded; retrying will not change it."
                    : "The stored bytes failed verification against their recorded digest. "
                      + "This is a recorded break in custody; retrying will not change it.",
            retryable: false,
        }
    }
    if (status === 503) {
        return { message: "The file store did not answer. This is usually brief, so try again.", retryable: true }
    }
    return { message: `The download failed (${status}).`, retryable: false }
}

export interface DownloadedArtifact {
    blob: Blob
    filename: string
}

export async function fetchArtifact(input: {
    sha256: string
    artifactRef?: string | null
    filename?: string | null
}): Promise<DownloadedArtifact> {
    const response = await fetch(
        `${API_BASE}/api/v1/scientific/artifacts/${input.sha256}/download`,
        { method: "GET", credentials: "include" },
    )
    if (!response.ok) {
        const code = await readCode(response)
        const { message, retryable } = messageFor(response.status, code)
        throw new ArtifactDownloadError(response.status, message, code, retryable)
    }
    return {
        blob: await response.blob(),
        filename: artifactDownloadName(input.artifactRef, input.filename, input.sha256),
    }
}

/**
 * Hands the bytes to the browser's save flow.
 *
 * Split from `fetchArtifact` because it is the only part that touches the
 * DOM, and because the object URL has to be revoked: an un-revoked one
 * pins the whole blob in memory for the life of the document, which for
 * a page where someone downloads several multi-megabyte logs is a leak
 * with a visible cost.
 */
export function saveBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob)
    try {
        const anchor = document.createElement("a")
        anchor.href = url
        anchor.download = filename
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
    } finally {
        URL.revokeObjectURL(url)
    }
}
