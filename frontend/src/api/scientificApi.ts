import { z } from "zod"
import type { EquationParticipantInput } from "../domain/reactionEquation"
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
// Reaction participation ("Reactions involving …", second group under a
// structure-query result -- see IdentifierSearch.tsx)
// ---------------------------------------------------------------------------

// This module owns its own response schema end to end (same convention
// `api/browseApi.ts`'s `reactionBrowseRecordSchema` documents for itself)
// rather than importing that sibling module's schema -- deliberately, to
// keep the two read surfaces decoupled; this endpoint call only needs a
// small slice of the same server shape.
const reactionParticipationParticipantSchema = z.object({
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    smiles: z.string(),
    formula: z.string().nullable().optional(),
    stoichiometry: z.number(),
    participant_index: z.number(),
}).passthrough()

const reactionParticipationRecordSchema = z.object({
    reaction_ref: z.string(),
    reaction_entry_ref: z.string(),
    reversible: z.boolean(),
    reactants: z.array(reactionParticipationParticipantSchema),
    products: z.array(reactionParticipationParticipantSchema),
}).passthrough()

const reactionParticipationResponseSchema = z.object({
    records: z.array(reactionParticipationRecordSchema),
    pagination: z.object({ total: z.number() }).passthrough(),
}).passthrough()

export type ReactionParticipationMatch = {
    reactionRef: string
    reactionEntryRef: string
    reversible: boolean
    reactants: EquationParticipantInput[]
    products: EquationParticipantInput[]
}

export type ReactionParticipationResult = {
    matches: ReactionParticipationMatch[]
    /** Server-reported total across every resolved species SMILES -- can exceed `matches.length` (only the first page is fetched per SMILES); see `IdentifierSearch`'s "See all N reactions" link-through. */
    total: number
}

/** Rows requested per distinct species SMILES -- kept small since this is a secondary group under the primary species results, not the archive's own browse page (which the "See all" link hands off to for the rest). */
const REACTION_PARTICIPATION_LIMIT = 5

/**
 * Reactions where any of `smilesValues` participates, on EITHER side
 * (`GET /scientific/reactions/browse?reactant_smiles=…&direction=either`).
 * One call per distinct SMILES value, not one call with every value as a
 * multi-value `reactant_smiles` list -- the browse route matches a
 * multi-value list as ONE group (AND) against a single stored side, which
 * is the wrong semantics for "does species A participate, OR does species
 * B" (an exact-structure search can occasionally resolve to more than one
 * species, e.g. distinct stereo entries). Results are merged and
 * de-duplicated by `reaction_entry_ref`.
 *
 * **`direction=either` is sent explicitly (PR #418 follow-up, 2026-09).**
 * The browse route used to run the equivalent of `direction=either`
 * unconditionally, so naming the param `reactant_smiles` still matched a
 * species that only ever appears as a product -- this function's own doc
 * comment said so. #418 changed the route's OWN default to `forward`
 * (`reactant_smiles` matches only the stored reactant side). This
 * function's job is unchanged by that: "reactions involving this species"
 * means either side, the way a reader means the question, so it must keep
 * asking for either-direction matching -- now as an explicit `direction`
 * param rather than getting it for free from the endpoint's old default.
 * Letting the endpoint's new default silently narrow this call to
 * forward-only matches would understate "reactions involving X" by
 * exactly the species that appear only as a product.
 *
 * The backend match is a literal string comparison against the stored
 * SMILES (not RDKit-canonicalized) -- callers should pass the ARCHIVE'S
 * OWN canonical SMILES for the matched species (as returned by
 * `searchSpeciesExact`), not the raw user-typed spelling, so a
 * non-canonical query spelling that the structure-search endpoint still
 * resolved does not silently miss every reaction.
 */
export async function searchReactionParticipation(
    smilesValues: string[],
    signal?: AbortSignal,
): Promise<ReactionParticipationResult> {
    const unique = [...new Set(smilesValues.map((value) => value.trim()).filter((value) => value !== ""))]
    if (unique.length === 0) return { matches: [], total: 0 }

    const perSmiles = await Promise.all(unique.map(async (smiles) => {
        const query = new URLSearchParams({
            reactant_smiles: smiles, direction: "either", limit: String(REACTION_PARTICIPATION_LIMIT),
        })
        return parseScientificResponse(
            reactionParticipationResponseSchema,
            await requestScientificJson(`/api/v1/scientific/reactions/browse?${query}`, signal),
            "reaction participation",
        )
    }))

    const byEntryRef = new Map<string, ReactionParticipationMatch>()
    let total = 0
    for (const parsed of perSmiles) {
        total += parsed.pagination.total
        for (const record of parsed.records) {
            if (byEntryRef.has(record.reaction_entry_ref)) continue
            byEntryRef.set(record.reaction_entry_ref, {
                reactionRef: record.reaction_ref,
                reactionEntryRef: record.reaction_entry_ref,
                reversible: record.reversible,
                reactants: record.reactants,
                products: record.products,
            })
        }
    }
    return { matches: [...byEntryRef.values()].slice(0, REACTION_PARTICIPATION_LIMIT), total }
}
