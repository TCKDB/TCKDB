import { z } from "zod"
import { levelOfTheorySchema, recordReviewSchema } from "./scientificSchemas"
import { scientificSpeciesRecordSchema } from "./scientificSpeciesSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The archive browse surface: `/species/browse` and
 * `/transition-states/browse` (`backend/app/api/routes/scientific/
 * species_browse.py`, `transition_states_browse.py`) -- identifier-free
 * catalogue reads, unlike `/species/search` and `/transition-states/search`
 * which require a handle. This module owns the query-building AND the
 * filter-state shape together, on purpose: the two are one contract (a
 * filter field only exists here because some query parameter answers it),
 * and splitting them across files would let one drift from the other
 * silently.
 */

// ---------------------------------------------------------------------------
// Kinds
// ---------------------------------------------------------------------------

/**
 * The three record kinds a reader can browse. "species" and "vdw" both hit
 * `/species/browse` -- they differ only in the `species_entry_kind` value
 * baked into the query (`minimum` vs `vdw_complex`, see
 * `StationaryPointKind` in `app/db/models/common.py`); "transition_state"
 * hits the sibling `/transition-states/browse` endpoint, whose row shape is
 * genuinely different (no formula -- a transition state is identified by
 * the reaction it connects, not a molecular graph).
 */
export const BROWSE_KINDS = ["species", "vdw", "transition_state"] as const
export type BrowseKind = (typeof BROWSE_KINDS)[number]
export const DEFAULT_BROWSE_KIND: BrowseKind = "species"

export function isBrowseKind(value: string | null): value is BrowseKind {
    return value !== null && (BROWSE_KINDS as readonly string[]).includes(value)
}

