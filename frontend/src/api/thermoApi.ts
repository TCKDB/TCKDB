import { z } from "zod"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// Shape notes (measured 2026-08-29 against https://tckdb.homecalvin.com,
// backend/app/schemas/reads/scientific_thermo.py and
// backend/app/services/scientific_read/thermo.py):
//
// - `GET /api/v1/scientific/species-entries/{id}/thermo` is an ENTRY-SCOPED
//   LIST surface, not a `thermo_ref`-addressed detail route — there is no
//   standalone thermo detail page in this project (see the module docstring
//   on `EntryThermoSection.tsx`). The envelope is `{ request,
//   species_entry_ref, review_summary, records[], pagination }`: no `record`
//   wrapper, and no `species_entry_id` (Phase D policy-gates the internal id
//   off every public response; only the `spe_…` ref is present).
//
// - `get_species_thermo` (`app/services/scientific_read/thermo.py`) accepts
//   five public include tokens — `provenance`, `calculations`, `statmech`,
//   `review`, `artifacts` — that `validate_includes` treats as legal, but
//   NONE of them is ever tested with `if "<token>" in includes:` anywhere in
//   the service. They gate nothing. This was verified two ways: reading the
//   service body (no branch references any of the five), and cross-checking
//   `INCLUDE_GATED_COMPONENTS["ThermoRecord"]` in
//   `backend/app/api/routes/scientific/_response.py`, which lists only
//   `trust` and `assessments` for `ThermoRecord` — the one surface the brief
//   for this slice flagged as "gates nothing at all". This client therefore
//   requests NO include tokens for thermo: everything below is either an
//   always-present field or gated only by `trust`/`assessments`, and this
//   slice does not request either (trust is out of scope, matching the
//   precedent `CalculationDetailPage` set for the calculation surface).
//
// - `nasa` / `nasa9` / `wilhoit` / `points` are UNGATED SCIENTIFIC FACTS,
//   not sections behind a token. A `null` here means "this record has no
//   NASA-9 polynomial" — never "not requested". See the long comment above
//   `STATMECH_RECORD_SECTIONS` in `_response.py`, which names this thermo
//   record's four model fields as the canonical example of the distinction:
//   "If one ever does [appear in a strip table], the wire stops being able
//   to say 'this record has no NASA-9 polynomial' — which is the original
//   defect restored from the other side." `EntryThermoSection.tsx` renders
//   every one of the four independently and always distinguishes a genuine
//   `null` from a section that was never requested (which cannot happen
//   here, since nothing on this surface is request-gated).
// ---------------------------------------------------------------------------

const reviewBadgeSchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    reviewer_kind: z.string().nullable().optional(),
}).passthrough()

// `SupersessionNotice` — always computed, never behind an `include=` token
// (see the class docstring in `scientific_common.py`). `null` on a current
// record; present whenever this record was replaced. Never used as a signal
// to hide the record — a superseded record is not a deleted one.
const supersessionSchema = z.object({
    superseded_by: z.string(),
    current: z.string(),
    reason: z.string(),
    superseded_at: z.string(),
    chain_length: z.number(),
}).passthrough()

const nasaBlockSchema = z.object({
    t_low: z.number().nullable().optional(),
    t_mid: z.number().nullable().optional(),
    t_high: z.number().nullable().optional(),
    low_temperature_coefficients: z.array(z.number().nullable()).optional(),
    high_temperature_coefficients: z.array(z.number().nullable()).optional(),
}).passthrough()

const nasa9IntervalSchema = z.object({
    interval_index: z.number(),
    t_min_k: z.number(),
    t_max_k: z.number(),
    a1: z.number(),
    a2: z.number(),
    a3: z.number(),
    a4: z.number(),
    a5: z.number(),
    a6: z.number(),
    a7: z.number(),
    a8: z.number(),
    a9: z.number(),
}).passthrough()

const wilhoitSchema = z.object({
    cp0_j_mol_k: z.number(),
    cp_inf_j_mol_k: z.number(),
    b_k: z.number(),
    a0: z.number(),
    a1: z.number(),
    a2: z.number(),
    a3: z.number(),
    h0_kj_mol: z.number().nullable().optional(),
    s0_j_mol_k: z.number().nullable().optional(),
}).passthrough()

const pointSchema = z.object({
    temperature_k: z.number(),
    cp_j_mol_k: z.number().nullable().optional(),
    h_kj_mol: z.number().nullable().optional(),
    s_j_mol_k: z.number().nullable().optional(),
    g_kj_mol: z.number().nullable().optional(),
}).passthrough()

const temperatureCoverageSchema = z.object({
    requested_min_k: z.number().nullable().optional(),
    requested_max_k: z.number().nullable().optional(),
    record_min_k: z.number().nullable().optional(),
    record_max_k: z.number().nullable().optional(),
    covers_requested_range: z.boolean(),
    overlap_fraction: z.number().nullable().optional(),
    extrapolation_distance_k: z.number(),
}).passthrough()

