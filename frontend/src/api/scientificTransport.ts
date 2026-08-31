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

/**
 * The error envelope's `detail` is a plain string for most errors (e.g.
 * `geometry_too_large`) but FastAPI's own request-validation errors put a
 * LIST of per-field problems there instead (`{"code":
 * "request_validation_error", "detail": [{"loc": ["query", "charge"],
 * "msg": "Input should be a valid integer...", ...}]}`, measured against
 * the live `/species/browse?charge=abc`). Without this, that list form was
 * silently dropped -- `typeof body.detail === "string"` is false for an
 * array, so the archive's own explanation of what was wrong with the
 * request never reached the caller, and every caller fell back to the same
 * generic HTTP status text.
 */
function formatDetail(detail: unknown): string | undefined {
    if (typeof detail === "string") return detail
    if (Array.isArray(detail)) {
        const parts = detail
            .map((item): string | undefined => {
                if (!item || typeof item !== "object") return undefined
                const msg = "msg" in item && typeof item.msg === "string" ? item.msg : undefined
                if (msg === undefined) return undefined
                const loc = "loc" in item && Array.isArray(item.loc) ? item.loc.at(-1) : undefined
                return typeof loc === "string" ? `${loc}: ${msg}` : msg
            })
            .filter((part): part is string => part !== undefined)
        if (parts.length > 0) return parts.join("; ")
    }
    return undefined
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
            if (body && typeof body === "object" && "detail" in body) {
                const formatted = formatDetail(body.detail)
                if (formatted !== undefined) detail = formatted
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
