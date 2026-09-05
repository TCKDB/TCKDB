import { z } from "zod"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// Shape notes (measured 2026-08-29 against https://tckdb.homecalvin.com,
// backend/app/schemas/reads/scientific_statmech.py and
// backend/app/services/scientific_read/{statmech,species_statmech}.py):
//
// - `GET /api/v1/scientific/species-entries/{id}/statmech` is an
//   ENTRY-SCOPED LIST surface (the species-entry subresource read added
//   alongside thermo's — `species_subresources.py`). It reuses
//   `ScientificStatmechSearchResponse`: `{ request, review_summary,
//   records[], pagination }`. Unlike thermo's envelope, there is no
//   top-level `species_entry_ref` — the species context sits per-record at
//   `record.species.species_entry_ref` instead, because this is the same
//   envelope shape `/statmech/search` uses.
//
// - Six public include tokens gate real sections, read off
//   `STATMECH_RECORD_SECTIONS` in `_response.py` and cross-checked against
//   `_LEGAL_INCLUDE_TOKENS` in `services/scientific_read/statmech.py`:
//   `source_calculations`, `torsions`, `electronic_levels`, `frequencies`,
//   `conformers`, `review` (token `review` -> field `review_history`; every
//   other token names its own field — same asymmetry as the calculation
//   surface). `trust` and `assessments` are further, internal-tokenized
//   opt-ins (`include=all` never expands to either) and are out of scope for
//   this slice, matching the calculation-page precedent.
//
// - `available_sections` (`AvailableStatmechSections`) is GENUINELY
//   MEASURED here, not hardcoded — verified by reading
//   `build_statmech_record` in `services/scientific_read/statmech.py`:
//   every flag is a real `bool(...)` over freshly-queried rows
//   (`has_source_calculations=bool(source_rows)`,
//   `has_torsions=bool(torsion_rows)`,
//   `has_frequencies=any(r.role == freq for r in source_rows)`,
//   `has_conformers=<live EXISTS query>`,
//   `has_review=_exists_review_for(...)`). This is NOT the conformer
//   surface's hardcoded-false `has_selections` (issue #268) — do not
//   generalise that defect onto this surface.
//
// - Live-measured against `spe_bcbdjwkip75yoziblpntwzblzu` ([CH3]): the
//   `include=` gate is real at the LIST level too, not only on the
//   ref-scoped detail endpoint — `torsions` is entirely ABSENT from every
//   record when not requested, and present as `[]` (not `null`) once
//   requested, for a species entry whose statmech rows carry no torsions.
//   That is the three-state distinction `EntryStatmechSection.tsx` renders:
//   absent-because-not-requested is a different message from
//   present-and-empty.
// ---------------------------------------------------------------------------

export const STATMECH_SECTION_TOKENS = [
    "source_calculations",
    "torsions",
    "electronic_levels",
    "frequencies",
    "conformers",
    "review",
] as const
export type StatmechSectionToken = typeof STATMECH_SECTION_TOKENS[number]

/** Token -> response field name. Only `review` differs from its token. */
function sectionField(token: StatmechSectionToken): string {
    return token === "review" ? "review_history" : token
}

const reviewBadgeSchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    reviewer_kind: z.string().nullable().optional(),
}).passthrough()

const supersessionSchema = z.object({
    superseded_by: z.string(),
    current: z.string(),
    reason: z.string(),
    superseded_at: z.string(),
    chain_length: z.number(),
}).passthrough()

const speciesContextSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    canonical_smiles: z.string().nullable().optional(),
    inchi_key: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
}).passthrough()

