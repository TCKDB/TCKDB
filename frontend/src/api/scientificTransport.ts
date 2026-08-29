import { z } from "zod"

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

export class ScientificApiError extends Error {
    readonly status: number
    /**
     * The archive's own `code` field from its error envelope
     * (`backend/app/api/error_contract.py`), e.g. `handle_type_mismatch`,
     * `invalid_handle`, `geometry_too_large`. `undefined` when the response
     * body carried no `code` (a bare-statusText fallback, or a caller that
     * mocked only `detail`). Callers that need to distinguish *why* a 422
     * happened — not every 422 means "this is not a valid reference" — read
     * this rather than pattern-matching `message`. See `useScientificRecord`.
     */
    readonly code?: string

    constructor(status: number, message: string, code?: string) {
        super(message)
        this.name = "ScientificApiError"
        this.status = status
        this.code = code
    }
}

export async function requestScientificJson(path: string, signal?: AbortSignal): Promise<unknown> {
    const response = await fetch(`${API_BASE}${path}`, {
        headers: { Accept: "application/json" },
        signal,
    })
    if (!response.ok) {
        let detail = response.statusText
        let code: string | undefined
        try {
            const body: unknown = await response.json()
            if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
                detail = body.detail
            }
            if (body && typeof body === "object" && "code" in body && typeof body.code === "string") {
                code = body.code
            }
        } catch { /* Keep the HTTP status text. */ }
        throw new ScientificApiError(response.status, detail, code)
    }
    return response.json()
}

export function parseScientificResponse<T>(schema: z.ZodType<T>, payload: unknown, label: string): T {
    const result = schema.safeParse(payload)
    if (!result.success) throw new ScientificApiError(200, `Archive returned malformed ${label} data.`)
    return result.data
}
