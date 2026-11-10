import { z } from "zod"
import { hiddenByDefaultSchema, type HiddenByDefault } from "./listEnvelope"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

const thermoSchema = z.object({ thermo_ref: z.string(), declared_by: z.string().nullish() }).passthrough()
const kineticsSchema = z.object({
    kinetics_ref: z.string(),
    reaction_entry_ref: z.string(),
    role: z.string().nullish(),
    conformer_selection_kind: z.string().nullish(),
    ensemble_policy: z.string().nullish(),
}).passthrough()
const groupSchema = z.object({
    conformer_group_ref: z.string(),
    merged_into_conformer_group_ref: z.string().nullish(),
    thermo: z.array(thermoSchema).default([]),
    kinetics: z.array(kineticsSchema).default([]),
}).passthrough()
const responseSchema = z.object({
    groups: z.array(groupSchema).default([]),
    ensemble_thermo_refs: z.array(z.string()).default([]),
    request: z.object({ hidden_by_default: hiddenByDefaultSchema.nullish() }).passthrough().nullish(),
}).passthrough()

export type ConformerUsageGroup = z.infer<typeof groupSchema>
export type ConformerUsage = {
    groups: Map<string, ConformerUsageGroup>
    ensembleThermoRefs: string[]
    hidden: HiddenByDefault | null
}

/** Which thermo and kinetics records name each conformer group of an entry. Only groups something uses are listed. */
export async function loadConformerUsage(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerUsage> {
    const payload = await requestScientificJson(
        `/api/v1/scientific/species-entries/${encodeURIComponent(entryRef)}/conformer-usage`, signal, onRateLimited,
    )
    const response = parseScientificResponse(responseSchema, payload, "conformer usage")
    return {
        groups: new Map(response.groups.map((group) => [group.conformer_group_ref, group])),
        ensembleThermoRefs: response.ensemble_thermo_refs,
        hidden: response.request?.hidden_by_default ?? null,
    }
}
