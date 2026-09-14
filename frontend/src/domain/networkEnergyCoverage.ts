/**
 * Pure computation behind the network entry page's "Energy coverage" card
 * (`docs/plans/pressure-dependent-network-surface.md` §2.3/§3.2, PR 2 of
 * that plan). The plan's own §1 measurement found "0 of 7 states have
 * relative energies deposited" when joined against species-level thermo --
 * but that number is a fact about ONE archive snapshot, not a constant.
 * Every count this module produces is derived from whatever payload the
 * caller hands it; nothing here is a literal figure. A regression that
 * hardcodes a coverage number instead of calling these functions is exactly
 * what `networkEnergyCoverage.test.ts` exists to catch.
 *
 * Species-level thermo coverage is answered by joining each network state's
 * participant `species_entry_ref`s (served on `NetworkStateSummary.
 * composition.participants[]`) against `GET
 * /scientific/species-entries/{ref}/thermo` for each unique ref -- see
 * `api/networkEntryApi.ts`'s `loadNetworkEntry`, which does that joining and
 * hands this module only the already-resolved presence map. Solve-level
 * state-energy / channel-barrier coverage needs no join at all: those counts
 * are already scoped to composition_hash / channel_key on the served
 * `network-solves/{ref}` payload, so `coverageFraction` below is the whole
 * of that half.
 */

/** `species_entry_ref` -> whether at least one thermo record was found for it. */
export type SpeciesThermoPresence = Record<string, boolean>

export type SpeciesEnergyCoverage = {
    /** Count of unique participant species entries that carry a thermo (H298) record. */
    withThermo: number
    /** Count of unique participant species entries referenced by this network's states. */
    total: number
}

/**
 * Live species-level thermo coverage across a network's states, computed
 * from the caller-supplied presence map -- never a hardcoded count. `total`
 * is the number of DISTINCT species entry refs passed in (a species entry
 * referenced by more than one state is counted once, matching the plan's
 * own "9 unique species entries" framing in §1).
 */
export function computeSpeciesEnergyCoverage(
    speciesEntryRefs: readonly string[],
    thermoPresence: SpeciesThermoPresence,
): SpeciesEnergyCoverage {
    const unique = Array.from(new Set(speciesEntryRefs))
    const withThermo = unique.filter((ref) => thermoPresence[ref] === true).length
    return { withThermo, total: unique.length }
}

/**
 * Generic "N of M" coverage fraction for the solve-level facts (state
 * energies out of states; channel barriers out of channels) -- both are
 * plain array-length comparisons against served data, but routed through
 * one named function rather than an inline `.length` at each call site so
 * the "always live, never literal" invariant reads the same way at every
 * use.
 */
export function coverageFraction(coveredCount: number, totalCount: number): { covered: number; total: number } {
    return { covered: coveredCount, total: totalCount }
}
