import { z } from "zod"

const REF = /^(spe|rxe)_[a-z2-7]{26}$/

const exampleRefSchema = z.string().regex(REF)
const siteConfigSchema = z.object({ homeExampleRef: exampleRefSchema }).passthrough()

export type HomeExample = { kind: "molecule" | "reaction"; ref: string }

/** The example record named by a ref; anything not a molecule entry or reaction entry ref names none. */
export function parseHomeExample(raw: unknown): HomeExample | null {
    const parsed = exampleRefSchema.safeParse(typeof raw === "string" ? raw.trim() : raw)
    if (!parsed.success) return null
    return { kind: parsed.data.startsWith("spe_") ? "molecule" : "reaction", ref: parsed.data }
}

/**
 * The operator's `/site-config.json`, read at run time. A missing file, a non-JSON answer, bad JSON, a value
 * that does not match the schema or a network error all name no example.
 */
export async function loadHomeExample(_key?: string, signal?: AbortSignal): Promise<HomeExample | null> {
    try {
        const response = await fetch("/site-config.json", { cache: "no-cache", signal, headers: { Accept: "application/json" } })
        if (!response.ok) return null
        if (!(response.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) return null
        const parsed = siteConfigSchema.safeParse(await response.json())
        return parsed.success ? parseHomeExample(parsed.data.homeExampleRef) : null
    } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") throw error
        return null
    }
}