const transitionStateContextSchema = z.object({
    transition_state_ref: z.string(),
    transition_state_entry_ref: z.string(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
    unmapped_smiles: z.string().nullable().optional(),
    reaction_entry_ref: z.string().nullable().optional(),
}).passthrough()

const softwareReleaseSchema = z.object({
    software_release_ref: z.string(),
    software: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const workflowToolReleaseSchema = z.object({
    workflow_tool_release_ref: z.string(),
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const literatureSchema = z.object({
    literature_ref: z.string(),
    title: z.string().nullable().optional(),
    year: z.number().nullable().optional(),
    doi: z.string().nullable().optional(),
}).passthrough()

const frequencyScaleFactorSummarySchema = z.object({
    frequency_scale_factor_ref: z.string(),
    value: z.number(),
    scale_kind: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software: softwareReleaseSchema.nullable().optional(),
    source_literature: literatureSchema.nullable().optional(),
}).passthrough()

const statmechCoreSchema = z.object({
    statmech_ref: z.string(),
    scientific_origin: z.string(),
    statmech_treatment: z.string().nullable().optional(),
    rigid_rotor_kind: z.string().nullable().optional(),
    point_group: z.string().nullable().optional(),
    external_symmetry: z.number().nullable().optional(),
    is_linear: z.boolean().nullable().optional(),
    uses_projected_frequencies: z.boolean().nullable().optional(),
    optical_isomers: z.number().nullable().optional(),
    rotational_constant_a_cm1: z.number().nullable().optional(),
    rotational_constant_b_cm1: z.number().nullable().optional(),
    rotational_constant_c_cm1: z.number().nullable().optional(),
    frequency_scale_factor_value: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
    created_at: z.string(),
    review: reviewBadgeSchema,
}).passthrough()

const evidenceSummarySchema = z.object({
    source_calculation_count: z.number(),
    has_opt_calculation: z.boolean(),
    has_freq_calculation: z.boolean(),
    has_sp_calculation: z.boolean(),
    sp_from_optimization: z.boolean().optional(),
    has_rotor_scans: z.boolean(),
    torsion_count: z.number(),
    has_frequency_scale_factor: z.boolean(),
    has_conformer_context: z.boolean(),
}).passthrough()

const availableSectionsSchema = z.object({
    has_source_calculations: z.boolean(),
    has_torsions: z.boolean(),
    has_electronic_levels: z.boolean(),
    has_frequencies: z.boolean(),
    has_conformers: z.boolean(),
    has_review: z.boolean(),
}).passthrough()

// ---------------------------------------------------------------------------
// include=source_calculations / torsions / electronic_levels / frequencies
// / conformers / review
// ---------------------------------------------------------------------------

const sourceCalculationSummarySchema = z.object({
    role: z.string(),
    calculation_ref: z.string(),
    calculation_type: z.string(),
    quality: z.string(),
    created_at: z.string(),
    review: reviewBadgeSchema,
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
}).passthrough()

const torsionCoordinateSchema = z.object({
    coordinate_index: z.number(),
    atom1_index: z.number(),
    atom2_index: z.number(),
    atom3_index: z.number(),
    atom4_index: z.number(),
}).passthrough()

const torsionSchema = z.object({
    torsion_index: z.number(),
    treatment_kind: z.string().nullable().optional(),
    symmetry_number: z.number().nullable().optional(),
    dimension: z.number(),
    top_description: z.string().nullable().optional(),
    invalidated_reason: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    source_scan_calculation_ref: z.string().nullable().optional(),
    coordinates: z.array(torsionCoordinateSchema).optional(),
}).passthrough()

const electronicLevelSchema = z.object({
    level_index: z.number(),
    energy_cm1: z.number(),
    degeneracy: z.number(),
}).passthrough()

const frequenciesSummarySchema = z.object({
    source_freq_calculation_refs: z.array(z.string()).optional(),
    frequency_scale_factor_value: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const conformerContextItemSchema = z.object({
    conformer_group_ref: z.string(),
    label: z.string().nullable().optional(),
}).passthrough()

const reviewEntrySchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// Top-level record + response envelope
// ---------------------------------------------------------------------------

const statmechRecordSchema = z.object({
    statmech: statmechCoreSchema,
    supersession: supersessionSchema.nullable().optional(),
    species: speciesContextSchema.nullable().optional(),
    transition_state: transitionStateContextSchema.nullable().optional(),
    frequency_scale_factor: frequencyScaleFactorSummarySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    literature: literatureSchema.nullable().optional(),
    evidence_summary: evidenceSummarySchema,
    available_sections: availableSectionsSchema,
    source_calculations: z.array(sourceCalculationSummarySchema).nullable().optional(),
    torsions: z.array(torsionSchema).nullable().optional(),
    electronic_levels: z.array(electronicLevelSchema).nullable().optional(),
    frequencies: frequenciesSummarySchema.nullable().optional(),
    conformers: z.array(conformerContextItemSchema).nullable().optional(),
    review_history: z.array(reviewEntrySchema).nullable().optional(),
}).passthrough()

const reviewStatusSummarySchema = z.object({
    approved: z.number(),
    under_review: z.number(),
    not_reviewed: z.number(),
    deprecated: z.number(),
    rejected: z.number(),
    total: z.number(),
}).passthrough()

const paginationSchema = z.object({
    offset: z.number(),
    limit: z.number(),
    returned: z.number(),
    total: z.number(),
    post_collapse_total: z.number(),
}).passthrough()

const statmechListResponseSchema = z.object({
    review_summary: reviewStatusSummarySchema,
    records: z.array(statmechRecordSchema),
    pagination: paginationSchema,
}).passthrough()

export type StatmechRecord = z.infer<typeof statmechRecordSchema>
export type StatmechSupersession = z.infer<typeof supersessionSchema>
export type StatmechAvailableSections = z.infer<typeof availableSectionsSchema>
export type StatmechListResponse = z.infer<typeof statmechListResponseSchema>

function buildEndpoint(entryRef: string, tokens: readonly string[]): string {
    const base = `/api/v1/scientific/species-entries/${encodeURIComponent(entryRef)}/statmech`
    if (tokens.length === 0) return base
    const query = new URLSearchParams()
    for (const token of tokens) query.append("include", token)
    return `${base}?${query}`
}

/**
 * Load every statmech record deposited for this species entry, with no
 * heavy include tokens (the eager set — core, species/TS context, evidence
 * summary, available_sections — is always present). `useScientificRecord`
 * -shaped: `(ref, signal) => Promise<T>`.
 */
export async function loadEntryStatmech(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<StatmechListResponse> {
    const payload = await requestScientificJson(buildEndpoint(entryRef, []), signal, onRateLimited)
    return parseScientificResponse(statmechListResponseSchema, payload, "statmech")
}

/**
 * Refetch the same entry-scoped list with exactly one additional heavy
 * include token. Unlike the calculation surface's per-record
 * `loadCalculationSection`, this token is not addressed to one record — it
 * is a request-time decision that gates that field on EVERY record the list
 * returns (see the module docstring, "measured...at the LIST level too").
 * Callers read only the one field the token gates
 * (`readStatmechSectionField`), once per record, matched by that record's
 * own `statmech_ref` — never the first record, never another record's.
 */
export async function loadEntryStatmechSection(
    entryRef: string, token: StatmechSectionToken, signal?: AbortSignal,
): Promise<StatmechRecord[]> {
    const payload = await requestScientificJson(buildEndpoint(entryRef, [token]), signal)
    return parseScientificResponse(statmechListResponseSchema, payload, "statmech").records
}

/** Read the field a resolved on-demand token actually populated on one record. */
export function readStatmechSectionField<T>(record: StatmechRecord, token: StatmechSectionToken): T {
    return (record as unknown as Record<string, unknown>)[sectionField(token)] as T
}
