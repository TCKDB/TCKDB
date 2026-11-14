import { z } from "zod"
import { hiddenByDefaultSchema } from "./listEnvelope"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * `GET /scientific/reaction-entries/{ref}/energy-profile`: electronic-energy profiles of one reaction entry, one per
 * transition-state entry, level of theory and program, plus the energies the entry's kinetics records cite. Tokens are
 * read as plain strings, so one this client has not been taught never fails the whole read.
 */

const energyType = z.string()

const softwareSchema = z.object({
    program: z.string(),
    version: z.string().nullish(),
    revision: z.string().nullish(),
    build: z.string().nullish(),
}).passthrough()

const moleculePointSchema = z.object({
    species_entry_ref: z.string(),
    count: z.number(),
    calculation_ref: z.string(),
    energy_type: energyType,
    electronic_energy_hartree: z.number(),
    software: softwareSchema,
    review_status: z.string(),
    convergence_unrecorded: z.boolean().default(false),
}).passthrough()

const transitionStatePointSchema = z.object({
    transition_state_entry_ref: z.string(),
    calculation_ref: z.string(),
    energy_type: energyType,
    electronic_energy_hartree: z.number(),
    software: softwareSchema,
    review_status: z.string(),
    convergence_unrecorded: z.boolean().default(false),
}).passthrough()

const valuesSchema = z.object({
    reactants: z.number(),
    transition_state: z.number(),
    products: z.number(),
}).passthrough()

const profileSchema = z.object({
    transition_state_entry_ref: z.string(),
    transition_state_entry_review_status: z.string(),
    level_of_theory: levelOfTheorySchema,
    program: z.string(),
    energy_types: z.array(energyType),
    versions: z.array(z.string().nullable()),
    energy_kind: z.string(),
    definition: z.string(),
    reactants: z.array(moleculePointSchema),
    transition_state: transitionStatePointSchema,
    products: z.array(moleculePointSchema),
    energy_hartree: valuesSchema,
    relative_kj_mol: valuesSchema,
}).passthrough()

const unavailableSchema = z.object({
    transition_state_entry_ref: z.string(),
    transition_state_entry_review_status: z.string(),
    code: z.string(),
    level_of_theory: levelOfTheorySchema.nullish(),
    program: z.string().nullish(),
}).passthrough()

const citedCalculationSchema = z.object({
    role: z.string(),
    calculation_ref: z.string(),
    review_status: z.string(),
    species_entry_ref: z.string().nullish(),
    transition_state_entry_ref: z.string().nullish(),
    level_of_theory_ref: z.string().nullish(),
    level_of_theory: levelOfTheorySchema.nullish(),
    software: softwareSchema.nullish(),
    energy_type: energyType.nullish(),
    electronic_energy_hartree: z.number().nullish(),
}).passthrough()

const citedByKineticsSchema = z.object({
    kinetics_ref: z.string(),
    calculations: z.array(citedCalculationSchema).default([]),
}).passthrough()

const responseSchema = z.object({
    request: z.object({
        hidden_by_default: hiddenByDefaultSchema.nullish(),
        hidden_by_default_by_scope: z.record(z.string(), z.record(z.string(), z.number())).default({}),
    }).passthrough(),
    reaction_entry_ref: z.string(),
    profiles: z.array(profileSchema).default([]),
    unavailable: z.array(unavailableSchema).default([]),
    cited_by_kinetics: z.array(citedByKineticsSchema).default([]),
}).passthrough()

export type EnergyProfileRead = z.infer<typeof responseSchema>
export type EnergyProfileRecord = z.infer<typeof profileSchema>
export type EnergyProfileMoleculePoint = z.infer<typeof moleculePointSchema>
export type EnergyProfileUnavailableRecord = z.infer<typeof unavailableSchema>
export type EnergyProfileCitedCalculation = z.infer<typeof citedCalculationSchema>
export type EnergyProfileKineticsCitation = z.infer<typeof citedByKineticsSchema>

export async function loadReactionEnergyProfile(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<EnergyProfileRead> {
    const endpoint = `/api/v1/scientific/reaction-entries/${encodeURIComponent(ref)}/energy-profile`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(responseSchema, payload, "energy profile")
}