export const BROWSE_KIND_LABELS: Record<BrowseKind, string> = {
    species: "Species",
    vdw: "Van der Waals complex",
    transition_state: "Transition state",
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

/**
 * State for a `has_*` evidence flag: unset ("any") or "true". Was a
 * three-way type (`"" | "true" | "false"`) matching the query parameter's
 * own optional-boolean shape, back when each flag had its own tri-state
 * select (Any / Yes / No). The seven selects collapsed into one "Show
 * only entries with..." checkbox row (item 4 of the findability change),
 * which offers only the positive half -- a checkbox has no third state to
 * put "No" in, and nothing in `BrowseFilterForm.tsx` produces "false" for
 * any of these fields any more. Narrowed to match: the backend query
 * param still legitimately accepts `has_x=false` (unrelated to this UI,
 * used by other callers), so this type describes what the FORM can
 * produce, not what the wire protocol allows.
 */
export type EvidenceFlagState = "" | "true"

/**
 * Every filter field across all three kinds, flattened into one object
 * rather than a per-kind union. That is what lets the SHARED fields
 * (charge/multiplicity/review) survive a kind switch untouched --
 * `clearInapplicableFilters` below resets only the half that no longer
 * applies, in place, instead of requiring a copy step that could drop a
 * shared value by accident.
 */
export type BrowseFilters = {
    // Shared across every kind.
    charge: string
    multiplicity: string
    minReviewStatus: string
    includeRejected: boolean
    includeDeprecated: boolean

    // Species / vdW composition filters only.
    formula: string
    elements: string
    elemMode: "all" | "any"
    minHeavyAtoms: string
    maxHeavyAtoms: string
    electronicStateKind: string

    // Species / vdW structure filter only -- the /species/browse-side
    // counterpart of /scientific/species/structure-search's own
    // vocabulary (query_smiles/query_smarts/mode/similarity_threshold;
    // see BrowseFilterForm.tsx's `StructureField`). `queryStructure` is
    // ONE text field that routes to `query_smarts` when `queryIsSmarts`
    // is checked and to `query_smiles` otherwise -- never both.
    // `similarityThreshold` is free-text (like charge/heavy-atom fields)
    // so a half-typed value never reaches the wire mid-keystroke; only
    // read when `structureMode === "similarity"`.
    queryStructure: string
    queryIsSmarts: boolean
    structureMode: "exact" | "substructure" | "similarity"
    similarityThreshold: string

    // Transition-state filters only.
    status: string
    method: string
    basis: string
    software: string
    softwareVersion: string
    workflowTool: string
    workflowToolVersion: string
    hasOpt: EvidenceFlagState
    hasFreq: EvidenceFlagState
    hasSp: EvidenceFlagState
    hasIrc: EvidenceFlagState
    hasPathSearch: EvidenceFlagState
    hasGeometryValidation: EvidenceFlagState
    hasScfStability: EvidenceFlagState
    // Transition-state findability filters (item 4): `/transition-states/
    // browse` has no formula/elements of its own -- a transition state is
    // identified by the reaction it connects, not a molecular graph -- so
    // these narrow through that reaction instead. `participantSmiles` is
    // ONE field matching either side (reactant or product); `family` is an
    // exact match against `/meta/reaction-families`' bounded vocabulary.
    participantSmiles: string
    family: string
}

export const EMPTY_BROWSE_FILTERS: BrowseFilters = {
    charge: "", multiplicity: "", minReviewStatus: "", includeRejected: false, includeDeprecated: false,
    formula: "", elements: "", elemMode: "all", minHeavyAtoms: "", maxHeavyAtoms: "", electronicStateKind: "",
    queryStructure: "", queryIsSmarts: false, structureMode: "substructure", similarityThreshold: "",
    status: "", method: "", basis: "", software: "", softwareVersion: "", workflowTool: "", workflowToolVersion: "",
    hasOpt: "", hasFreq: "", hasSp: "", hasIrc: "", hasPathSearch: "", hasGeometryValidation: "", hasScfStability: "",
    participantSmiles: "", family: "",
}

const COMPOSITION_DEFAULTS = {
    formula: "", elements: "", elemMode: "all" as const, minHeavyAtoms: "", maxHeavyAtoms: "", electronicStateKind: "",
    queryStructure: "", queryIsSmarts: false, structureMode: "substructure" as const, similarityThreshold: "",
}
/**
 * `status` and the seven `has_*` evidence flags -- transition-state only;
 * `/species/browse` accepts none of these. The six PROVENANCE fields
 * (method/basis/software(+version)/workflow tool(+version)) are
 * deliberately NOT in here or in any per-kind defaults object: they are
 * NOT kind-specific -- `/species/browse` and `/transition-states/browse`
 * both accept all six (see `buildSpeciesBrowseQuery` /
 * `buildTransitionStateBrowseQuery`) -- so they are only ever cleared
 * explicitly by a reader picking "Any", never by `clearInapplicableFilters`
 * on a kind switch.
 */
const EVIDENCE_DEFAULTS = {
    status: "",
    hasOpt: "" as EvidenceFlagState, hasFreq: "" as EvidenceFlagState, hasSp: "" as EvidenceFlagState, hasIrc: "" as EvidenceFlagState,
    hasPathSearch: "" as EvidenceFlagState, hasGeometryValidation: "" as EvidenceFlagState, hasScfStability: "" as EvidenceFlagState,
    // TS-only findability filters (item 4) -- cleared the same way `status`
    // and the seven `has_*` flags above are, on a switch AWAY from
    // "transition_state".
    participantSmiles: "", family: "",
}

/**
 * Drops whichever half of the flat `BrowseFilters` shape does not apply to
 * `kind`, leaving the shared fields (and the six provenance fields, which
 * apply to every kind) untouched. Called on every kind switch so the FORM
 * (not just the outgoing request) stops showing a filter that can no
 * longer take effect -- a composition filter surviving a switch to
 * "Transition state" would look active while doing nothing, and a stale
 * `has_*`/`status` value surviving a switch back to "Species" would
 * silently do nothing there either.
 */
export function clearInapplicableFilters(kind: BrowseKind, filters: BrowseFilters): BrowseFilters {
    if (kind === "transition_state") return { ...filters, ...COMPOSITION_DEFAULTS }
    return { ...filters, ...EVIDENCE_DEFAULTS }
}

/**
 * True when any filter beyond the kind selector itself is set -- see
 * `domain/browseEmptyState.ts`, which uses this to tell "nothing deposited"
 * apart from "nothing matched". `includeRejected`/`includeDeprecated` are
 * deliberately EXCLUDED here: both WIDEN the result set (they relax a
 * default exclusion), so ticking one can never be the reason a listing came
 * back empty -- counting them as "active" made the empty-state copy claim a
 * widening toggle had narrowed the archive to zero, which is backwards.
 *
 * The six provenance fields count as active on EVERY kind, not just
 * "transition_state" -- `/species/browse` answers all six (see
 * `buildSpeciesBrowseQuery`), so a species query with only `method` set is
 * a real narrowing filter, and reporting it as "nothing active" would make
 * the empty state claim "nothing of this kind has been deposited" when the
 * true reason is that the filters excluded everything.
 */
export function hasActiveFilters(kind: BrowseKind, filters: BrowseFilters): boolean {
    const sharedActive = filters.charge !== "" || filters.multiplicity !== "" || filters.minReviewStatus !== ""
    if (sharedActive) return true
    const provenanceActive = filters.method !== "" || filters.basis !== "" || filters.software !== ""
        || filters.softwareVersion !== "" || filters.workflowTool !== "" || filters.workflowToolVersion !== ""
    if (provenanceActive) return true
    if (kind === "transition_state") {
        return filters.status !== "" || filters.participantSmiles !== "" || filters.family !== "" || [
            filters.hasOpt, filters.hasFreq, filters.hasSp, filters.hasIrc,
            filters.hasPathSearch, filters.hasGeometryValidation, filters.hasScfStability,
        ].some((value) => value !== "")
    }
    return filters.formula !== "" || filters.elements !== "" || filters.minHeavyAtoms !== ""
        || filters.maxHeavyAtoms !== "" || filters.electronicStateKind !== "" || filters.queryStructure !== ""
}

// ---------------------------------------------------------------------------
// Query construction
// ---------------------------------------------------------------------------

/**
 * Charge/multiplicity/min/max-heavy-atoms are free-text fields applied per
 * keystroke (see `BrowseFilterForm`'s doc comment) -- a half-typed value
 * like the lone `-` that starts any anion charge is not yet a valid
 * integer, and sending it produces a live 422 mid-keystroke. Rather than
 * changing the input type (which fights the browser's own handling of a
 * leading `-`), an incomplete value is simply not sent: the field stays
 * whatever the reader typed, but the query only gains the parameter once
 * it parses as a complete optionally-signed integer.
 */
function isCompleteInteger(value: string): boolean {
    return /^-?\d+$/.test(value)
}

/**
 * Same "don't send a half-typed value" reasoning as `isCompleteInteger`,
 * for `similarityThreshold` (a decimal 0.0-1.0, e.g. a lone trailing
 * `.` mid-keystroke is not yet a valid number). Never signed -- a
 * Tanimoto similarity is never negative.
 */
function isCompleteNumber(value: string): boolean {
    return /^\d+(\.\d+)?$/.test(value)
}

/**
 * The six provenance params -- method/basis/software(+version)/workflow
 * tool(+version) -- shared verbatim between `buildSpeciesBrowseQuery` and
 * `buildTransitionStateBrowseQuery` because both underlying endpoints
 * accept the exact same six query-parameter names (measured against
 * `species_browse.py` and `transition_states_browse.py`). Kept as its own
 * function rather than folded into `sharedQueryParams` so each builder's
 * own doc comment can still name its OWN kind-specific params next to the
 * params call that emits them, while the two builders cannot drift apart
 * on the shared six by one of them forgetting a line.
 */
function applyProvenanceParams(query: URLSearchParams, filters: BrowseFilters): void {
    if (filters.method !== "") query.set("method", filters.method)
    if (filters.basis !== "") query.set("basis", filters.basis)
    if (filters.software !== "") query.set("software", filters.software)
    if (filters.softwareVersion !== "") query.set("software_version", filters.softwareVersion)
    if (filters.workflowTool !== "") query.set("workflow_tool", filters.workflowTool)
    if (filters.workflowToolVersion !== "") query.set("workflow_tool_version", filters.workflowToolVersion)
}

function sharedQueryParams(filters: BrowseFilters, offset: number, limit: number): URLSearchParams {
    const query = new URLSearchParams()
    if (filters.charge !== "" && isCompleteInteger(filters.charge)) query.set("charge", filters.charge)
    if (filters.multiplicity !== "" && isCompleteInteger(filters.multiplicity)) query.set("multiplicity", filters.multiplicity)
    if (filters.minReviewStatus !== "") query.set("min_review_status", filters.minReviewStatus)
    if (filters.includeRejected) query.set("include_rejected", "true")
    if (filters.includeDeprecated) query.set("include_deprecated", "true")
    applyProvenanceParams(query, filters)
    query.set("offset", String(offset))
    query.set("limit", String(limit))
    return query
}

/** `species_entry_kind` is what actually distinguishes "species" from "vdw" on the wire -- both are the same endpoint, see `BROWSE_KINDS`'s doc comment. */
export function buildSpeciesBrowseQuery(
    kind: "species" | "vdw", filters: BrowseFilters, offset: number, limit: number,
): URLSearchParams {
    const query = sharedQueryParams(filters, offset, limit)
    query.set("species_entry_kind", kind === "vdw" ? "vdw_complex" : "minimum")
    // Sent explicitly rather than relying on the server default (`CollapseMode.all`,
    // `species_browse.py:69`) so a future server-side default change cannot silently
    // narrow this catalogue to one record per species without a test noticing.
    query.set("collapse", "all")
    if (filters.formula !== "") query.set("formula", filters.formula)
    if (filters.elements !== "") {
        query.set("elements", filters.elements)
        query.set("elem_mode", filters.elemMode)
    }
    if (filters.minHeavyAtoms !== "" && isCompleteInteger(filters.minHeavyAtoms)) query.set("min_heavy_atoms", filters.minHeavyAtoms)
    if (filters.maxHeavyAtoms !== "" && isCompleteInteger(filters.maxHeavyAtoms)) query.set("max_heavy_atoms", filters.maxHeavyAtoms)
    if (filters.electronicStateKind !== "") query.set("electronic_state_kind", filters.electronicStateKind)
    // Structure filter -- all four params travel together, and ONLY
    // together: an empty `queryStructure` means no structure filter at
    // all, so `mode`/`similarity_threshold` (meaningless without a
    // query) are never sent on their own. `queryIsSmarts` routes the
    // SAME typed value into query_smarts instead of query_smiles --
    // never both.
    if (filters.queryStructure !== "") {
        query.set(filters.queryIsSmarts ? "query_smarts" : "query_smiles", filters.queryStructure)
        query.set("mode", filters.structureMode)
        if (filters.structureMode === "similarity" && filters.similarityThreshold !== "" && isCompleteNumber(filters.similarityThreshold)) {
            query.set("similarity_threshold", filters.similarityThreshold)
        }
    }
    return query
}

export function buildTransitionStateBrowseQuery(filters: BrowseFilters, offset: number, limit: number): URLSearchParams {
    const query = sharedQueryParams(filters, offset, limit)
    if (filters.status !== "") query.set("status", filters.status)
    const evidenceFlags: [string, EvidenceFlagState][] = [
        ["has_opt", filters.hasOpt], ["has_freq", filters.hasFreq], ["has_sp", filters.hasSp],
        ["has_irc", filters.hasIrc], ["has_path_search", filters.hasPathSearch],
        ["has_geometry_validation", filters.hasGeometryValidation], ["has_scf_stability", filters.hasScfStability],
    ]
    for (const [param, value] of evidenceFlags) if (value !== "") query.set(param, value)
    // Findability filters (item 4) -- both additive, both exact match. See
    // `TransitionStatesBrowseRequest.participant_smiles`/`.family` on the
    // backend for the matching semantics.
    if (filters.participantSmiles !== "") query.set("participant_smiles", filters.participantSmiles)
    if (filters.family !== "") query.set("family", filters.family)
    return query
}

// ---------------------------------------------------------------------------
// Response schemas
// ---------------------------------------------------------------------------

const paginationSchema = z.object({
    offset: z.number(), limit: z.number(), returned: z.number(), total: z.number(), post_collapse_total: z.number(),
}).passthrough()
export type BrowsePagination = z.infer<typeof paginationSchema>

// Species/vdW rows reuse `scientificSpeciesRecordSchema` verbatim --
// `ScientificSpeciesBrowseResponse` is field-for-field identical to the
// search response's record shape by the backend's own design (see that
// endpoint's module docstring), so a second, hand-copied schema here would
// just be a second place for the two to drift apart.
const speciesBrowseResponseSchema = z.object({
    records: z.array(scientificSpeciesRecordSchema),
    pagination: paginationSchema,
}).passthrough()
export type SpeciesBrowseRecord = z.infer<typeof scientificSpeciesRecordSchema>

const reactionContextSchema = z.object({
    reaction_ref: z.string().nullable().optional(),
    reaction_entry_ref: z.string().nullable().optional(),
    equation: z.string().nullable().optional(),
    reversible: z.boolean().nullable().optional(),
    family: z.string().nullable().optional(),
}).passthrough()

// Mirrors `calculationSummarySchema`'s own inline `software_release` shape
// (`scientificSchemas.ts`) rather than importing a shared export -- this
// module owns its own response schemas end to end (see the module
// docstring), and the shape is small enough that duplicating it here does
// not risk drifting from the calculation surface's meaning of the same
// three fields.
const softwareReleaseSchema = z.object({
    software: z.string(),
    version: z.string().nullable().optional(),
    software_release_ref: z.string().optional(),
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
    levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)).optional(),
    // Added alongside `levels_of_theory` -- same per-calculation-type shape,
    // same absence contract (key absent: no calculation of that type; key
    // present, empty list: a calculation exists but names no software
    // release). Optional here because `evidenceSummarySchema` is shared by
    // response shapes that predate this field.
    software: z.record(z.string(), z.array(softwareReleaseSchema)).optional(),
}).passthrough()