const evidenceCompletenessSchema = z.object({
    score: z.number(),
    max: z.number(),
    checklist: z.record(z.string(), z.boolean()),
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

const calculationEvidenceSummarySchema = z.object({
    calculation_ref: z.string().nullable().optional(),
    calculation_type: z.string(),
    converged: z.boolean().nullable().optional(),
    geometry_validation_status: z.string(),
    scf_stability_status: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software: softwareReleaseSchema.nullable().optional(),
}).passthrough()

// Always present (`provenance: ThermoProvenance` is not `| None` on the
// wire) — none of its fields is gated by any include token; see the module
// docstring above.
//
// `software_release` / `workflow_tool_release` are the THERMO's own
// provenance (e.g. Arkane / ARC), sourced from `thermo.software_release_id`
// / `thermo.workflow_tool_release_id` server-side -- never backfilled from
// the primary calculation's software. `primary_calculation.software`
// (inside `calculationEvidenceSummarySchema` above) is the *calculation's*
// software (e.g. Gaussian) and is a separate fact. Issue #284: these two
// were previously collapsed into one field named `software` here, which
// silently discarded both real keys under `.passthrough()` and rendered
// every thermo record's software as "not recorded" on the live page.
const thermoProvenanceSchema = z.object({
    primary_calculation: calculationEvidenceSummarySchema.nullable().optional(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    statmech_ref: z.string().nullable().optional(),
    freq_calculation_ref: z.string().nullable().optional(),
    sp_calculation_ref: z.string().nullable().optional(),
    // PR #285: the conformer this record traces to, resolved server-side
    // through the SAME primary calculation used for `primary_calculation`/
    // `level_of_theory` above (`calculation.conformer_observation_id`),
    // never an independent pick. `null` on a record with no resolvable
    // primary calculation (population B) -- see
    // `domain/conformerEvidence.ts`'s `thermoConformerGroupRef`.
    conformer_observation_ref: z.string().nullable().optional(),
    conformer_group_ref: z.string().nullable().optional(),
}).passthrough()

const groupAdditivityComponentSchema = z.object({
    component_kind: z.string(),
    group_label: z.string(),
    count: z.number(),
    h298_contribution_kj_mol: z.number().nullable().optional(),
    s298_contribution_j_mol_k: z.number().nullable().optional(),
    cp298_contribution_j_mol_k: z.number().nullable().optional(),
}).passthrough()

// `null` unless this record is an estimated thermo with an attached
// group-additivity breakdown — an absent scientific fact, not a section.
const groupAdditivitySchema = z.object({
    scheme_ref: z.string(),
    scheme_name: z.string(),
    scheme_version: z.string().nullable().optional(),
    code_commit: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    components: z.array(groupAdditivityComponentSchema).optional(),
}).passthrough()

const thermoRecordSchema = z.object({
    thermo_ref: z.string(),
    scientific_origin: z.string(),
    model_kind: z.string(),
    review: reviewBadgeSchema,
    supersession: supersessionSchema.nullable().optional(),
    h298_kj_mol: z.number().nullable().optional(),
    s298_j_mol_k: z.number().nullable().optional(),
    h298_uncertainty_kj_mol: z.number().nullable().optional(),
    s298_uncertainty_j_mol_k: z.number().nullable().optional(),
    nasa: nasaBlockSchema.nullable().optional(),
    nasa9: z.array(nasa9IntervalSchema).nullable().optional(),
    wilhoit: wilhoitSchema.nullable().optional(),
    points: z.array(pointSchema).nullable().optional(),
    temperature_coverage: temperatureCoverageSchema.nullable().optional(),
    evidence_completeness: evidenceCompletenessSchema.optional(),
    provenance: thermoProvenanceSchema.optional(),
    group_additivity: groupAdditivitySchema.nullable().optional(),
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

const thermoListResponseSchema = z.object({
    species_entry_ref: z.string(),
    review_summary: reviewStatusSummarySchema,
    records: z.array(thermoRecordSchema),
    pagination: paginationSchema,
}).passthrough()

export type ThermoRecord = z.infer<typeof thermoRecordSchema>
export type ThermoSupersession = z.infer<typeof supersessionSchema>
export type ThermoEvidenceCompleteness = z.infer<typeof evidenceCompletenessSchema>
export type ThermoProvenance = z.infer<typeof thermoProvenanceSchema>
export type ThermoGroupAdditivity = z.infer<typeof groupAdditivitySchema>
export type ThermoListResponse = z.infer<typeof thermoListResponseSchema>

/**
 * Load every thermo record deposited for this species entry. No include
 * tokens are sent — see the module docstring: nothing on this surface is
 * gated by one. `useScientificRecord`-shaped: `(ref, signal) => Promise<T>`.
 */
export async function loadEntryThermo(entryRef: string, signal?: AbortSignal): Promise<ThermoListResponse> {
    const endpoint = `/api/v1/scientific/species-entries/${encodeURIComponent(entryRef)}/thermo`
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(thermoListResponseSchema, payload, "thermo")
}
