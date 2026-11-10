import { z } from "zod"

const exampleRefSchema = z.string().regex(/^(spe|rxe)_[a-z2-7]{26}$/)

export type HomeExample = { kind: "molecule" | "reaction"; ref: string }

/** The example record named by a build-time value; anything unset or malformed names none. */
export function parseHomeExample(raw: unknown): HomeExample | null {
    const parsed = exampleRefSchema.safeParse(typeof raw === "string" ? raw.trim() : raw)
    if (!parsed.success) return null
    return { kind: parsed.data.startsWith("spe_") ? "molecule" : "reaction", ref: parsed.data }
}

/** Settings fixed when the site is built. */
export const siteConfig = {
    homeExample: parseHomeExample(import.meta.env.VITE_HOME_EXAMPLE_REF),
}
