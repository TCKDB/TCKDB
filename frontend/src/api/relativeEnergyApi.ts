import { z } from "zod"
import { parseScientificResponse, requestScientificJsonPost } from "./scientificTransport"

const memberSchema = z.object({
    conformer_ref: z.string(),
    status: z.string(),
    relative_energy_kj_mol: z.number().nullish(),
    value_hartree: z.number().nullish(),
    energy_calculation_refs: z.array(z.string()).default([]),
    absent_reason: z.string().nullish(),
    detail: z.array(z.string()).default([]),
    alternates: z.array(z.object({ determination_ref: z.string(), value_hartree: z.number() }).passthrough()).default([]),
}).passthrough()

const responseSchema = z.object({
    outcome: z.string(),
    basis: z.string().nullish(),
    reference: z.object({ conformer_ref: z.string(), status: z.string(), absent_reason: z.string().nullish() }).passthrough(),
    members: z.array(memberSchema),
}).passthrough()

export type RelativeEnergyMember = z.infer<typeof memberSchema>
export type RelativeEnergies = z.infer<typeof responseSchema>

/** What a relative-energy read is asked: one reference shape and one level of theory, both named by the reader. */
export type RelativeEnergyQuery = { entryRef: string; referenceRef: string; levelRef: string }

export const relativeEnergyKey = (query: RelativeEnergyQuery) => JSON.stringify(query)

/**
 * Energies of every shape of the entry relative to the named reference, at one level of theory. A read: nothing is
 * stored. The energy is electronic, and each shape must carry a stored check that it is a true minimum.
 */
export async function loadRelativeEnergies(
    key: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<RelativeEnergies> {
    const query = JSON.parse(key) as RelativeEnergyQuery
    const payload = await requestScientificJsonPost(
        `/api/v1/scientific/species-entries/${encodeURIComponent(query.entryRef)}/conformers/relative-energies`,
        {
            reference_conformer_ref: query.referenceRef,
            energy_kind: "electronic_energy",
            level: { level_of_theory_ref: query.levelRef },
            certificate: "local_minimum_any_surface",
        },
        signal,
        onRateLimited,
    )
    return parseScientificResponse(responseSchema, payload, "relative energy")
}
