import { z } from "zod"
import { loadMoleculeProbe } from "../api/neighbourApi"
import { loadReactionEntry, type ReactionFullRecord } from "../api/reactionEntryApi"
import { loadHomeExample } from "../config/siteConfig"
import { useLoaded } from "./useLoaded"

const moleculeRowSchema = z.object({
    canonical_smiles: z.string().nullish(),
    formula: z.string().nullish(),
}).passthrough()

/** What the home page knows about its example: still reading, none to show, or the record's light identity. */
export type HomeExampleState =
    | { status: "loading" }
    | { status: "none" }
    | { status: "molecule"; ref: string; smiles: string | null; formula: string | null }
    | { status: "reaction"; ref: string; record: ReactionFullRecord }

/**
 * Reads the operator's `/site-config.json`, then one light read of the named record. Anything that is not a
 * record the archive returns under its current visibility names no example; nothing else is ever picked.
 */
export function useHomeExample(): HomeExampleState {
    const config = useLoaded(loadHomeExample, "site-config")
    const example = config.status === "ready" ? config.data : null
    const molecule = useLoaded(loadMoleculeProbe, example?.kind === "molecule" ? example.ref : null)
    const reaction = useLoaded(loadReactionEntry, example?.kind === "reaction" ? example.ref : null)

    if (config.status === "idle" || config.status === "loading") return { status: "loading" }
    if (!example) return { status: "none" }
    if (example.kind === "molecule") {
        if (molecule.status === "idle" || molecule.status === "loading") return { status: "loading" }
        if (molecule.status !== "ready" || molecule.data.row === null) return { status: "none" }
        const row = moleculeRowSchema.safeParse(molecule.data.row)
        return row.success
            ? { status: "molecule", ref: example.ref, smiles: row.data.canonical_smiles ?? null, formula: row.data.formula ?? null }
            : { status: "none" }
    }
    if (reaction.status === "idle" || reaction.status === "loading") return { status: "loading" }
    if (reaction.status !== "ready") return { status: "none" }
    return { status: "reaction", ref: example.ref, record: reaction.data }
}