const transitionStateEntryCoreSchema = z.object({
    transition_state_entry_ref: z.string(),
    charge: z.number(),
    multiplicity: z.number(),
    status: z.string(),
    unmapped_smiles: z.string().nullable().optional(),
    created_at: z.string().optional(),
    review: recordReviewSchema,
}).passthrough()

const transitionStateCoreSchema = z.object({
    transition_state_ref: z.string(),
    label: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    review: recordReviewSchema,
}).passthrough()

export const transitionStateBrowseRecordSchema = z.object({
    transition_state_entry: transitionStateEntryCoreSchema,
    transition_state: transitionStateCoreSchema,
    reaction: reactionContextSchema,
    evidence_summary: evidenceSummarySchema,
}).passthrough()
export type TransitionStateBrowseRecord = z.infer<typeof transitionStateBrowseRecordSchema>

const transitionStateBrowseResponseSchema = z.object({
    records: z.array(transitionStateBrowseRecordSchema),
    pagination: paginationSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// Loaders
// ---------------------------------------------------------------------------

export type BrowseResult =
    | { kind: "species" | "vdw"; records: SpeciesBrowseRecord[]; pagination: BrowsePagination }
    | { kind: "transition_state"; records: TransitionStateBrowseRecord[]; pagination: BrowsePagination }

export async function loadBrowse(
    kind: BrowseKind, filters: BrowseFilters, offset: number, limit: number, signal?: AbortSignal,
): Promise<BrowseResult> {
    if (kind === "transition_state") {
        const query = buildTransitionStateBrowseQuery(filters, offset, limit)
        const payload = await requestScientificJson(`/api/v1/scientific/transition-states/browse?${query}`, signal)
        const parsed = parseScientificResponse(transitionStateBrowseResponseSchema, payload, "transition state browse")
        return { kind, records: parsed.records, pagination: parsed.pagination }
    }
    const query = buildSpeciesBrowseQuery(kind, filters, offset, limit)
    const payload = await requestScientificJson(`/api/v1/scientific/species/browse?${query}`, signal)
    const parsed = parseScientificResponse(speciesBrowseResponseSchema, payload, "species browse")
    return { kind, records: parsed.records, pagination: parsed.pagination }
}
