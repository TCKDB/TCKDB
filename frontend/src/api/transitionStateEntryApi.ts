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
 * This client fetches `include=calculations,geometries,review,trust` up
 * front -- mirroring `conformerGroupApi.ts`'s `loadConformerGroup`, which
 * requests every section this page renders in one round trip rather than
 * lazily per disclosure (unlike `CalculationDetailPage`'s heavier
 * per-section fetch, which exists for a 19-section surface this page's
 * extra sections don't need). `validation_evidence` is NOT requested:
 * `available_sections.has_validation_evidence` is false for every entry
 * sampled from the live archive, and `validation.irc` (always present, no
 * include needed) already answers the one question that block exists to
 * detail.
 *
 * `trust` is requested now (it previously was not, despite being legal on
 * this endpoint) -- MEASURED on tse_aq5ktxlu27nvul3hmdwpuyuz4e:
 * `include=trust` serves `trust_status: "well_supported"` and
 * `evidence.checks.irc_evidence_present: "passed"` on the SAME entry
 * whose `validation.irc` (already requested, no extra cost) says
 * `"absent"` -- the two blocks read as contradictory on the page until
 * both are shown side by side with what each one actually asks (see
 * `TransitionStateEntryPage`'s header and Geometry section).
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

const energyBlockSchema = z.object({
    energy_hartree: z.number().nullable().optional(),
    energy_kind: z.string(),
}).passthrough()

const calculationSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    quality: z.string().optional(),
    created_at: z.string().optional(),
    review: recordReviewSchema.optional(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    // `version` is served alongside the name ('Gaussian' + '16', 'ARC' +
    // '1.1.0') and is load-bearing provenance in this archive -- an ESS/
    // workflow-tool version pins the exact behaviour that produced this
    // entry. Previously only the name reached this schema, so the page
    // had no version to show even though the API served one.
    software_release: z.object({ software: z.string(), version: z.string().nullable().optional() }).passthrough().nullable().optional(),
    workflow_tool_release: z.object({ workflow_tool: z.string(), version: z.string().nullable().optional() }).passthrough().nullable().optional(),
    // Present under `include=calculations` but was left off this table
    // (a comment at the old call site deliberately hid it) -- it is the
    // TS absolute energy, barrier context a reader of this table can use.
    // Null for every non-sp/opt calc type, per `CalculationEnergyBlock`'s
    // own docstring.
    energy: energyBlockSchema.nullable().optional(),
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

// `saddle_point` (schema `TransitionStateSaddlePointEvidence`) -- the
// imaginary-frequency verdict, always computed server-side, null only
// when the entry has no freq result to report. NOT served by the live
// deployment as of this branch (additive, unit-tested backend-side only
// so far -- see the PR body); a client hitting an older backend gets
// `undefined` here, which this schema's `.nullable().optional()` and the
// page's own `?? null` treat identically to a served `null`.
const saddlePointSchema = z.object({
    n_imag: z.number().nullable().optional(),
    imag_freq_cm1: z.number().nullable().optional(),
    reaction_coordinate_mode_index: z.number().nullable().optional(),
    imaginary_mode_structural_flag: z.boolean().nullable().optional(),
    calculation_ref: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
}).passthrough()

// Trust fragment (`TrustFragment`) -- only the two facts this page
// surfaces (`trust_status`, `evidence.passed_count`/`possible_count`) are
// pulled out; the rest (per-check map, llm_precheck, ...) is left to
// `.passthrough()` since nothing here renders it.
const trustSchema = z.object({
    trust_status: z.string(),
    evidence: z.object({
        passed_count: z.number(),
        possible_count: z.number(),
    }).passthrough(),
}).passthrough()

const responseSchema = z.object({
    record: z.object({
        transition_state_entry: entryCoreSchema,
        transition_state: tsCoreSchema,
        reaction: reactionContextSchema,
        evidence_summary: evidenceSummarySchema,
        validation: validationSchema,
        saddle_point: saddlePointSchema.nullable().optional(),
        available_sections: availableSectionsSchema,
        calculations: z.array(calculationSchema).nullable().optional(),
        geometries: z.array(geometrySchema).nullable().optional(),
        review_history: z.array(reviewHistoryEntrySchema).nullable().optional(),
        trust: trustSchema.nullable().optional(),
    }).passthrough(),
}).passthrough()

export type TransitionStateEntryRecord = z.infer<typeof responseSchema>["record"]

export async function loadTransitionStateEntry(ref: string, signal?: AbortSignal): Promise<TransitionStateEntryRecord> {
    const query = new URLSearchParams()
    for (const include of ["calculations", "geometries", "review", "trust"]) query.append("include", include)
    const endpoint = `/api/v1/scientific/transition-state-entries/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(responseSchema, payload, "transition state entry").record
}

// ---------------------------------------------------------------------------
// Sibling saddle points -- `GET /scientific/transition-states/search
// ?reaction_ref=...&include=calculations`, one request, confirmed on the
// wire (hydrazine reaction `rxn_xj7yamh5drvxapzlaukpzndbbu`, 4 entries):
// serves `transition_state.label`, `transition_state_entry.review`, and
// (under `include=calculations`) per-calc level of theory + software --
// everything "Other saddle points deposited for this reaction" needs.
// ---------------------------------------------------------------------------

const siblingRecordSchema = z.object({
    transition_state_entry: z.object({
        transition_state_entry_ref: z.string(),
        review: recordReviewSchema,
    }).passthrough(),
    transition_state: z.object({
        label: z.string().nullable().optional(),
    }).passthrough(),
    calculations: z.array(calculationSchema).nullable().optional(),
}).passthrough()

const siblingsResponseSchema = z.object({
    records: z.array(siblingRecordSchema),
}).passthrough()

export type TransitionStateSiblingRecord = z.infer<typeof siblingRecordSchema>

/**
 * Every TS-entry record attached to *reactionRef*, excluding
 * *excludeEntryRef* (the entry the caller is already showing) --
 * "Other saddle points deposited for this reaction". The default
 * `limit=50` on this search endpoint comfortably covers every reaction
 * sampled so far (the largest, hydrazine, has 4).
 */
export async function loadTransitionStateSiblings(
    reactionRef: string,
    excludeEntryRef: string,
    signal?: AbortSignal,
): Promise<TransitionStateSiblingRecord[]> {
    const query = new URLSearchParams()
    query.set("reaction_ref", reactionRef)
    query.append("include", "calculations")
    const endpoint = `/api/v1/scientific/transition-states/search?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    const parsed = parseScientificResponse(siblingsResponseSchema, payload, "transition state siblings")
    return parsed.records.filter((record) => record.transition_state_entry.transition_state_entry_ref !== excludeEntryRef)
}
