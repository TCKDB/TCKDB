import { z } from "zod"
import { nameQueryKey } from "../domain/nameQuery"
import { ScientificApiError, ScientificRateLimitError, parseScientificResponse } from "./scientificTransport"

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const FALLBACK_RETRY_SECONDS = 30

const nameMatchSchema = z.object({
    species_ref: z.string(),
    formula: z.string().nullable().optional(),
    smiles: z.string().nullable().optional(),
    name: z.string(),
    kind: z.string().optional(),
    source_kind: z.string().optional(),
}).passthrough()

const nameSearchSchema = z.object({
    matches: z.array(nameMatchSchema),
    truncated: z.boolean().optional(),
}).passthrough()

export type NameMatch = {
    speciesRef: string
    name: string
    formula: string | null
    smiles: string | null
    imported: boolean
}

export type NameSearchResult = { matches: NameMatch[]; truncated: boolean }

const cache = new Map<string, NameSearchResult>()
let blockedUntil = 0

/** Seconds left of a name-search back-off, or 0 when requests may be sent. */
export function nameSearchBackoffSeconds(now = Date.now()): number {
    return blockedUntil > now ? Math.ceil((blockedUntil - now) / 1000) : 0
}

export function cachedNameSearch(text: string, match: "prefix" | "exact"): NameSearchResult | undefined {
    return cache.get(`${match}:${nameQueryKey(text)}`)
}

/** Empties the session cache and the back-off (tests). */
export function resetNameSearchSession() {
    cache.clear()
    blockedUntil = 0
}

function retrySeconds(response: Response): number {
    const header = response.headers.get("Retry-After")?.trim() ?? ""
    const seconds = /^\d+$/.test(header) ? Number.parseInt(header, 10) : 0
    return seconds > 0 ? seconds : FALLBACK_RETRY_SECONDS
}

/**
 * One name search. A 429 is never retried here: it starts a back-off that every caller
 * honours, and throws `ScientificRateLimitError` with the wait.
 */
export async function searchSpeciesNames(
    text: string,
    match: "prefix" | "exact",
    limit: number,
    signal?: AbortSignal,
): Promise<NameSearchResult> {
    const cached = cachedNameSearch(text, match)
    if (cached) return cached
    const wait = nameSearchBackoffSeconds()
    if (wait > 0) throw new ScientificRateLimitError(wait)

    const params = new URLSearchParams({ q: text.trim(), match, limit: String(limit) })
    const response = await fetch(`${API_BASE}/api/v1/scientific/species/name-search?${params}`, {
        headers: { Accept: "application/json" },
        signal,
    })
    if (response.status === 429) {
        const seconds = retrySeconds(response)
        blockedUntil = Date.now() + seconds * 1000
        throw new ScientificRateLimitError(seconds)
    }
    if (!response.ok) {
        let code: string | undefined
        try {
            const body: unknown = await response.json()
            if (body && typeof body === "object" && "code" in body && typeof body.code === "string") code = body.code
        } catch { /* keep the status */ }
        throw new ScientificApiError(response.status, response.statusText || "Name search failed.", code)
    }
    const parsed = parseScientificResponse(nameSearchSchema, await response.json(), "name search")
    const result: NameSearchResult = {
        truncated: parsed.truncated ?? false,
        matches: parsed.matches.map((item) => ({
            speciesRef: item.species_ref,
            name: item.name,
            formula: item.formula ?? null,
            smiles: item.smiles ?? null,
            imported: item.source_kind === "offline_import",
        })),
    }
    cache.set(`${match}:${nameQueryKey(text)}`, result)
    return result
}
