import {
    ApiKeyCreateResponseSchema,
    ApiKeyMetadataSchema,
    MeResponseSchema,
    type ApiKeyCreateResponse,
    type ApiKeyMetadata,
    type MeResponse,
} from "../types/auth"

/**
 * Client for `/api/v1/auth/*` (`backend/app/api/routes/auth.py`).
 *
 * Session-cookie based: every request below sends `credentials:
 * "include"` so the browser attaches/receives the `tckdb_session` cookie
 * the backend sets on `POST /login` and `POST /register` and clears on
 * `POST /logout`. Dropping that option on any of these is not a style
 * nit -- without it the cookie never round-trips and every subsequent
 * `/auth/me` looks signed-out regardless of what `/login` just returned.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

/**
 * Thrown for any non-2xx response from an auth endpoint.
 *
 * `code` is the archive's own error-envelope `code` field
 * (`backend/app/api/error_contract.py`) when the response carried one --
 * e.g. `username_taken`, `email_taken` -- so a caller can distinguish
 * *which* field a 409 is about rather than pattern-matching English.
 * `message` is the human-readable sentence to show: the envelope's
 * `detail`, with a `"<code>: "` prefix stripped when the code and the
 * prefix agree (the backend's legacy `"code: message"` convention --
 * see `error_contract.py`'s `detail_code`), so a reader sees "That
 * username is already in use." rather than the wire-format
 * "username_taken: That username is already in use.".
 */
export class AuthApiError extends Error {
    readonly status: number
    readonly code?: string

    constructor(status: number, message: string, code?: string) {
        super(message)
        this.name = "AuthApiError"
        this.status = status
        this.code = code
    }
}

function stripCodePrefix(detail: string, code: string | undefined): string {
    if (code && detail.startsWith(`${code}: `)) return detail.slice(code.length + 2)
    return detail
}

async function throwForFailedResponse(response: Response): Promise<never> {
    let detail = response.statusText || `Request failed (${response.status})`
    let code: string | undefined
    try {
        const body: unknown = await response.json()
        if (body && typeof body === "object") {
            if ("code" in body && typeof body.code === "string") code = body.code
            if ("detail" in body && typeof body.detail === "string") detail = body.detail
            // FastAPI's request-validation errors put a LIST here, one entry
            // per rejected field ({loc, msg, type}), not a string. Without
            // this branch the most common registration failure -- a password
            // under 8 characters -- fell through to `response.statusText`,
            // which HTTP/2 leaves empty, so the user was told
            // "Request failed (422)" and never which field was wrong.
            else if ("detail" in body && Array.isArray(body.detail)) {
                const parts = body.detail
                    .map((entry) => {
                        if (!entry || typeof entry !== "object") return null
                        const msg = "msg" in entry && typeof entry.msg === "string" ? entry.msg : null
                        if (msg === null) return null
                        const loc = "loc" in entry && Array.isArray(entry.loc) ? entry.loc : []
                        // Drop the leading "body"/"query" segment: it names the
                        // request part, not anything the user filled in.
                        const field = loc.filter((segment: unknown) => typeof segment === "string" && segment !== "body" && segment !== "query").pop()
                        return typeof field === "string" ? `${field}: ${msg}` : msg
                    })
                    .filter((part): part is string => part !== null)
                if (parts.length > 0) detail = parts.join("; ")
            }
        }
    } catch {
        // Non-JSON error body; keep the status text.
    }
    throw new AuthApiError(response.status, stripCodePrefix(detail, code), code)
}

function jsonHeaders(): HeadersInit {
    return { Accept: "application/json", "Content-Type": "application/json" }
}

async function postJson(path: string, body: unknown): Promise<unknown> {
    const response = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        credentials: "include",
        headers: jsonHeaders(),
        body: JSON.stringify(body),
    })
    if (!response.ok) return throwForFailedResponse(response)
    if (response.status === 204) return undefined
    return response.json()
}

export async function register(input: { username: string; password: string; email?: string; full_name?: string }): Promise<MeResponse> {
    const payload = await postJson("/api/v1/auth/register", input)
    return MeResponseSchema.parse(payload)
}

export async function login(input: { username: string; password: string }): Promise<MeResponse> {
    const payload = await postJson("/api/v1/auth/login", input)
    return MeResponseSchema.parse(payload)
}

export async function logout(): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
        // Survives the page going away. Signing out is the one request a
        // user is most likely to fire and then immediately close the tab or
        // navigate off, and without this the browser is free to cancel it
        // in flight -- leaving the session row alive and the cookie set.
        //
        // That matters more here than elsewhere because the cookie is
        // httpOnly: JavaScript cannot clear it, so only this response's
        // Set-Cookie can. If the request never lands, nothing on the client
        // can finish the job.
        keepalive: true,
    })
    if (!response.ok) return throwForFailedResponse(response)
}

/**
 * `null` on an unauthenticated 401 -- this is the "am I signed in" probe
 * `AuthProvider` calls on load, and a returning visitor with no live
 * session is not an error condition. Any OTHER non-2xx status still
 * throws `AuthApiError`, since that genuinely is unexpected (a 500, a
 * misconfigured proxy, ...).
 */
export async function fetchMe(): Promise<MeResponse | null> {
    const response = await fetch(`${API_BASE}/api/v1/auth/me`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (response.status === 401) return null
    if (!response.ok) return throwForFailedResponse(response)
    return MeResponseSchema.parse(await response.json())
}

export async function createApiKey(input: { label?: string }): Promise<ApiKeyCreateResponse> {
    const payload = await postJson("/api/v1/auth/api-keys", input)
    return ApiKeyCreateResponseSchema.parse(payload)
}

export async function listApiKeys(): Promise<ApiKeyMetadata[]> {
    const response = await fetch(`${API_BASE}/api/v1/auth/api-keys`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
    const payload = await response.json()
    if (!Array.isArray(payload)) throw new AuthApiError(200, "Archive returned malformed API key data.")
    return payload.map((entry) => ApiKeyMetadataSchema.parse(entry))
}

export async function revokeApiKey(keyId: number): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/auth/api-keys/${keyId}`, {
        method: "DELETE",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
}
