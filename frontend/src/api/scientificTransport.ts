import { z } from "zod"

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")

export class ScientificApiError extends Error {
    readonly status: number

    constructor(status: number, message: string) {
        super(message)
        this.name = "ScientificApiError"
        this.status = status
    }
}

export async function requestScientificJson(path: string, signal?: AbortSignal): Promise<unknown> {
    const response = await fetch(`${API_BASE}${path}`, {
        headers: { Accept: "application/json" },
        signal,
    })
    if (!response.ok) {
        let detail = response.statusText
        try {
            const body: unknown = await response.json()
            if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
                detail = body.detail
            }
        } catch { /* Keep the HTTP status text. */ }
        throw new ScientificApiError(response.status, detail)
    }
    return response.json()
}

export function parseScientificResponse<T>(schema: z.ZodType<T>, payload: unknown, label: string): T {
    const result = schema.safeParse(payload)
    if (!result.success) throw new ScientificApiError(200, `Archive returned malformed ${label} data.`)
    return result.data
}
