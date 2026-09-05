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
 * Thrown only after `requestScientificJson`'s own single automatic retry
 * (see below) ALSO came back 429 -- i.e. the anonymous-read budget
 * (`rate_limit_anon_read_per_minute`, `backend/app/api/config.py`) is
 * still exhausted a `Retry-After` window later. A plain `ScientificApiError`
 * with `status === 429` would fall through every caller's generic
 * catch-all and render as "unavailable" -- the exact bug this fixes ("The
 * archive service could not load this entry projection. Try again later.",
 * reported against a plain rate-limit burst). A rate limit is neither an
 * absent record nor a null value; it gets its own type, status, and
 * wording so no caller can accidentally collapse it into either.
 */
export class ScientificRateLimitError extends ScientificApiError {
    /** Seconds to wait before the archive's rate-limit window rolls over, per the retry's own `Retry-After` header. */
    readonly retryAfterSeconds: number

    constructor(retryAfterSeconds: number) {
        super(429, `The archive is rate-limiting anonymous reads; retry in about ${retryAfterSeconds}s.`, "rate_limited")
        this.name = "ScientificRateLimitError"
        this.retryAfterSeconds = retryAfterSeconds
    }
}

/** Used when a 429 response carries no usable `Retry-After` header (should not happen against the live API, which always sets one — see `backend/app/api/rate_limit.py:391` — but a fallback keeps the retry from firing with a 0ms or NaN delay against a misbehaving proxy or a test that forgets the header). */
const FALLBACK_RETRY_AFTER_SECONDS = 5

function parseRetryAfterSeconds(response: Response): number {
    const header = response.headers.get("Retry-After")
    const seconds = header === null ? NaN : Number.parseInt(header, 10)
    return Number.isFinite(seconds) && seconds > 0 ? seconds : FALLBACK_RETRY_AFTER_SECONDS
}

/** Rejects with `AbortError` immediately if already aborted, or as soon as `signal` aborts mid-wait, so a component that unmounts while this is sleeping doesn't leave a dangling retry. */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(new DOMException("Aborted", "AbortError"))
            return
        }
        const timer = setTimeout(resolve, ms)
        signal?.addEventListener(
            "abort",
            () => {
                clearTimeout(timer)
                reject(new DOMException("Aborted", "AbortError"))
            },
            { once: true },
        )
    })
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

async function throwForFailedResponse(response: Response): Promise<never> {
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

function fetchOnce(path: string, signal?: AbortSignal): Promise<Response> {
    return fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" }, signal })
}

/**
 * A 429 is transient by construction -- the anonymous-read budget resets
 * every window (`rate_limit_anon_read_per_minute`,
 * `backend/app/api/config.py:85`) -- so it is never treated as a terminal
 * failure on the first hit. The archive's own `Retry-After` (an integer
 * second count, `backend/app/api/rate_limit.py:391`) names exactly how
 * long until that window rolls over; this waits that long and retries
 * ONCE, transparently to the caller, before giving up. A caller that never
 * sees a rejection here (the common case: the burst was transient, the
 * retry lands inside the new window) never needs to know a 429 happened at
 * all. Only a caller unlucky enough to hit 429 twice in a row -- the
 * budget is still exhausted a full window later, i.e. sustained traffic,
 * not one burst -- ever sees `ScientificRateLimitError`, so callers can
 * treat it as "genuinely still limited" rather than "flaky".
 */
export async function requestScientificJson(path: string, signal?: AbortSignal): Promise<unknown> {
    const first = await fetchOnce(path, signal)
    if (first.status !== 429) {
        if (!first.ok) return throwForFailedResponse(first)
        return first.json()
    }

    const retryAfterSeconds = parseRetryAfterSeconds(first)
    await sleep(retryAfterSeconds * 1000, signal)

    const second = await fetchOnce(path, signal)
    if (second.status === 429) {
        throw new ScientificRateLimitError(parseRetryAfterSeconds(second))
    }
    if (!second.ok) return throwForFailedResponse(second)
    return second.json()
}

export function parseScientificResponse<T>(schema: z.ZodType<T>, payload: unknown, label: string): T {
    const result = schema.safeParse(payload)
    if (!result.success) throw new ScientificApiError(200, `Archive returned malformed ${label} data.`)
    return result.data
}
