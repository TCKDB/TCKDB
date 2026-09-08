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
 * The four record kinds a reader can browse. "species" and "vdw" both hit
 * `/species/browse` -- they differ only in the `species_entry_kind` value
 * baked into the query (`minimum` vs `vdw_complex`, see
 * `StationaryPointKind` in `app/db/models/common.py`); "transition_state"
 * hits the sibling `/transition-states/browse` endpoint, whose row shape is
 * genuinely different (no formula -- a transition state is identified by
 * the reaction it connects, not a molecular graph). "reaction" hits
 * `/reactions/browse` (PR 4b) -- rows carry reactant/product participants
 * (with formula and stoichiometry) and availability flags
 * (`has_kinetics`/`has_transition_state`) rather than a single molecular
 * graph or a linked TS.
 */
export const BROWSE_KINDS = ["species", "vdw", "transition_state", "reaction"] as const
export type BrowseKind = (typeof BROWSE_KINDS)[number]
export const DEFAULT_BROWSE_KIND: BrowseKind = "species"

export function isBrowseKind(value: string | null): value is BrowseKind {
    return value !== null && (BROWSE_KINDS as readonly string[]).includes(value)
}

export const BROWSE_KIND_LABELS: Record<BrowseKind, string> = {
    species: "Species",
    vdw: "Van der Waals complex",
    transition_state: "Transition state",
    reaction: "Reaction",
}

/**
 * Each index's own identity (owner, repeatedly: "Why is the browse reactions
 * with the species/transition/vanderwaals browsing page?" / "there should be
 * separate for reaction and species not slammed together"). Before this, all
 * four kind paths rendered `BrowsePage` with the SAME heading/eyebrow/intro
 * ("Browse the archive" / "Archive index" / "Choose what to browse, then
 * narrow it down…") -- the URL said which kind, but nothing ON the page did.
 * `heading` names the kind directly (an h1 a reader can screenshot and know
 * which archive they are looking at); `intro` is one line stating what the
 * index actually holds, including the one thing it deliberately excludes
 * (`species` naming that `vdw` is catalogued separately, and vice versa --
 * the two hit the same `/species/browse` endpoint under the hood, which is
 * exactly the distinction a reader cannot see from the URL alone).
 * `breadcrumbLabel` is the honest per-path breadcrumb trail end (was the
 * literal string "Browse" on every one of the four paths).
 */
export const BROWSE_KIND_CONTENT: Record<BrowseKind, { eyebrow: string; heading: string; intro: string; breadcrumbLabel: string }> = {
    species: {
        eyebrow: "Species index",
        heading: "Browse species",
        intro: "Every stable minimum deposited in the archive. Narrow by formula, structure, charge, or review "
            + "status -- van der Waals complexes are catalogued on their own page.",
        breadcrumbLabel: "Species",
    },
    vdw: {
        eyebrow: "Van der Waals index",
        heading: "Browse van der Waals complexes",
        intro: "Weakly bound complexes deposited in the archive, catalogued separately from ordinary species "
            + "minima. Narrow by formula, structure, charge, or review status.",
        breadcrumbLabel: "Van der Waals complexes",
    },
    transition_state: {
        eyebrow: "Transition-state index",
        heading: "Browse transition states",
        intro: "Every transition state deposited in the archive, identified by the reaction it connects rather "
            + "than a molecular formula. Narrow by participant SMILES, family, or calculation evidence.",
        breadcrumbLabel: "Transition states",
    },
    reaction: {
        eyebrow: "Reaction index",
        heading: "Browse reactions",
        intro: "Every reaction entry deposited in the archive. Narrow by reactant or product structure, family, "
            + "or available kinetics and transition-state evidence.",
        breadcrumbLabel: "Reactions",
    },
}

/**
 * Each browse kind's own path (owner decision: one URL per kind, replacing
 * the earlier single `/species?kind=` surface -- see `App.tsx`). The ONE
 * place either direction of the kind<->path relationship is spelled out;
 * `App.tsx` builds its four `BrowsePage` routes from this object (kind ->
 * path) and `browseKindForPath` below inverts it (path -> kind) rather than
 * hand-maintaining a second table, so the route table and the reverse
 * lookup cannot drift apart. `species` keeps its existing path (the
 * pre-existing default, and the one path that still accepts a legacy
 * `?kind=` for backward compatibility); `vdw`/`transition_state`/`reaction`
 * get new plural, hyphenated paths matching the app's existing
 * `/species-entries`, `/reaction-entries`, `/transition-state-entries`
 * convention.
 */
