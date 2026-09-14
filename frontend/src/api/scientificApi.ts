import { z } from "zod"
import type { EquationParticipantInput } from "../domain/reactionEquation"
import { buildReactionBrowseQuery, EMPTY_BROWSE_FILTERS, reactionBrowseRecordSchema } from "./browseApi"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
export { ScientificApiError, ScientificRateLimitError } from "./scientificTransport"

const entrySchema = z.object({
    species_entry_ref: z.string(),
}).passthrough()

const speciesRecordSchema = z.object({
    species_ref: z.string(),
    // Nullable, not just optional: computed by the archive (#251) and can
    // come back `null` for a real record. `canonical_smiles`/`charge`/
    // `multiplicity` are core identity fields the live API always sends,
    // but are still read defensively since a search-results row must
    // survive a fixture or a future response that omits them, not throw.
    formula: z.string().nullable().optional(),
    canonical_smiles: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
    entries: z.array(entrySchema),
}).passthrough()

const speciesSearchSchema = z.object({
    records: z.array(speciesRecordSchema),
}).passthrough()

const structureRecordSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    // The structure-search endpoint never returns `formula` at all -- it
    // answers with the matched structure, not the species projection --
    // so `formula` is intentionally absent from this schema. See
    // `SearchMatch.formula` below.
    smiles: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
}).passthrough()

const structureSearchSchema = z.object({
    records: z.array(structureRecordSchema),
}).passthrough()

export type SearchMatch = {
    speciesRef: string
    entryRef?: string
    /**
     * `null` when the archive computed no formula for this record (#251);
     * `undefined` only for a structure-search match, whose endpoint never
     * projects a formula at all. Both render the same honest fallback --
     * see `IdentifierSearch`'s `MatchHeadline`.
     */
    formula?: string | null
    smiles?: string | null
    charge?: number | null
    multiplicity?: number | null
    /** Present only for species-grain matches (formula/ref search); a structure-search match is already one entry. */
    entryCount?: number
}
export type IdentifierSearch =
    | { kind: "formula"; value: string }
    | { kind: "species-ref"; value: string }
    | { kind: "species-entry-ref"; value: string }
    /**
     * A recognised public reference (`rxn_`/`rxe_`/`tse_`/`cg_`/`co_`/`calc_`/
     * `geom_`, per `classifyIdentifier`'s `ROUTED_REF_PREFIXES`) whose route
     * this frontend already serves. Unlike `species-ref`/`species-entry-ref`,
     * there is no verifying search call first -- `path` is navigated to
     * directly, same as clicking a stale/bookmarked link, and the
     * destination page's own `RecordStatus` reports "not found" honestly if
     * the ref does not resolve (every routed detail page already handles
     * that state for an ordinary bad link).
     */
    | { kind: "record-ref"; value: string; path: string }
    | { kind: "smiles"; value: string }
    | { kind: "inchi"; value: string }
    | { kind: "inchi-key"; value: string }

export async function searchSpeciesExact(
    identifier: IdentifierSearch,
    signal?: AbortSignal,
): Promise<SearchMatch[]> {
    const query = new URLSearchParams({ limit: "50" })
    if (identifier.kind === "formula") query.set("formula", identifier.value)
    if (identifier.kind === "species-ref") query.set("species_ref", identifier.value)
    if (identifier.kind === "species-entry-ref") query.set("species_entry_ref", identifier.value)
    if (identifier.kind === "formula" || identifier.kind === "species-ref" || identifier.kind === "species-entry-ref") {
        const parsed = parseScientificResponse(
            speciesSearchSchema,
            await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal),
            "scientific search",
        )
        if (identifier.kind === "species-entry-ref") {
            return parsed.records.flatMap((record) => record.entries.map((entry) => ({
                speciesRef: record.species_ref,
                entryRef: entry.species_entry_ref,
                formula: record.formula ?? null,
                smiles: record.canonical_smiles ?? null,
                charge: record.charge ?? null,
                multiplicity: record.multiplicity ?? null,
                entryCount: record.entries.length,
            })))
        }
        return parsed.records.map((record) => ({
            speciesRef: record.species_ref,
            formula: record.formula ?? null,
            smiles: record.canonical_smiles ?? null,
            charge: record.charge ?? null,
            multiplicity: record.multiplicity ?? null,
            entryCount: record.entries.length,
        }))
    }

    const field = identifier.kind === "smiles" ? "query_smiles"
        : identifier.kind === "inchi" ? "query_inchi" : "query_inchi_key"
    query.set(field, identifier.value)
    query.set("mode", "exact")
    const parsed = parseScientificResponse(structureSearchSchema,
        await requestScientificJson(`/api/v1/scientific/species/structure-search?${query}`, signal),
        "scientific search",
    )
    // The structure-search endpoint never returns a formula (see the schema
    // comment above): `null` here is a known-absent value, not "not fetched".
    return parsed.records.map((record) => ({
        speciesRef: record.species_ref,
        entryRef: record.species_entry_ref,
        formula: null,
        smiles: record.smiles ?? null,
        charge: record.charge ?? null,
        multiplicity: record.multiplicity ?? null,
    }))
}

