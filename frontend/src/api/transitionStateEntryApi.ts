import { z } from "zod"
import { levelOfTheorySchema, recordReviewSchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * `GET /scientific/transition-state-entries/{ref}` --
 * `backend/app/api/routes/scientific/transition_states.py`,
 * `backend/app/schemas/reads/scientific_transition_state.py`. One TS entry:
 * a single candidate saddle point, identified by the reaction it connects
 * rather than by a molecular graph -- see `TransitionStateEntryCoreBlock`'s
 * own docstring for why there is no `canonical_smiles`/`inchi_key` here,
 * only `unmapped_smiles`, and `domain/recordIdentity.ts`'s
 * `TransitionStateIdentity` for the shared header shape this feeds.
 *
 * This client fetches `include=calculations,geometries,review` up front --
 * mirroring `conformerGroupApi.ts`'s `loadConformerGroup`, which requests
 * every section this page renders in one round trip rather than lazily per
 * disclosure (unlike `CalculationDetailPage`'s heavier per-section fetch,
 * which exists for a 19-section surface this page's 3 extra sections don't
 * need). `validation_evidence` is NOT requested: `available_sections
 * .has_validation_evidence` is false for every entry sampled from the live
 * archive, and `validation.irc` (always present, no include needed) already
 * answers the one question that block exists to detail.
 */

const entryCoreSchema = z.object({
    transition_state_entry_ref: z.string(),
    charge: z.number(),
    multiplicity: z.number(),
    status: z.string(),
    unmapped_smiles: z.string().nullable().optional(),
    created_at: z.string(),
    review: recordReviewSchema,
}).passthrough()

const tsCoreSchema = z.object({
    transition_state_ref: z.string(),
    label: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    created_at: z.string(),
    review: recordReviewSchema,
}).passthrough()

const reactionContextSchema = z.object({
    reaction_ref: z.string().nullable().optional(),
    reaction_entry_ref: z.string().nullable().optional(),
    equation: z.string().nullable().optional(),
    reversible: z.boolean().nullable().optional(),
    family: z.string().nullable().optional(),
}).passthrough()

const evidenceSummarySchema = z.object({
    calculation_count: z.number(),
    has_opt: z.boolean(),
    has_freq: z.boolean(),
    has_sp: z.boolean(),
    has_irc: z.boolean(),
    has_path_search: z.boolean(),
    has_geometry_validation: z.boolean(),
    has_scf_stability: z.boolean(),
    levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)),
}).passthrough()

// "present" | "absent" | "failed" -- kept as a bare string rather than a
// zod enum so an archive-side vocabulary addition degrades to an unstyled
// label instead of a validation failure across this whole record.
const validationSchema = z.object({ irc: z.string() }).passthrough()

const availableSectionsSchema = z.object({
    has_entries: z.boolean(),
    has_calculations: z.boolean(),
    has_geometries: z.boolean(),
    has_review: z.boolean(),
    has_validation_evidence: z.boolean(),
}).passthrough()

const calculationSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    quality: z.string().optional(),
    review: recordReviewSchema.optional(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: z.object({ software: z.string() }).passthrough().nullable().optional(),
    workflow_tool_release: z.object({ workflow_tool: z.string() }).passthrough().nullable().optional(),
}).passthrough()

// `role`/`input_order`/`output_order` -- see `CalculationGeometryLinkSummary`'s
// own docstring: `input_order` is set for input links, `output_order`/`role`
// for output links, and never both. No `calculation_ref` on this link shape
// (unlike the conformer-group surface's geometry links), so this page cannot
// say which calculation produced which geometry -- only which role it played.
const geometrySchema = z.object({
    geometry_ref: z.string(),
    role: z.string().nullable().optional(),
    input_order: z.number().nullable().optional(),
    output_order: z.number().nullable().optional(),
    natoms: z.number().nullable().optional(),
    geom_hash: z.string().nullable().optional(),
}).passthrough()

const reviewHistoryEntrySchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const responseSchema = z.object({
    record: z.object({
        transition_state_entry: entryCoreSchema,
        transition_state: tsCoreSchema,
        reaction: reactionContextSchema,
        evidence_summary: evidenceSummarySchema,
        validation: validationSchema,
        available_sections: availableSectionsSchema,
        calculations: z.array(calculationSchema).nullable().optional(),
        geometries: z.array(geometrySchema).nullable().optional(),
        review_history: z.array(reviewHistoryEntrySchema).nullable().optional(),
    }).passthrough(),
}).passthrough()

export type TransitionStateEntryRecord = z.infer<typeof responseSchema>["record"]

export async function loadTransitionStateEntry(ref: string, signal?: AbortSignal): Promise<TransitionStateEntryRecord> {
    const query = new URLSearchParams()
    for (const include of ["calculations", "geometries", "review"]) query.append("include", include)
    const endpoint = `/api/v1/scientific/transition-state-entries/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(responseSchema, payload, "transition state entry").record
}