export const BROWSE_KIND_PATHS: Record<BrowseKind, string> = {
    species: "/species",
    vdw: "/vdw-complexes",
    transition_state: "/transition-states",
    reaction: "/reactions",
}

const PATH_TO_BROWSE_KIND: Record<string, BrowseKind> = Object.fromEntries(
    BROWSE_KINDS.map((kind) => [BROWSE_KIND_PATHS[kind], kind]),
) as Record<string, BrowseKind>

/** Inverse of `BROWSE_KIND_PATHS` -- an exact-match lookup (not a prefix match), since `location.pathname` for a `BrowsePage` route is always exactly one of the four. */
export function browseKindForPath(pathname: string): BrowseKind | undefined {
    return PATH_TO_BROWSE_KIND[pathname]
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
    // `family` is SHARED with the "reaction" kind below (both narrow
    // through the same reaction-family vocabulary) -- it is the one field
    // that survives a switch between "transition_state" and "reaction",
    // see `clearInapplicableFilters`.
    participantSmiles: string
    family: string

    // Reaction-only filters (PR 4b): `/reactions/browse` has no charge,
    // multiplicity, or provenance axis of its own (a reaction is not a
    // single calculation owner the way a species or transition state is --
    // measured against the live endpoint, which accepts exactly `family,
    // reactant_smiles, product_smiles, has_kinetics, has_transition_state,
    // min_review_status, include_rejected, include_deprecated, offset,
    // limit`). `reactantSmiles`/`productSmiles` are two separate exact-match
    // fields (unlike the TS kind's one merged `participantSmiles`) because
    // the backend filter itself is side-specific.
    reactantSmiles: string
    productSmiles: string
    hasKinetics: EvidenceFlagState
    hasTransitionState: EvidenceFlagState
}