// ---------------------------------------------------------------------------
// Reaction search (reaction mode on the archive home page -- see
// `IdentifierSearch.tsx` / `domain/reactionQuery.ts`): a bare structure (or
// comma-list of structures), a full equation, or a species cross-link,
// all resolve to ONE call against the same browse endpoint the reaction
// catalogue itself reads.
// ---------------------------------------------------------------------------

// One schema for the whole read surface: `reactionBrowseRecordSchema`
// (`api/browseApi.ts`) already describes exactly this endpoint's row shape
// (this call and `BrowsePage`'s own reaction listing are the SAME route,
// `GET /scientific/reactions/browse`, just with different query params) --
// importing it here keeps the two callers from ever describing one server
// shape two different ways. Pagination only needs `total` for this read.
const reactionSearchResponseSchema = z.object({
    records: z.array(reactionBrowseRecordSchema),
    pagination: z.object({ total: z.number() }).passthrough(),
}).passthrough()

export type ReactionParticipationMatch = {
    reactionRef: string
    reactionEntryRef: string
    reversible: boolean
    reactants: EquationParticipantInput[]
    products: EquationParticipantInput[]
    /**
     * `"reverse"` when THIS record matched the search's participants on
     * the opposite stored side from how the request named them (identical
     * meaning and identical source field, `matched_direction`, as
     * `ReactionBrowseRecord`'s own -- see `ReactionBrowseRow.tsx`'s doc
     * comment). `"forward"` or an absent/null value both mean "matched as
     * named, nothing to caveat" and render nothing, the same absent-vs-
     * asserted rule that component follows. Every search here now runs
     * `direction=either` regardless of which arrow was typed (or none), so
     * THIS field -- not the arrow, not a second query mode -- is how a
     * reader learns a given row only matched because the archive also
     * checked the reverse orientation.
     */
    matchedDirection: string | null
}

export type ReactionSearchResult = {
    matches: ReactionParticipationMatch[]
    /** Server-reported total, which can exceed `matches.length` -- only the first page is fetched here; see `IdentifierSearch`'s "See all N reactions" link-through. */
    total: number
}

/** Rows requested from the search -- kept small since this is a landing-page preview, not the archive's own browse page (which the "See all" link hands off to for the rest). */
export const REACTION_SEARCH_LIMIT = 5

/**
 * One request onto `GET /scientific/reactions/browse`, built with
 * `buildReactionBrowseQuery` (`api/browseApi.ts`) from an explicit
 * `reactants`/`products` pair -- the exact same query-building code path
 * `BrowsePage`'s own reaction filters use, so a home-page reaction search
 * can never drift from what the browse page's "See all" link-through
 * actually runs.
 *
 * **Always `direction=either`, never derived from the arrow (owner
 * correction).** An earlier version of this call site branched on which
 * arrow `classifyReactionQuery` saw -- a hidden mode a reader typing `<>`
 * had no way to discover `->` even existed, let alone that it meant
 * something narrower. Every search now asks the archive to check BOTH
 * stored sides regardless of syntax, and each returned record's own
 * `matched_direction` (mapped through to `ReactionParticipationMatch.
 * matchedDirection`) says whether THAT row matched as written or in
 * reverse -- the reader gets the full set, labelled, rather than a
 * silently narrower one they would have had to already know to widen.
 *
 * The backend match is a literal string comparison against the stored
 * SMILES per participant (not RDKit-canonicalized) and, for `reactants`/
 * `products` with more than one entry, an ALL-of-these-together (AND)
 * match on that one side -- both measured live against
 * `/scientific/reactions/browse` (`?reactant_smiles=NN&direction=either`
 * -> 20, `?reactant_smiles=NN&reactant_smiles=[H]&direction=either` -> 0).
 * This function does not resolve the reader's typed spelling to the
 * archive's own canonical form first (contrast the OLD
 * `searchReactionParticipation`, which did, via a species structure-
 * search) -- reaction mode is meant to be typed straight from the
 * equation a kineticist already has in hand and mapped directly onto the
 * browse filter a reader could type into that page too, not silently
 * re-interpreted through a second RDKit-backed lookup first.
 */
export async function searchReactionEquation(
    { reactants, products }: { reactants: string[]; products: string[] },
    signal?: AbortSignal,
): Promise<ReactionSearchResult> {
    const query = buildReactionBrowseQuery(
        { ...EMPTY_BROWSE_FILTERS, reactantSmiles: reactants.join(","), productSmiles: products.join(","), direction: "either" },
        0,
        REACTION_SEARCH_LIMIT,
    )
    const parsed = parseScientificResponse(
        reactionSearchResponseSchema,
        await requestScientificJson(`/api/v1/scientific/reactions/browse?${query}`, signal),
        "reaction search",
    )
    return {
        matches: parsed.records.map((record) => ({
            reactionRef: record.reaction_ref,
            reactionEntryRef: record.reaction_entry_ref,
            reversible: record.reversible,
            reactants: record.reactants,
            products: record.products,
            matchedDirection: record.matched_direction ?? null,
        })),
        total: parsed.pagination.total,
    }
}
