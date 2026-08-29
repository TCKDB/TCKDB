import { z } from "zod"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// Shape notes (measured 2026-08-29 against https://tckdb.homecalvin.com,
// backend/app/schemas/reads/scientific_transport.py and
// backend/app/services/scientific_read/{transport,species_transport}.py):
//
// - `GET /api/v1/scientific/species-entries/{id}/transport` is an
//   ENTRY-SCOPED LIST surface reusing `ScientificTransportSearchResponse`:
//   `{ request, review_summary, records[], pagination }` — same envelope
//   shape as statmech's, no top-level `species_entry_ref`.
//
// - Only TWO public include tokens gate real sections, read off
//   `TRANSPORT_RECORD_SECTIONS` in `_response.py`: `source_calculations`
//   and `review` (-> field `review_history`). `trust`/`assessments` are
//   further, internal-tokenized opt-ins out of scope for this slice.
//
// - `species` is NOT nullable on `ScientificTransportRecord` (unlike
//   statmech, transport rows attach at the species_entry level only — there
//   is no transition-state-owned transport row and so no XOR to represent).
//
// - Live-measured against `spe_bcbdjwkip75yoziblpntwzblzu` ([CH3]): this
//   entry has ZERO transport records (`records: [], pagination.total: 0`).
//   The species-search `availability.has_transport` flag for the same entry
//   also reads `false` — the two agree; there is no live contradiction to
//   surface for this fixture (though `EntryTransportSection.tsx` still
//   distinguishes a genuinely empty list from a load failure, since nothing
//   guarantees they always will agree).
// ---------------------------------------------------------------------------

export const TRANSPORT_SECTION_TOKENS = ["source_calculations", "review"] as const
export type TransportSectionToken = typeof TRANSPORT_SECTION_TOKENS[number]

/** Token -> response field name. Only `review` differs from its token. */
function sectionField(token: TransportSectionToken): string {
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

const transportCoreSchema = z.object({
    transport_ref: z.string(),
    scientific_origin: z.string(),
    sigma_angstrom: z.number().nullable().optional(),
    epsilon_over_k_k: z.number().nullable().optional(),
    dipole_debye: z.number().nullable().optional(),
    polarizability_angstrom3: z.number().nullable().optional(),
    rotational_relaxation: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
    created_at: z.string(),
    review: reviewBadgeSchema,
}).passthrough()

const evidenceSummarySchema = z.object({
    source_calculation_count: z.number(),
    has_source_calculations: z.boolean(),
    has_lj_parameters: z.boolean(),
    has_dipole_moment: z.boolean(),
    has_polarizability: z.boolean(),
    has_rotational_relaxation: z.boolean(),
    has_literature_source: z.boolean(),
}).passthrough()

const availableSectionsSchema = z.object({
    has_source_calculations: z.boolean(),
    has_review: z.boolean(),
}).passthrough()

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

const reviewEntrySchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const transportRecordSchema = z.object({
    transport: transportCoreSchema,
    supersession: supersessionSchema.nullable().optional(),
    species: speciesContextSchema,
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    literature: literatureSchema.nullable().optional(),
    evidence_summary: evidenceSummarySchema,
    available_sections: availableSectionsSchema,
    source_calculations: z.array(sourceCalculationSummarySchema).nullable().optional(),
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

const transportListResponseSchema = z.object({
    review_summary: reviewStatusSummarySchema,
    records: z.array(transportRecordSchema),
    pagination: paginationSchema,
}).passthrough()

export type TransportRecord = z.infer<typeof transportRecordSchema>
export type TransportSupersession = z.infer<typeof supersessionSchema>
export type TransportAvailableSections = z.infer<typeof availableSectionsSchema>
export type TransportListResponse = z.infer<typeof transportListResponseSchema>

function buildEndpoint(entryRef: string, tokens: readonly string[]): string {
    const base = `/api/v1/scientific/species-entries/${encodeURIComponent(entryRef)}/transport`
    if (tokens.length === 0) return base
    const query = new URLSearchParams()
    for (const token of tokens) query.append("include", token)
    return `${base}?${query}`
}

/**
 * Load every transport record deposited for this species entry. See the
 * module docstring on the live zero-record case. `useScientificRecord`
 * -shaped: `(ref, signal) => Promise<T>`.
 */
export async function loadEntryTransport(entryRef: string, signal?: AbortSignal): Promise<TransportListResponse> {
    const payload = await requestScientificJson(buildEndpoint(entryRef, []), signal)
    return parseScientificResponse(transportListResponseSchema, payload, "transport")
}

/**
 * Refetch the same entry-scoped list with exactly one additional heavy
 * include token — see the equivalent statmech function for why this
 * refetches the whole list rather than addressing one record.
 */
export async function loadEntryTransportSection(
    entryRef: string, token: TransportSectionToken, signal?: AbortSignal,
): Promise<TransportRecord[]> {
    const payload = await requestScientificJson(buildEndpoint(entryRef, [token]), signal)
    return parseScientificResponse(transportListResponseSchema, payload, "transport").records
}

/** Read the field a resolved on-demand token actually populated on one record. */
export function readTransportSectionField<T>(record: TransportRecord, token: TransportSectionToken): T {
    return (record as unknown as Record<string, unknown>)[sectionField(token)] as T
}
