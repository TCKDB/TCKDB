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

/** Tri-state for a `has_*` evidence flag: unset ("any"), "true", or "false" -- matching the query parameter's own optional-boolean shape. */
export type TriState = "" | "true" | "false"

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

    // Transition-state filters only.
    status: string
    method: string
    basis: string
    software: string
    softwareVersion: string
    workflowTool: string
    workflowToolVersion: string
    hasOpt: TriState
    hasFreq: TriState
    hasSp: TriState
    hasIrc: TriState
    hasPathSearch: TriState
    hasGeometryValidation: TriState
    hasScfStability: TriState
}

export const EMPTY_BROWSE_FILTERS: BrowseFilters = {
    charge: "", multiplicity: "", minReviewStatus: "", includeRejected: false, includeDeprecated: false,
    formula: "", elements: "", elemMode: "all", minHeavyAtoms: "", maxHeavyAtoms: "", electronicStateKind: "",
    status: "", method: "", basis: "", software: "", softwareVersion: "", workflowTool: "", workflowToolVersion: "",
    hasOpt: "", hasFreq: "", hasSp: "", hasIrc: "", hasPathSearch: "", hasGeometryValidation: "", hasScfStability: "",
}

const COMPOSITION_DEFAULTS = {
    formula: "", elements: "", elemMode: "all" as const, minHeavyAtoms: "", maxHeavyAtoms: "", electronicStateKind: "",
}
const EVIDENCE_DEFAULTS = {
    status: "", method: "", basis: "", software: "", softwareVersion: "", workflowTool: "", workflowToolVersion: "",
    hasOpt: "" as TriState, hasFreq: "" as TriState, hasSp: "" as TriState, hasIrc: "" as TriState,
    hasPathSearch: "" as TriState, hasGeometryValidation: "" as TriState, hasScfStability: "" as TriState,
}

/**
 * Drops whichever half of the flat `BrowseFilters` shape does not apply to
 * `kind`, leaving the shared fields untouched. Called on every kind switch
 * so the FORM (not just the outgoing request) stops showing a filter that
 * can no longer take effect -- a composition filter surviving a switch to
 * "Transition state" would look active while doing nothing, and a stale
 * evidence filter surviving a switch back to "Species" would silently do
 * nothing there either.
 */
export function clearInapplicableFilters(kind: BrowseKind, filters: BrowseFilters): BrowseFilters {
    if (kind === "transition_state") return { ...filters, ...COMPOSITION_DEFAULTS }
    return { ...filters, ...EVIDENCE_DEFAULTS }
}

/** True when any filter beyond the kind selector itself is set -- see `domain/browseEmptyState.ts`, which uses this to tell "nothing deposited" apart from "nothing matched". */
export function hasActiveFilters(kind: BrowseKind, filters: BrowseFilters): boolean {
    const sharedActive = filters.charge !== "" || filters.multiplicity !== "" || filters.minReviewStatus !== ""
        || filters.includeRejected || filters.includeDeprecated
    if (sharedActive) return true
    if (kind === "transition_state") {
        return filters.status !== "" || filters.method !== "" || filters.basis !== "" || filters.software !== ""
            || filters.softwareVersion !== "" || filters.workflowTool !== "" || filters.workflowToolVersion !== ""
            || [
                filters.hasOpt, filters.hasFreq, filters.hasSp, filters.hasIrc,
                filters.hasPathSearch, filters.hasGeometryValidation, filters.hasScfStability,
            ].some((value) => value !== "")
    }
    return filters.formula !== "" || filters.elements !== "" || filters.minHeavyAtoms !== ""
        || filters.maxHeavyAtoms !== "" || filters.electronicStateKind !== ""
}

// ---------------------------------------------------------------------------
// Query construction
// ---------------------------------------------------------------------------

function sharedQueryParams(filters: BrowseFilters, offset: number, limit: number): URLSearchParams {
    const query = new URLSearchParams()
    if (filters.charge !== "") query.set("charge", filters.charge)
    if (filters.multiplicity !== "") query.set("multiplicity", filters.multiplicity)
    if (filters.minReviewStatus !== "") query.set("min_review_status", filters.minReviewStatus)
    if (filters.includeRejected) query.set("include_rejected", "true")
    if (filters.includeDeprecated) query.set("include_deprecated", "true")
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
    if (filters.formula !== "") query.set("formula", filters.formula)
    if (filters.elements !== "") {
        query.set("elements", filters.elements)
        query.set("elem_mode", filters.elemMode)
    }
    if (filters.minHeavyAtoms !== "") query.set("min_heavy_atoms", filters.minHeavyAtoms)
    if (filters.maxHeavyAtoms !== "") query.set("max_heavy_atoms", filters.maxHeavyAtoms)
    if (filters.electronicStateKind !== "") query.set("electronic_state_kind", filters.electronicStateKind)
    return query
}

export function buildTransitionStateBrowseQuery(filters: BrowseFilters, offset: number, limit: number): URLSearchParams {
    const query = sharedQueryParams(filters, offset, limit)
    if (filters.status !== "") query.set("status", filters.status)
    if (filters.method !== "") query.set("method", filters.method)
    if (filters.basis !== "") query.set("basis", filters.basis)
    if (filters.software !== "") query.set("software", filters.software)
    if (filters.softwareVersion !== "") query.set("software_version", filters.softwareVersion)
    if (filters.workflowTool !== "") query.set("workflow_tool", filters.workflowTool)
    if (filters.workflowToolVersion !== "") query.set("workflow_tool_version", filters.workflowToolVersion)
    const evidenceFlags: [string, TriState][] = [
        ["has_opt", filters.hasOpt], ["has_freq", filters.hasFreq], ["has_sp", filters.hasSp],
        ["has_irc", filters.hasIrc], ["has_path_search", filters.hasPathSearch],
        ["has_geometry_validation", filters.hasGeometryValidation], ["has_scf_stability", filters.hasScfStability],
    ]
    for (const [param, value] of evidenceFlags) if (value !== "") query.set(param, value)
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
}).passthrough()

const transitionStateEntryCoreSchema = z.object({
    transition_state_entry_ref: z.string(),
    charge: z.number(),
    multiplicity: z.number(),
    status: z.string(),
    unmapped_smiles: z.string().nullable().optional(),
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