export const EMPTY_BROWSE_FILTERS: BrowseFilters = {
    charge: "", multiplicity: "", minReviewStatus: "", includeRejected: false, includeDeprecated: false,
    formula: "", elements: "", elemMode: "all", minHeavyAtoms: "", maxHeavyAtoms: "", electronicStateKind: "",
    queryStructure: "", queryIsSmarts: false, structureMode: "substructure", similarityThreshold: "",
    status: "", method: "", basis: "", software: "", softwareVersion: "", workflowTool: "", workflowToolVersion: "",
    hasOpt: "", hasFreq: "", hasSp: "", hasIrc: "", hasPathSearch: "", hasGeometryValidation: "", hasScfStability: "",
    participantSmiles: "", family: "",
    reactantSmiles: "", productSmiles: "", hasKinetics: "", hasTransitionState: "",
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
 * on a kind switch. `participantSmiles` (item 4's TS-only findability
 * field) is here too, but `family` is NOT -- `family` is shared with the
 * "reaction" kind below (see `FAMILY_DEFAULT`'s own comment).
 */
const EVIDENCE_DEFAULTS = {
    status: "",
    hasOpt: "" as EvidenceFlagState, hasFreq: "" as EvidenceFlagState, hasSp: "" as EvidenceFlagState, hasIrc: "" as EvidenceFlagState,
    hasPathSearch: "" as EvidenceFlagState, hasGeometryValidation: "" as EvidenceFlagState, hasScfStability: "" as EvidenceFlagState,
    participantSmiles: "",
}

/** Reaction-only filters (PR 4b) -- cleared on a switch away from "reaction". See `BrowseFilters.reactantSmiles`'s own comment for why these have no provenance/composition counterpart. */
const REACTION_ONLY_DEFAULTS = {
    reactantSmiles: "", productSmiles: "",
    hasKinetics: "" as EvidenceFlagState, hasTransitionState: "" as EvidenceFlagState,
}

/**
 * `family` alone, cleared only on a switch to "species"/"vdw" (neither
 * narrows by reaction family at all). Kept OUT of both `EVIDENCE_DEFAULTS`
 * and `REACTION_ONLY_DEFAULTS` because `family` is the one field shared
 * between "transition_state" and "reaction" -- both narrow through
 * `/meta/reaction-families`' same bounded vocabulary (see
 * `TransitionStateFindabilityFields`'s and the reaction kind's own family
 * dropdown) -- so a value typed while on one of those two kinds must
 * survive a switch to the OTHER, not just to itself.
 */
const FAMILY_DEFAULT = { family: "" }

/**
 * Drops whichever half of the flat `BrowseFilters` shape does not apply to
 * `kind`, leaving the shared fields (and the six provenance fields, which
 * apply to species/vdw/transition_state but NOT reaction) untouched.
 * Called on every kind switch so the FORM (not just the outgoing request)
 * stops showing a filter that can no longer take effect -- a composition
 * filter surviving a switch to "Transition state" would look active while
 * doing nothing, and a stale `has_*`/`status` value surviving a switch back
 * to "Species" would silently do nothing there either.
 */
// Review follow-up (round 2), decision recorded here rather than left
// implicit: the six provenance fields are NOT added to any defaults group
// above, so a value set while on species/vdw/transition_state survives a
// switch through "reaction" (where the fields are hidden and inert, see
// `BrowseFilterForm`'s own `!isReaction` guards) and re-applies the moment
// the reader switches to a kind that reads them again. Chose to PRESERVE
// this round-trip rather than carve out a reaction-specific clear: the
// module's own established rule for these six fields (see
// `EVIDENCE_DEFAULTS`'s doc comment, "only ever cleared explicitly by a
// reader picking 'Any', never by a kind switch") already applies uniformly
// across every kind pair today, and a reaction-only exception would make
// this function's contract depend on WHICH kind a value is hidden by,
// not just whether it is currently visible -- a real ("Method" narrows the
// SAME calculations regardless of which browse kind is currently
// selected) but genuinely debatable trade-off; see the PR body for the
// alternative considered and why it was not taken.
export function clearInapplicableFilters(kind: BrowseKind, filters: BrowseFilters): BrowseFilters {
    if (kind === "transition_state") return { ...filters, ...COMPOSITION_DEFAULTS, ...REACTION_ONLY_DEFAULTS }
    if (kind === "reaction") return { ...filters, ...COMPOSITION_DEFAULTS, ...EVIDENCE_DEFAULTS }
    return { ...filters, ...EVIDENCE_DEFAULTS, ...REACTION_ONLY_DEFAULTS, ...FAMILY_DEFAULT }
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
 * The six provenance fields count as active on species/vdw/transition_state
 * -- `/species/browse` answers all six (see `buildSpeciesBrowseQuery`), so
 * a species query with only `method` set is a real narrowing filter, and
 * reporting it as "nothing active" would make the empty state claim
 * "nothing of this kind has been deposited" when the true reason is that
 * the filters excluded everything. "reaction" is handled in its own
 * branch, FIRST, because none of charge/multiplicity/the six provenance
 * fields apply to `/reactions/browse` at all (see `BrowseFilters.
 * reactantSmiles`'s comment) -- falling through to the shared/provenance
 * checks below for that kind would report a stale species-scoped `method`
 * value as an active reaction filter when it does nothing on the wire.
 */
export function hasActiveFilters(kind: BrowseKind, filters: BrowseFilters): boolean {
    if (kind === "reaction") {
        return filters.minReviewStatus !== "" || filters.family !== ""
            || splitSmilesList(filters.reactantSmiles).length > 0 || splitSmilesList(filters.productSmiles).length > 0
            || filters.hasKinetics !== "" || filters.hasTransitionState !== ""
    }
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

/**
 * `reactantSmiles`/`productSmiles` hold a COMMA-separated list, typed by the
 * reader into one text box (`ReactionFindabilityFields`,
 * `BrowseFilterForm.tsx`) -- not a bare single SMILES any more. Comma is the
 * only separator that cannot appear inside a SMILES token itself the way `+`
 * and `.` can (`[NH4+]` carries a `+`; a disconnected-component SMILES like
 * `[Na+].[Cl-]` carries a `.`), so splitting on it never mis-parses a real
 * structure into two. Each split token is trimmed and blank tokens are
 * dropped, so `"NN, [H],"` (stray whitespace/trailing comma from typing) and
 * `""` (nothing typed) both behave exactly as intended -- one clean token
 * list, or none at all.
 */
function splitSmilesList(value: string): string[] {
    return value.split(",").map((token) => token.trim()).filter((token) => token !== "")
}

/**
 * `/scientific/reactions/browse` (PR 4b, §3D of the plan) -- deliberately
 * NOT built on `sharedQueryParams`: that helper sends charge, multiplicity,
 * and the six provenance params, none of which this endpoint accepts
 * (verified live -- `GET /reactions/browse` answers exactly `family,
 * reactant_smiles, product_smiles, has_kinetics, has_transition_state,
 * min_review_status, include_rejected, include_deprecated, offset, limit`).
 * Sending an inapplicable param would not 422 (the route ignores unknown
 * query keys) but would silently do nothing, which is the same "looks
 * active while doing nothing" failure `clearInapplicableFilters` exists to
 * prevent for the FORM -- this function is the matching guarantee for the
 * REQUEST.
 *
 * `reactant_smiles`/`product_smiles` are sent as REPEATED params, one per
 * comma-separated token in the filter's own text value (`splitSmilesList`
 * above) -- verified live: `?reactant_smiles=NN` returns 20 reactions,
 * `?reactant_smiles=NN&reactant_smiles=[H]` returns 0 (the archive holds no
 * reaction with both together on one side), and `?reactant_smiles=` (empty)
 * is unfiltered, same as omitting it entirely. A single-token value (no
 * comma typed) still produces exactly one `query.append`, so the one-value
 * shape callers relied on before this change is unaffected.
 */
export function buildReactionBrowseQuery(filters: BrowseFilters, offset: number, limit: number): URLSearchParams {
    const query = new URLSearchParams()
    if (filters.family !== "") query.set("family", filters.family)
    for (const smiles of splitSmilesList(filters.reactantSmiles)) query.append("reactant_smiles", smiles)
    for (const smiles of splitSmilesList(filters.productSmiles)) query.append("product_smiles", smiles)
    if (filters.hasKinetics !== "") query.set("has_kinetics", filters.hasKinetics)
    if (filters.hasTransitionState !== "") query.set("has_transition_state", filters.hasTransitionState)
    if (filters.minReviewStatus !== "") query.set("min_review_status", filters.minReviewStatus)
    if (filters.includeRejected) query.set("include_rejected", "true")
    if (filters.includeDeprecated) query.set("include_deprecated", "true")
    query.set("offset", String(offset))
    query.set("limit", String(limit))
    return query
}

/**
 * Seeds whichever filters can be read back from the INITIAL URL, kind by
 * kind (`BrowsePage.tsx`'s lazy `useState` initializer, run once on mount).
 * Deliberately not a general "every filter field syncs two-way with a
 * matching query param" mechanism -- every field in `BrowseFilters` already
 * applies immediately as the reader types (`BrowseFilterForm`'s own doc
 * comment) and drives ONE outgoing request; adding a live URL<->filters
 * sync on top would be a second source of truth for state the page already
 * owns, for a need only the initial mount actually has. What this covers is
 * external LINKERS -- another page constructing a `BrowsePage` URL and
 * expecting the field to already show what the link promised:
 *
 * - `SpeciesEntrySummary.tsx`'s "Transition states for reactions of this
 *   species" link (`?participant_smiles=...`, `transition_state` kind).
 * - The front-page reaction search's "See all N reactions involving X"
 *   link (`?reactant_smiles=...`/`?product_smiles=...`, `reaction` kind).
 *   Gap found post-hoc: that link can carry the SAME param repeated
 *   (`?reactant_smiles=NN&reactant_smiles=%5BH%5D`, the multi-structure
 *   search this filter form now supports) -- `searchParams.get` would
 *   silently keep only the first, understating what the link promised, so
 *   this reads with `getAll` and rejoins with a comma (`splitSmilesList`'s
 *   own on-the-wire shape, above) rather than `.get`.
 *
 * Still one function with an explicit per-kind branch, not a fully generic
 * "read every filter from a same-named param" walk -- that would seed
 * fields NO external caller ever links to today (family, review status,
 * evidence flags…) from a URL a reader might have hand-edited or bookmarked
 * with unrelated params, silently pre-filtering a listing the reader never
 * asked to be filtered. Both cases above are a real, known linker; a third
 * kind gains a line here only when a third linker exists to seed for.
 */
export function seedFiltersFromUrl(kind: BrowseKind, searchParams: URLSearchParams): Partial<BrowseFilters> {
    if (kind === "transition_state") {
        return { participantSmiles: searchParams.get("participant_smiles") ?? "" }
    }
    if (kind === "reaction") {
        const reactantSmiles = searchParams.getAll("reactant_smiles").filter((value) => value !== "")
        const productSmiles = searchParams.getAll("product_smiles").filter((value) => value !== "")
        return { reactantSmiles: reactantSmiles.join(","), productSmiles: productSmiles.join(",") }
    }
    return {}
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

/**
 * `/reactions/browse` row shape (PR 4b), measured live 2026-09-07 against
 * `GET /scientific/reactions/browse?limit=2`. FLAT, unlike the
 * `transition_state`/`reaction` split `TransitionStateBrowseRecord` nests
 * (`transitionStateEntryCoreSchema`/`transitionStateCoreSchema`/
 * `reactionContextSchema`) -- there is no separate "reaction" vs "reaction
 * entry" sub-object on this row; `reaction_ref` and `reaction_entry_ref`
 * sit alongside every other field at the top level. `reactants`/`products`
 * reuse the same participant shape `reactionEquation.ts`'s
 * `EquationParticipantInput` already expects (`species_entry_ref`,
 * `species_entry_label`, `smiles`, `formula`, `stoichiometry`,
 * `participant_index`), so `ReactionBrowseRow` can feed them into
 * `ReactionEquation` with no reshaping.
 */
const reactionParticipantSchema = z.object({
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    smiles: z.string(),
    formula: z.string().nullable().optional(),
    stoichiometry: z.number(),
    participant_index: z.number(),
}).passthrough()

const reactionAvailabilitySchema = z.object({
    has_kinetics: z.boolean(),
    has_transition_state: z.boolean(),
    has_path_search: z.boolean().optional(),
    has_atom_map: z.boolean().optional(),
    kinetics_count: z.number().optional(),
}).passthrough()

export const reactionBrowseRecordSchema = z.object({
    reaction_ref: z.string(),
    reaction_entry_ref: z.string(),
    equation: z.string().nullable().optional(),
    // Review follow-up (round 2): served on every row (measured live,
    // present even with no smiles filter applied, where it is always
    // "forward") but previously dropped on the floor. "reverse" means the
    // participant a reactant/product SMILES search matched sits on the
    // OPPOSITE side from where the query named it -- e.g. `product_smiles=O`
    // matching `rxe_ed66mj3ohtyien5rm2x3sb3rdu` ("O + [CH3] <=> C + [OH]",
    // water on the REACTANT side) because the reaction is reversible and
    // the search considered both directions. Optional/nullable so an
    // older or pre-deployment response that never served this field parses
    // without claiming a direction the archive never asserted -- see
    // `ReactionBrowseRow.tsx`'s own rendering rule for the absent-vs-null
    // distinction this preserves.
    matched_direction: z.string().nullable().optional(),
    reversible: z.boolean(),
    family: z.string().nullable().optional(),
    review: recordReviewSchema,
    reactants: z.array(reactionParticipantSchema),
    products: z.array(reactionParticipantSchema),
    availability: reactionAvailabilitySchema,
}).passthrough()
export type ReactionBrowseRecord = z.infer<typeof reactionBrowseRecordSchema>

const reactionBrowseResponseSchema = z.object({
    records: z.array(reactionBrowseRecordSchema),
    pagination: paginationSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// Loaders
// ---------------------------------------------------------------------------

export type BrowseResult =
    | { kind: "species" | "vdw"; records: SpeciesBrowseRecord[]; pagination: BrowsePagination }
    | { kind: "transition_state"; records: TransitionStateBrowseRecord[]; pagination: BrowsePagination }
    | { kind: "reaction"; records: ReactionBrowseRecord[]; pagination: BrowsePagination }

export async function loadBrowse(
    kind: BrowseKind, filters: BrowseFilters, offset: number, limit: number, signal?: AbortSignal,
): Promise<BrowseResult> {
    if (kind === "transition_state") {
        const query = buildTransitionStateBrowseQuery(filters, offset, limit)
        const payload = await requestScientificJson(`/api/v1/scientific/transition-states/browse?${query}`, signal)
        const parsed = parseScientificResponse(transitionStateBrowseResponseSchema, payload, "transition state browse")
        return { kind, records: parsed.records, pagination: parsed.pagination }
    }
    if (kind === "reaction") {
        const query = buildReactionBrowseQuery(filters, offset, limit)
        const payload = await requestScientificJson(`/api/v1/scientific/reactions/browse?${query}`, signal)
        const parsed = parseScientificResponse(reactionBrowseResponseSchema, payload, "reaction browse")
        return { kind, records: parsed.records, pagination: parsed.pagination }
    }
    const query = buildSpeciesBrowseQuery(kind, filters, offset, limit)
    const payload = await requestScientificJson(`/api/v1/scientific/species/browse?${query}`, signal)
    const parsed = parseScientificResponse(speciesBrowseResponseSchema, payload, "species browse")
    return { kind, records: parsed.records, pagination: parsed.pagination }
}
